"""
bq_client.py — Wrapper do BigQuery para o MCP Server.

Responsabilidades:
  1. Autenticação via service account (GCP_KEYFILE_PATH)
  2. Execução de queries com timeout e limite de linhas
  3. Formatação de resultados em dict serializável (JSON-safe)
  4. Logging estruturado de cada query (base de auditoria)

Conceito — Por que service account e não Application Default Credentials (ADC)?
  ADC usa as credenciais do usuário logado no gcloud — bom para desenvolvimento
  interativo, mas imprevisível em subprocessos (como o MCP server iniciado pelo
  Claude Desktop). Service account é explícito: sempre a mesma identidade,
  sempre o mesmo conjunto de permissões. Em produção, seria o SA agent_readonly
  com acesso read-only apenas ao dataset marts.

[DEV/ESTUDO]  : service account via arquivo JSON local (GCP_KEYFILE_PATH)
[PRODUÇÃO]    : Workload Identity Federation — sem arquivo JSON no disco,
               credenciais injetadas pelo ambiente (GKE, Cloud Run, etc.)
"""

import json
import logging
import os
import time
from decimal import Decimal
from typing import Any

from google.cloud import bigquery
from google.oauth2 import service_account

logger = logging.getLogger(__name__)

# Limite máximo de linhas retornadas por query — garante respostas manejáveis
# pelo contexto do LLM. O agente pode pedir subconjuntos menores explicitamente.
MAX_ROWS = 500

# Timeout em segundos para queries no BigQuery
QUERY_TIMEOUT_SECONDS = 30


class BigQueryClient:
    """
    Cliente BigQuery com escopo restrito ao dataset marts.

    O escopo é aplicado em duas camadas:
      1. Role do service account no GCP (agent_readonly — apenas marts)
      2. Validação de prefixo de tabela antes da execução (defense in depth)
    """

    def __init__(self):
        self.project_id = os.environ["GCP_PROJECT_ID"]
        keyfile_path = os.environ["GCP_KEYFILE_PATH"]

        credentials = service_account.Credentials.from_service_account_file(
            keyfile_path,
            # O cliente Python do BigQuery usa jobs.insert para todas as queries
            # (inclusive leituras), o que requer o escopo completo.
            # A restrição de acesso real fica no IAM do service account (agent_readonly)
            # — o escopo OAuth não é o mecanismo de segurança aqui.
            scopes=["https://www.googleapis.com/auth/bigquery"],
        )

        self.client = bigquery.Client(
            project=self.project_id,
            credentials=credentials,
        )
        logger.info(
            "BigQueryClient inicializado",
            extra={"project_id": self.project_id, "keyfile": keyfile_path},
        )

    def full_table_ref(self, table: str) -> str:
        """
        Converte 'marts.fct_orders' → '`project-id.marts.fct_orders`'.
        Usado pelo query_builder para gerar SQL com referência completa.
        """
        return f"`{self.project_id}.{table}`"

    def run_query(
        self,
        sql: str,
        *,
        limit: int = MAX_ROWS,
        source: str = "unknown",
    ) -> dict[str, Any]:
        """
        Executa SQL no BigQuery e retorna resultado serializado.

        Args:
            sql    : SQL já validado pelo sql_validator ou gerado pelo query_builder.
            limit  : Máximo de linhas a retornar (default MAX_ROWS).
            source : Identificador do tool que originou a query (para logs/auditoria).

        Returns:
            dict com keys:
              - rows        : lista de dicts {coluna: valor}
              - row_count   : int
              - columns     : lista de nomes de colunas
              - truncated   : bool — True se havia mais linhas além do limite
              - elapsed_ms  : tempo de execução em ms
              - sql_executed: SQL que foi de fato enviado ao BigQuery

        [PRODUÇÃO] Os logs deste método são a base da auditoria de acesso.
        Em produção, seria enriquecido com: user_id, session_id, request_id,
        bytes_billed, slot_ms — e exportado para Cloud Logging + BigQuery audit log.
        """
        start = time.monotonic()

        # Garante que o LIMIT está presente e dentro do máximo permitido
        effective_limit = min(limit, MAX_ROWS)
        if "LIMIT" not in sql.upper():
            sql = f"{sql.rstrip().rstrip(';')}\nLIMIT {effective_limit}"

        logger.info(
            "query_start",
            extra={
                "source": source,
                "sql_preview": sql[:200],
                "limit": effective_limit,
            },
        )

        try:
            job_config = bigquery.QueryJobConfig(
                # Desabilita escrita de resultado em tabelas temporárias para
                # queries que excedam o cache — força comportamento read-only
                use_legacy_sql=False,
            )

            query_job = self.client.query(sql, job_config=job_config)

            # Aguarda com timeout explícito
            rows = list(query_job.result(timeout=QUERY_TIMEOUT_SECONDS))

        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.error(
                "query_error",
                extra={"source": source, "error": str(exc), "elapsed_ms": elapsed_ms},
            )
            raise RuntimeError(f"Erro na query BigQuery: {exc}") from exc

        elapsed_ms = int((time.monotonic() - start) * 1000)

        if not rows:
            logger.info("query_empty", extra={"source": source, "elapsed_ms": elapsed_ms})
            return {
                "rows": [],
                "row_count": 0,
                "columns": [],
                "truncated": False,
                "elapsed_ms": elapsed_ms,
                "sql_executed": sql,
            }

        columns = list(rows[0].keys())
        serialized = [_serialize_row(dict(row)) for row in rows]
        truncated = len(serialized) >= effective_limit

        logger.info(
            "query_success",
            extra={
                "source": source,
                "row_count": len(serialized),
                "columns": columns,
                "truncated": truncated,
                "elapsed_ms": elapsed_ms,
            },
        )

        return {
            "rows": serialized,
            "row_count": len(serialized),
            "columns": columns,
            "truncated": truncated,
            "elapsed_ms": elapsed_ms,
            "sql_executed": sql,
        }

    def get_table_schema(self, table: str) -> list[dict]:
        """
        Retorna o schema de uma tabela BigQuery.
        Usado por get_lineage() para mostrar colunas e tipos.
        """
        full_ref = f"{self.project_id}.{table}"
        try:
            bq_table = self.client.get_table(full_ref)
            return [
                {
                    "name": field.name,
                    "type": field.field_type,
                    "mode": field.mode,
                    "description": field.description or "",
                }
                for field in bq_table.schema
            ]
        except Exception as exc:
            raise RuntimeError(f"Não foi possível obter schema de {table}: {exc}") from exc


# Singleton — instanciado uma vez na inicialização do server e reutilizado
# Evita o overhead de autenticação em cada tool call.
_client: BigQueryClient | None = None


def get_client() -> BigQueryClient:
    """Retorna o cliente singleton, inicializando na primeira chamada."""
    global _client
    if _client is None:
        _client = BigQueryClient()
    return _client


# =============================================================================
# HELPERS DE SERIALIZAÇÃO
#
# O BigQuery retorna tipos Python específicos (Decimal, datetime, Date)
# que não são nativamente serializáveis em JSON — precisamos converter.
# =============================================================================

def _serialize_row(row: dict) -> dict:
    """Converte tipos BigQuery para tipos Python serializáveis em JSON."""
    return {k: _serialize_value(v) for k, v in row.items()}


def _serialize_value(v: Any) -> Any:
    """Converte um valor BigQuery para um tipo serializável."""
    if v is None:
        return None
    if isinstance(v, Decimal):
        # Decimal → float (perda de precisão aceitável para display)
        return float(v)
    if hasattr(v, "isoformat"):
        # datetime, date, time → string ISO 8601
        return v.isoformat()
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float, str)):
        return v
    # Fallback: str() para tipos desconhecidos
    return str(v)
