"""
tools.py — Definição e execução das tools para a Claude API.

Conceito — Tool use na Claude API:
  Diferente do MCP (protocolo de servidor), o tool use da Claude API funciona
  assim: você passa uma lista de definições de tools no body da chamada. O modelo
  lê as descriptions e decide qual chamar. Quando decide, retorna um bloco
  `tool_use` com o nome e os argumentos. Você executa a tool e devolve o
  resultado como `tool_result`. O modelo então continua.

  Formato de definição de tool:
  {
    "name": "nome_da_tool",
    "description": "descrição que o modelo lê para decidir quando usar",
    "input_schema": { JSON Schema dos parâmetros }
  }

  A description é o campo mais importante — quanto mais clara e específica,
  melhor o modelo decide quando e como usar a tool.

Conceito — Reutilização do código do mcp_server:
  O executor de tools aqui importa diretamente o código do mcp_server
  (catalog.py, bq_client.py, query_builder.py, sql_validator.py).
  Isso elimina duplicação — a lógica de negócio fica em um lugar só.
  O MCP server continua servindo o Claude Desktop via stdio.
  O agent_api usa o mesmo código via import Python direto.

  [PRODUÇÃO] O agent_api chamaria o MCP server via HTTP (transporte SSE),
  tratando-o como um serviço independente. Para estudo, import direto é mais
  simples e igualmente correto conceitualmente.
"""

import json
import logging
import sys
from pathlib import Path
from typing import Any

# Adiciona mcp_server ao path para import direto
sys.path.insert(0, str(Path(__file__).parent.parent / "mcp_server"))

from catalog import format_catalog_for_agent, get_metric, METRICS
from bq_client import get_client
from query_builder import build_metric_query
from sql_validator import validate_sql

logger = logging.getLogger(__name__)

# =============================================================================
# DEFINIÇÕES DE TOOLS PARA A CLAUDE API
#
# Estas definições são passadas no parâmetro `tools` da chamada à Claude API.
# O modelo lê as descriptions para decidir qual tool chamar.
# Os input_schema definem os parâmetros aceitos — o modelo os preenche.
# =============================================================================

TOOL_DEFINITIONS: list[dict] = [

    # ── 1. list_metrics ───────────────────────────────────────────────────────
    {
        "name": "list_metrics",
        "description": (
            "Lista todas as métricas disponíveis com descrições, unidades, "
            "dimensões disponíveis e armadilhas de uso. "
            "Use ANTES de query_metric quando não tiver certeza do nome exato "
            "da métrica ou para entender diferenças entre métricas similares "
            "(ex: delivered_revenue vs gross_revenue vs net_revenue). "
            "Retorna catálogo completo de 8 métricas de e-commerce."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },

    # ── 2. query_metric ───────────────────────────────────────────────────────
    {
        "name": "query_metric",
        "description": (
            "Consulta uma métrica de negócio pré-definida no warehouse BigQuery. "
            "ESTE É O CAMINHO PREFERENCIAL — use antes de run_sql_readonly. "
            "Métricas disponíveis: delivered_revenue (receita canônica), "
            "gross_revenue (inclui em trânsito), net_revenue (após devoluções), "
            "returned_revenue (devoluções), delivered_orders (contagem), "
            "avg_order_value (ticket médio), return_rate (%), active_customers_30d. "
            "Use list_metrics() para descrições completas e dimensões disponíveis."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {
                    "type": "string",
                    "description": "Nome da métrica (ex: 'delivered_revenue').",
                },
                "dimensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Colunas para GROUP BY (ex: ['channel', 'customer_state']). "
                        "Dimensões disponíveis variam por métrica — use list_metrics() para ver."
                    ),
                },
                "period": {
                    "type": "string",
                    "description": (
                        "Período de análise: last_7d | last_30d | last_90d | last_180d | last_365d | "
                        "YYYY-MM (mês) | YYYY-QN (quarter) | YYYY (ano) | omitir = toda a história."
                    ),
                },
                "filters": {
                    "type": "object",
                    "description": "Filtros adicionais como dict (ex: {'channel': 'app_ios'}).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Máximo de linhas (default 500).",
                    "default": 500,
                },
            },
            "required": ["metric"],
        },
    },

    # ── 3. run_sql_readonly ───────────────────────────────────────────────────
    {
        "name": "run_sql_readonly",
        "description": (
            "Executa SQL read-only no BigQuery após validação de segurança. "
            "Use APENAS quando query_metric não cobrir a pergunta. "
            "Restrições: apenas SELECT/CTE/UNION, só schema 'marts', max 1000 linhas. "
            "INSERT/UPDATE/DELETE/DROP são SEMPRE bloqueados. "
            "Tabelas disponíveis: marts.fct_orders, marts.dim_customers, "
            "marts.dim_customers_kpis, marts.dim_products, marts.agg_daily_revenue."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "Query SQL (apenas SELECT/CTE/UNION, schema marts obrigatório).",
                },
                "reason": {
                    "type": "string",
                    "description": "Explique por que query_metric não foi suficiente.",
                },
            },
            "required": ["sql"],
        },
    },

    # ── 4. get_lineage ────────────────────────────────────────────────────────
    {
        "name": "get_lineage",
        "description": (
            "Retorna a linhagem de dados de um modelo dbt — origem, transformações "
            "e colunas disponíveis. Use para entender de onde vem um número ou "
            "verificar se uma coluna existe antes de escrever SQL. "
            "Modelos: fct_orders | dim_customers | dim_customers_kpis | "
            "dim_products | agg_daily_revenue."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Nome do modelo dbt (ex: 'fct_orders').",
                },
            },
            "required": ["model"],
        },
    },
]


# =============================================================================
# EXECUTOR DE TOOLS
#
# Mapeia o nome da tool (retornado pelo modelo) para a função Python
# que a executa. Retorna string que vai de volta para o modelo como tool_result.
# =============================================================================

class ToolExecutor:
    """
    Executa tools pelo nome e registra os resultados para auditoria.

    O registro (tool_calls_log) é usado para construir a trilha de auditoria
    da AskResponse — cada chamada fica documentada com parâmetros e SQL.
    """

    def __init__(self):
        self.tool_calls_log: list[dict] = []

    def execute(self, tool_name: str, tool_input: dict) -> str:
        """
        Executa uma tool e retorna o resultado como string.
        O modelo recebe essa string como conteúdo do tool_result.
        """
        logger.info("tool_execute", extra={"tool": tool_name, "input": tool_input})

        try:
            if tool_name == "list_metrics":
                return self._list_metrics(tool_input)
            elif tool_name == "query_metric":
                return self._query_metric(tool_input)
            elif tool_name == "run_sql_readonly":
                return self._run_sql_readonly(tool_input)
            elif tool_name == "get_lineage":
                return self._get_lineage(tool_input)
            else:
                return f"Tool '{tool_name}' não reconhecida."
        except Exception as exc:
            logger.error("tool_error", extra={"tool": tool_name, "error": str(exc)})
            return f"Erro ao executar tool '{tool_name}': {exc}"

    def _list_metrics(self, _: dict) -> str:
        result = format_catalog_for_agent()
        self.tool_calls_log.append({
            "tool_name": "list_metrics",
            "parameters": {},
            "sql_executed": None,
            "row_count": None,
            "elapsed_ms": None,
        })
        return result

    def _query_metric(self, tool_input: dict) -> str:
        import os
        metric_name = tool_input.get("metric")
        dimensions  = tool_input.get("dimensions")
        period      = tool_input.get("period")
        filters     = tool_input.get("filters")
        limit       = tool_input.get("limit", 500)

        metric_def = get_metric(metric_name)
        if metric_def is None:
            return f"Métrica '{metric_name}' não encontrada. Use list_metrics() para ver as disponíveis."

        project_id = os.environ["GCP_PROJECT_ID"]
        try:
            sql = build_metric_query(
                metric_def=metric_def,
                project_id=project_id,
                dimensions=dimensions,
                period=period,
                filters=filters,
                limit=min(limit or 500, 500),
            )
        except ValueError as exc:
            return f"Parâmetros inválidos: {exc}"

        client = get_client()
        result = client.run_query(sql, source="agent_api.query_metric")

        self.tool_calls_log.append({
            "tool_name": "query_metric",
            "parameters": {
                "metric": metric_name,
                "dimensions": dimensions,
                "period": period,
                "filters": filters,
            },
            "sql_executed": result["sql_executed"],
            "row_count": result["row_count"],
            "elapsed_ms": result["elapsed_ms"],
        })

        # Formata resultado para o modelo processar
        if result["row_count"] == 0:
            return (
                f"Nenhum resultado para {metric_name} com os filtros aplicados. "
                f"SQL executado: {sql}"
            )

        rows_text = json.dumps(result["rows"][:50], ensure_ascii=False, indent=2)
        return (
            f"Resultado de {metric_def.label}:\n"
            f"Linhas retornadas: {result['row_count']} | "
            f"Tempo: {result['elapsed_ms']}ms\n"
            f"SQL executado:\n```sql\n{result['sql_executed']}\n```\n"
            f"Dados:\n{rows_text}"
            + ("\n[TRUNCADO — mais linhas disponíveis]" if result["truncated"] else "")
        )

    def _run_sql_readonly(self, tool_input: dict) -> str:
        sql    = tool_input.get("sql", "")
        reason = tool_input.get("reason", "")

        validation = validate_sql(sql)
        if not validation.valid:
            return f"SQL bloqueado: {validation.error}"

        client = get_client()
        result = client.run_query(validation.normalized_sql, source="agent_api.run_sql_readonly")

        self.tool_calls_log.append({
            "tool_name": "run_sql_readonly",
            "parameters": {"reason": reason},
            "sql_executed": result["sql_executed"],
            "row_count": result["row_count"],
            "elapsed_ms": result["elapsed_ms"],
        })

        rows_text = json.dumps(result["rows"][:50], ensure_ascii=False, indent=2)
        return (
            f"Resultado SQL:\n"
            f"Linhas: {result['row_count']} | Tempo: {result['elapsed_ms']}ms\n"
            f"SQL executado:\n```sql\n{result['sql_executed']}\n```\n"
            f"Dados:\n{rows_text}"
        )

    def _get_lineage(self, tool_input: dict) -> str:
        # Linhagem estática — mesma lógica do mcp_server/server.py
        model = tool_input.get("model", "")
        lineage_map = {
            "fct_orders": "Origem: seed CSV → staging (dedup, cast, normalize) → marts (joins, receita calculada). Grão: 1 linha por order_id.",
            "dim_customers": "Origem: agregação de fct_orders por customer_id. Grão: 1 linha por cliente.",
            "dim_customers_kpis": "Origem: fct_orders + dim_customers. KPIs RFM, janelas 30/60/90d, segmentação. Grão: 1 linha por cliente (snapshot diário).",
            "dim_products": "Origem: agregação de stg_ecommerce__orders por product_name. Grão: 1 linha por produto.",
            "agg_daily_revenue": "Origem: agregação de fct_orders por (created_date, channel). Grão: 1 linha por dia+canal.",
        }
        self.tool_calls_log.append({
            "tool_name": "get_lineage",
            "parameters": {"model": model},
            "sql_executed": None,
            "row_count": None,
            "elapsed_ms": None,
        })
        return lineage_map.get(model, f"Modelo '{model}' não encontrado.")
