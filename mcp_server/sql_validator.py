"""
sql_validator.py — Validação de SQL antes da execução no BigQuery.

Usado exclusivamente pelo tool run_sql_readonly() — o escape hatch que permite
ao agente escrever SQL livre quando nenhuma métrica pré-definida atende.

Conceito — Por que sqlglot?
  sqlglot é um parser SQL que transforma texto SQL em uma AST (Abstract Syntax Tree)
  — a representação em árvore da estrutura da query. Em vez de tentar detectar
  DML com regex (frágil: 'DELETE' pode aparecer em aliases, strings, comentários),
  parseamos o SQL e inspecionamos o tipo do nó raiz.

  Exemplo de como a AST funciona:
    SQL: "SELECT order_id FROM marts.fct_orders WHERE status = 'entregue'"
    AST root: Select(
                expressions=[Column("order_id")],
                from_=From(Table("fct_orders", db="marts")),
                where=Where(EQ(Column("status"), Literal("entregue")))
              )
    → root é um Select → OK ✓

    SQL: "DELETE FROM marts.fct_orders WHERE 1=1"
    AST root: Delete(...)
    → root não é um Select → BLOQUEADO ✗

Conceito — Defense in depth (camada 1 de 2):
  Mesmo que um SQL DML passe pelo validator (ex: bug no sqlglot),
  a role agent_readonly no BigQuery vai rejeitar a execução.
  O validator é redundante por design — falha segura.

[PRODUÇÃO] Adicionar: allowlist de tabelas (só marts.*), limit máximo,
análise de custo estimado (bytes_processed) antes de executar.
"""

import logging
from dataclasses import dataclass

import sqlglot
import sqlglot.expressions as exp

logger = logging.getLogger(__name__)

# Statements SQL permitidos — apenas leitura
ALLOWED_STATEMENT_TYPES = (
    exp.Select,
    exp.With,      # CTEs: WITH ... AS (...) SELECT ...
    exp.Union,     # UNION / UNION ALL
    exp.Intersect,
    exp.Except,
)

# Statements explicitamente bloqueados (para mensagens de erro úteis).
# Usamos apenas tipos estáveis entre versões do sqlglot.
# exp.Truncate foi removido — o nome varia por versão (TruncateTable em algumas).
# Qualquer statement fora de ALLOWED_STATEMENT_TYPES é bloqueado pelo allowlist check abaixo.
BLOCKED_STATEMENTS: dict = {}
_BLOCKED_BY_NAME = {"Insert", "Update", "Delete", "Drop", "Create", "Alter", "Command", "Transaction"}

def _is_blocked(stmt) -> str | None:
    """Retorna o nome do statement se for bloqueado, None se for permitido."""
    class_name = type(stmt).__name__
    if class_name in _BLOCKED_BY_NAME:
        return class_name
    # Captura variantes como TruncateTable, DropTable, CreateTable, etc.
    for blocked in _BLOCKED_BY_NAME:
        if class_name.startswith(blocked):
            return class_name
    return None

# Tabelas permitidas — só marts (sem acesso a raw ou staging)
ALLOWED_SCHEMAS = {"marts"}

# Limite de linhas máximo que pode ser solicitado via run_sql_readonly
MAX_ALLOWED_LIMIT = 1000


@dataclass
class ValidationResult:
    """Resultado da validação de um SQL."""
    valid: bool
    error: str | None = None
    normalized_sql: str | None = None  # SQL normalizado com LIMIT injetado


def validate_sql(sql: str) -> ValidationResult:
    """
    Valida um SQL para execução segura no BigQuery.

    Verificações em ordem:
      1. Sintaxe parseável pelo sqlglot
      2. Statement é do tipo SELECT (ou CTE/UNION)
      3. Tabelas referenciadas pertencem ao schema marts
      4. LIMIT presente e dentro do máximo

    Retorna ValidationResult com valid=True e o SQL normalizado,
    ou valid=False com mensagem de erro legível para o agente.
    """
    sql = sql.strip().rstrip(";")

    # ── 1. PARSE ─────────────────────────────────────────────────────────────
    try:
        # dialect="bigquery" garante que funções específicas do BQ sejam reconhecidas
        # (DATE_SUB, SAFE_DIVIDE, COUNTIF, APPROX_QUANTILES, etc.)
        statements = sqlglot.parse(sql, dialect="bigquery")
    except Exception as exc:
        return ValidationResult(
            valid=False,
            error=f"SQL inválido — não foi possível parsear: {exc}",
        )

    if not statements or statements[0] is None:
        return ValidationResult(valid=False, error="SQL vazio ou não reconhecido.")

    if len(statements) > 1:
        return ValidationResult(
            valid=False,
            error=(
                f"Múltiplos statements detectados ({len(statements)}). "
                "Envie apenas 1 statement por vez."
            ),
        )

    stmt = statements[0]

    # ── 2. TIPO DE STATEMENT ──────────────────────────────────────────────────
    # Verifica blocklist por nome de classe (robusto entre versões do sqlglot)
    blocked_name = _is_blocked(stmt)
    if blocked_name:
        logger.warning(
            "sql_blocked",
            extra={"statement_type": blocked_name, "sql_preview": sql[:100]},
        )
        return ValidationResult(
            valid=False,
            error=(
                f"Statement '{blocked_name}' não é permitido. "
                "Apenas queries de leitura (SELECT, CTEs, UNION) são aceitas. "
                "Esta restrição existe por segurança — o agente não modifica dados."
            ),
        )

    if not isinstance(stmt, ALLOWED_STATEMENT_TYPES):
        return ValidationResult(
            valid=False,
            error=(
                f"Statement do tipo '{type(stmt).__name__}' não é permitido. "
                "Use SELECT, WITH (CTE) ou UNION."
            ),
        )

    # ── 3. SCHEMA DAS TABELAS ────────────────────────────────────────────────
    # Extrai todas as tabelas referenciadas no SQL
    tables = list(stmt.find_all(exp.Table))
    for table in tables:
        db = table.args.get("db")
        if db is None:
            # Tabela sem schema explícito — requer marts. prefix
            return ValidationResult(
                valid=False,
                error=(
                    f"Tabela '{table.name}' sem schema explícito. "
                    "Use sempre o schema completo: 'marts.nome_da_tabela'. "
                    "Exemplo: SELECT * FROM marts.fct_orders"
                ),
            )
        db_name = db.name if hasattr(db, "name") else str(db)
        if db_name not in ALLOWED_SCHEMAS:
            return ValidationResult(
                valid=False,
                error=(
                    f"Acesso ao schema '{db_name}' não permitido. "
                    f"Schemas permitidos: {sorted(ALLOWED_SCHEMAS)}. "
                    "O agente só pode consultar tabelas do schema marts."
                ),
            )

    # ── 4. LIMIT ─────────────────────────────────────────────────────────────
    limit_node = stmt.args.get("limit")

    if limit_node is None:
        # Injeta LIMIT para evitar queries sem bound que explodem o contexto do LLM
        normalized = f"{sql}\nLIMIT {MAX_ALLOWED_LIMIT}"
        logger.info("sql_limit_injected", extra={"limit": MAX_ALLOWED_LIMIT})
    else:
        # Valida se o LIMIT solicitado está dentro do permitido
        try:
            limit_value = int(limit_node.this.this)
            if limit_value > MAX_ALLOWED_LIMIT:
                normalized = sql.replace(
                    str(limit_value), str(MAX_ALLOWED_LIMIT), 1
                )
                logger.info(
                    "sql_limit_reduced",
                    extra={"original": limit_value, "reduced_to": MAX_ALLOWED_LIMIT},
                )
            else:
                normalized = sql
        except (AttributeError, ValueError):
            normalized = sql  # Não conseguiu parsear o valor — deixa passar para o BQ

    logger.info(
        "sql_validated",
        extra={
            "statement_type": type(stmt).__name__,
            "tables": [t.name for t in tables],
        },
    )

    return ValidationResult(valid=True, normalized_sql=normalized)
