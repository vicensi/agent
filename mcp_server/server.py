"""
server.py — MCP Server da Agentic Data Platform.

Conceito — FastMCP e decorators:
  @mcp.tool() transforma uma função Python em uma tool MCP.
  O FastMCP:
    1. Lê os type hints para gerar o JSON Schema dos parâmetros
    2. Usa o docstring da função como description da tool (o agente lê isso!)
    3. Registra o handler no protocolo MCP
    4. Serializa os retornos automaticamente

  O nome da função Python vira o nome da tool no protocolo.
  Então `def query_metric(...)` → tool chamada "query_metric" no Claude Desktop.

Conceito — Description da tool vs description da métrica:
  A description do @mcp.tool() (o docstring) é o que o agente lê para decidir
  QUANDO chamar a tool. A description das métricas no catalog.py é o que o agente
  lê DENTRO da tool list_metrics() para decidir QUAL métrica usar.
  São dois níveis de contexto diferentes — ambos importam para a precisão.

Conceito — Por que retornar texto em vez de JSON?
  O MCP suporta retornos tipados (text, image, resource), mas para tools que
  respondem a um LLM, texto estruturado (markdown) é mais útil do que JSON puro:
  o modelo já entende markdown e pode incorporar os dados na resposta final
  sem precisar de parsing adicional.

Inicialização:
  O server é iniciado com `mcp.run()` que abre o transporte stdio.
  O Claude Desktop detecta o server via `claude_desktop_config.json` e
  inicia o subprocesso automaticamente quando o usuário abre uma conversa.

[PRODUÇÃO] Adicionar: autenticação OAuth, rate limiting por tool,
health check endpoint, métricas de uso (Prometheus), deploy via Cloud Run.
"""

import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from tabulate import tabulate

# Adiciona o diretório do server ao path para imports relativos
sys.path.insert(0, str(Path(__file__).parent))

from bq_client import get_client
from catalog import format_catalog_for_agent, get_metric, list_metric_names, METRICS
from query_builder import build_metric_query, _resolve_period
from sql_validator import validate_sql

# ── LOGGING ──────────────────────────────────────────────────────────────────
# Log estruturado em JSON para facilitar análise posterior.
# [PRODUÇÃO] Exportar para Cloud Logging com structured logging.
logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "msg": "%(message)s"}',
    stream=sys.stderr,  # stderr para não poluir o stdio do protocolo MCP
)
logger = logging.getLogger(__name__)

# ── ENV VARS ─────────────────────────────────────────────────────────────────
# Carrega .env do diretório raiz do projeto (um nível acima de mcp_server/)
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

if not os.environ.get("GCP_PROJECT_ID"):
    logger.error("GCP_PROJECT_ID não configurado. Verifique o arquivo .env")
    sys.exit(1)

# ── FASTMCP APP ───────────────────────────────────────────────────────────────
mcp = FastMCP(
    name="ecommerce-data-platform",
    # Instructions são exibidas ao agente como contexto geral do server
    instructions=(
        "Você está conectado ao warehouse de e-commerce da Agentic Data Platform. "
        "O warehouse contém dados de pedidos, clientes, produtos e receita. "
        "SEMPRE use query_metric para métricas de negócio — é mais seguro que SQL livre. "
        "Use run_sql_readonly apenas para perguntas que não se encaixam em nenhuma métrica. "
        "NUNCA tente modificar dados — o servidor só aceita queries de leitura. "
        "Ao responder, SEMPRE cite qual tool foi chamada, quais parâmetros foram usados "
        "e o SQL executado — isso é a 'citação de fonte' que garante rastreabilidade."
    ),
)


# =============================================================================
# TOOL 1 — list_metrics
# =============================================================================

@mcp.tool()
def list_metrics() -> str:
    """
    Lista todas as métricas disponíveis com suas descrições completas, unidades,
    dimensões disponíveis e armadilhas de uso.

    Use esta tool ANTES de query_metric quando não tiver certeza do nome exato
    da métrica ou quando precisar entender as diferenças entre métricas similares
    (ex: delivered_revenue vs gross_revenue vs net_revenue).

    Retorna um catálogo em markdown com 8 métricas do e-commerce.
    """
    logger.info("tool_called", extra={"tool": "list_metrics"})
    return format_catalog_for_agent()


# =============================================================================
# TOOL 2 — query_metric  (CAMINHO PREFERENCIAL)
# =============================================================================

@mcp.tool()
def query_metric(
    metric: str,
    dimensions: list[str] | None = None,
    period: str | None = None,
    filters: dict | None = None,
    limit: int = 500,
) -> str:
    """
    Consulta uma métrica de negócio pré-definida no warehouse.
    ESTE É O CAMINHO PREFERENCIAL — use antes de run_sql_readonly.

    Métricas disponíveis (use list_metrics() para descrições completas):
      - delivered_revenue   : receita de pedidos entregues (métrica canônica)
      - gross_revenue       : receita incluindo pedidos em trânsito
      - net_revenue         : receita líquida após devoluções
      - returned_revenue    : valor total de devoluções
      - delivered_orders    : contagem de pedidos entregues
      - avg_order_value     : ticket médio (entregues)
      - return_rate         : taxa de devolução em %
      - active_customers_30d: clientes únicos com compra nos últimos 30 dias

    Args:
        metric     : Nome da métrica (ex: "delivered_revenue")
        dimensions : Colunas para agrupar (ex: ["channel", "customer_state"])
                     Dimensões disponíveis variam por métrica — use list_metrics() para ver
        period     : Período de análise. Opções:
                       "last_7d" | "last_30d" | "last_90d" | "last_180d" | "last_365d"
                       "2024-01" (mês) | "2024-Q1" (quarter) | "2024" (ano)
                       None = toda a história disponível
        filters    : Filtros adicionais como dict (ex: {"channel": "app_ios"})
        limit      : Máximo de linhas (default 500, max 500)

    Retorna resultado tabulado com o SQL executado (para citação de fonte).
    """
    logger.info(
        "tool_called",
        extra={
            "tool": "query_metric",
            "metric": metric,
            "dimensions": dimensions,
            "period": period,
            "filters": filters,
        },
    )

    # ── VALIDAÇÃO DA MÉTRICA ──────────────────────────────────────────────────
    metric_def = get_metric(metric)
    if metric_def is None:
        available = list_metric_names()
        return (
            f"❌ Métrica '{metric}' não encontrada.\n\n"
            f"Métricas disponíveis: {', '.join(available)}\n\n"
            "Use list_metrics() para ver descrições completas."
        )

    # ── GERAÇÃO DO SQL ────────────────────────────────────────────────────────
    project_id = os.environ["GCP_PROJECT_ID"]
    try:
        sql = build_metric_query(
            metric_def=metric_def,
            project_id=project_id,
            dimensions=dimensions,
            period=period,
            filters=filters,
            limit=min(limit, 500),
        )
    except ValueError as exc:
        return f"❌ Erro nos parâmetros: {exc}"

    # ── EXECUÇÃO ──────────────────────────────────────────────────────────────
    try:
        client = get_client()
        result = client.run_query(sql, source="query_metric")
    except RuntimeError as exc:
        return f"❌ Erro na execução: {exc}\n\n**SQL tentado:**\n```sql\n{sql}\n```"

    # ── FORMATAÇÃO DO RESULTADO ───────────────────────────────────────────────
    return _format_result(
        result=result,
        title=f"📊 {metric_def.label}",
        metadata={
            "Métrica": metric_def.name,
            "Dimensões": ", ".join(dimensions) if dimensions else "nenhuma (total geral)",
            "Período": period or "toda a história",
            "Filtros": json.dumps(filters, ensure_ascii=False) if filters else "nenhum",
            "Unidade": metric_def.unit,
        },
        caveats=metric_def.caveats,
    )


# =============================================================================
# TOOL 3 — run_sql_readonly  (ESCAPE HATCH)
# =============================================================================

@mcp.tool()
def run_sql_readonly(sql: str, reason: str = "") -> str:
    """
    Executa SQL read-only diretamente no BigQuery após validação de segurança.

    Use APENAS quando query_metric não cobrir a pergunta.
    Exemplos de uso legítimo:
      - Distribuição de review_score por categoria
      - Top 10 produtos por devolução
      - Cohort de clientes por mês de primeira compra

    Restrições de segurança (aplicadas pelo servidor, não por prompt):
      - Apenas SELECT, CTEs (WITH), UNION são permitidos
      - INSERT, UPDATE, DELETE, DROP, CREATE são SEMPRE bloqueados
      - Somente tabelas do schema 'marts' são acessíveis
      - Máximo de 1000 linhas por query
      - Timeout de 30 segundos

    Tabelas disponíveis no schema marts:
      - marts.fct_orders          : pedidos (1 linha por pedido)
      - marts.dim_customers       : dimensão de clientes
      - marts.dim_customers_kpis  : KPIs e segmentação RFM por cliente
      - marts.dim_products        : dimensão de produtos
      - marts.agg_daily_revenue   : receita agregada por dia e canal

    Args:
        sql    : Query SQL (apenas SELECT/CTE/UNION, schema marts obrigatório)
        reason : Explique por que query_metric não foi suficiente (para auditoria)
    """
    logger.info(
        "tool_called",
        extra={
            "tool": "run_sql_readonly",
            "reason": reason,
            "sql_preview": sql[:200],
        },
    )

    # ── VALIDAÇÃO ─────────────────────────────────────────────────────────────
    validation = validate_sql(sql)
    if not validation.valid:
        return (
            f"❌ SQL bloqueado pela validação de segurança:\n\n"
            f"**Motivo:** {validation.error}\n\n"
            "Reescreva o SQL respeitando as restrições e tente novamente."
        )

    safe_sql = validation.normalized_sql

    # ── EXECUÇÃO ──────────────────────────────────────────────────────────────
    try:
        client = get_client()
        result = client.run_query(safe_sql, source="run_sql_readonly")
    except RuntimeError as exc:
        return (
            f"❌ Erro na execução: {exc}\n\n"
            f"**SQL enviado:**\n```sql\n{safe_sql}\n```"
        )

    return _format_result(
        result=result,
        title="📋 Resultado SQL",
        metadata={"Motivo do SQL livre": reason or "não informado"},
    )


# =============================================================================
# TOOL 4 — get_lineage
# =============================================================================

@mcp.tool()
def get_lineage(model: str) -> str:
    """
    Retorna a linhagem de dados de um modelo dbt — de onde vêm os dados,
    quais transformações foram aplicadas e quais colunas estão disponíveis.

    Use quando o agente ou o usuário precisar entender a origem de um número
    ou verificar se uma coluna existe antes de escrever SQL.

    Modelos disponíveis:
      fct_orders | dim_customers | dim_customers_kpis | dim_products | agg_daily_revenue

    Args:
        model : Nome do modelo dbt (sem prefixo de schema)
    """
    logger.info("tool_called", extra={"tool": "get_lineage", "model": model})

    # Mapa de linhagem estático — derivado do dbt graph
    # [PRODUÇÃO] Ler do dbt manifest.json gerado por `dbt docs generate`
    lineage_map = {
        "fct_orders": {
            "origem": "seed_ecommerce_sintetico (CSV ~192k linhas)",
            "pipeline": [
                "1. seed → raw.seed_ecommerce_sintetico (cópia fiel, sem transformação)",
                "2. raw → staging.stg_ecommerce__orders (dedup, cast, renames, sanidade)",
                "3. staging → marts.fct_orders (joins com dim_customers, cálculos de receita)",
            ],
            "tratamentos_staging": [
                "Dedup por order_id (mantém o mais recente)",
                "Timestamps com fuso → UTC via SAFE_CAST",
                "Quantidade negativa (devoluções) → ABS()",
                "Desconto > 100% → NULL",
                "Estado cliente → normalizado via macro normalize_estado",
            ],
            "tratamentos_mart": [
                "delivered_revenue_brl = total_amount_brl se status='entregue', senão 0",
                "return_amount_brl = LEAST(valor_devolucao, valor_total)",
                "net_revenue_brl = total_amount_brl - return_amount_brl",
                "is_cross_month_return = TRUE se devolução em mês diferente do pedido",
            ],
            "tabela_bq": "marts.fct_orders",
            "grão": "1 linha por order_id",
            "atualização": "dbt run --select fct_orders (full refresh via seed)",
        },
        "dim_customers": {
            "origem": "Derivada de fct_orders (fonte não tem tabela de clientes)",
            "pipeline": [
                "stg_ecommerce__orders → dim_customers (agregação por customer_id)",
            ],
            "tratamentos_mart": [
                "Estado e CEP do pedido mais recente do cliente",
                "first_order_at / last_order_at via MIN/MAX",
                "lifetime_value_brl = SUM(total_amount_brl) de todos os pedidos",
            ],
            "tabela_bq": "marts.dim_customers",
            "grão": "1 linha por customer_id",
            "atualização": "dbt run --select dim_customers",
        },
        "dim_customers_kpis": {
            "origem": "Derivada de fct_orders + dim_customers",
            "pipeline": [
                "fct_orders → dim_customers_kpis (KPIs, RFM, segmentação)",
            ],
            "tratamentos_mart": [
                "Janelas temporais: 30, 60, 90 dias via DATE_SUB(CURRENT_DATE())",
                "Tendência: últimas 4 semanas vs 4 anteriores (revenue_trend_pct)",
                "RFM por percentis p25/p75 da base (auto-ajustável)",
                "Segmento: champions, loyal, new_customer, promising, at_risk, lost",
                "activity_status: active, at_risk, churning, churned",
            ],
            "tabela_bq": "marts.dim_customers_kpis",
            "grão": "1 linha por customer_id (snapshot diário)",
            "atualização": "dbt run --select dim_customers_kpis (recomendado: diário)",
            "contrato_dbt": "Ativo — contract: enforced: true (tipos e colunas garantidos)",
        },
        "dim_products": {
            "origem": "Derivada de stg_ecommerce__orders (fonte não tem catálogo)",
            "pipeline": [
                "stg_ecommerce__orders → dim_products (GROUP BY product_name)",
            ],
            "tratamentos_mart": [
                "Preços MIN/AVG/MAX por produto",
                "total_units_sold = SUM(quantity)",
                "total_revenue_brl = SUM(total_amount_brl)",
            ],
            "tabela_bq": "marts.dim_products",
            "grão": "1 linha por product_name",
            "atualização": "dbt run --select dim_products",
        },
        "agg_daily_revenue": {
            "origem": "Derivada de fct_orders",
            "pipeline": [
                "fct_orders → agg_daily_revenue (GROUP BY created_date, channel)",
            ],
            "tratamentos_mart": [
                "total_revenue_brl: todos os pedidos",
                "delivered_revenue_brl: só status='entregue'",
                "return_amount_brl: valor devolvido",
                "net_revenue_brl: entregue - devolvido",
                "avg_ticket_brl: ticket médio do dia/canal",
            ],
            "tabela_bq": "marts.agg_daily_revenue",
            "grão": "1 linha por (created_date, channel)",
            "atualização": "dbt run --select agg_daily_revenue",
            "uso": "Fonte principal para dashboards de receita no Looker Studio",
        },
    }

    if model not in lineage_map:
        available = list(lineage_map.keys())
        return (
            f"❌ Modelo '{model}' não encontrado.\n\n"
            f"Modelos disponíveis: {', '.join(available)}"
        )

    info = lineage_map[model]
    lines = [f"# Linhagem — `{model}`\n"]

    for key, val in info.items():
        label = key.replace("_", " ").title()
        if isinstance(val, list):
            lines.append(f"**{label}:**")
            for item in val:
                lines.append(f"  - {item}")
        else:
            lines.append(f"**{label}:** {val}")
        lines.append("")

    return "\n".join(lines)


# =============================================================================
# TOOL 5 — run_quality_checks
# =============================================================================

@mcp.tool()
def run_quality_checks(model: str | None = None) -> str:
    """
    Verifica a saúde dos dados consultando métricas de qualidade diretamente
    no BigQuery — detecta nulos inesperados, valores fora de range e
    duplicatas nos modelos marts.

    Use antes de apresentar resultados ao usuário quando quiser garantir
    que os dados estão íntegros no período consultado.

    Args:
        model : Nome do modelo para verificar (ou None para verificar todos)
                Opções: fct_orders | dim_customers | dim_customers_kpis |
                        dim_products | agg_daily_revenue
    """
    logger.info("tool_called", extra={"tool": "run_quality_checks", "model": model})

    project_id = os.environ["GCP_PROJECT_ID"]
    client = get_client()

    # Checks de qualidade por modelo
    # [PRODUÇÃO] Ler os resultados reais de `dbt test` armazenados no
    # information_schema ou via Elementary data observability.
    quality_queries: dict[str, list[tuple[str, str]]] = {
        "fct_orders": [
            (
                "PKs únicas",
                f"SELECT COUNT(*) - COUNT(DISTINCT order_id) AS duplicates "
                f"FROM `{project_id}.marts.fct_orders`",
            ),
            (
                "Nulos em order_id",
                f"SELECT COUNTIF(order_id IS NULL) AS null_count "
                f"FROM `{project_id}.marts.fct_orders`",
            ),
            (
                "Receita negativa",
                f"SELECT COUNTIF(total_amount_brl < 0) AS negatives "
                f"FROM `{project_id}.marts.fct_orders`",
            ),
            (
                "Status inválido",
                f"SELECT COUNT(*) AS invalid_status FROM `{project_id}.marts.fct_orders` "
                f"WHERE status NOT IN ('aprovado','enviado','entregue','cancelado','aguardando_pagamento')",
            ),
        ],
        "dim_customers": [
            (
                "PKs únicas",
                f"SELECT COUNT(*) - COUNT(DISTINCT customer_id) AS duplicates "
                f"FROM `{project_id}.marts.dim_customers`",
            ),
            (
                "LTV negativo",
                f"SELECT COUNTIF(lifetime_value_brl < 0) AS negatives "
                f"FROM `{project_id}.marts.dim_customers`",
            ),
        ],
        "dim_customers_kpis": [
            (
                "PKs únicas",
                f"SELECT COUNT(*) - COUNT(DISTINCT customer_id) AS duplicates "
                f"FROM `{project_id}.marts.dim_customers_kpis`",
            ),
            (
                "Segmentos inválidos",
                f"SELECT COUNT(*) AS invalid FROM `{project_id}.marts.dim_customers_kpis` "
                f"WHERE segment NOT IN ('champions','loyal','new_customer','promising','at_risk','lost')",
            ),
            (
                "RFM scores fora de range",
                f"SELECT COUNTIF(r_score NOT IN (1,2,3) OR f_score NOT IN (1,2,3) OR m_score NOT IN (1,2,3)) AS invalid "
                f"FROM `{project_id}.marts.dim_customers_kpis`",
            ),
        ],
        "dim_products": [
            (
                "PKs únicas",
                f"SELECT COUNT(*) - COUNT(DISTINCT product_name) AS duplicates "
                f"FROM `{project_id}.marts.dim_products`",
            ),
            (
                "Receita negativa",
                f"SELECT COUNTIF(total_revenue_brl < 0) AS negatives "
                f"FROM `{project_id}.marts.dim_products`",
            ),
        ],
        "agg_daily_revenue": [
            (
                "PKs únicas (date+channel)",
                f"SELECT COUNT(*) - COUNT(DISTINCT CONCAT(CAST(created_date AS STRING), channel)) AS duplicates "
                f"FROM `{project_id}.marts.agg_daily_revenue`",
            ),
            (
                "Receita negativa",
                f"SELECT COUNTIF(net_revenue_brl < 0) AS negatives "
                f"FROM `{project_id}.marts.agg_daily_revenue`",
            ),
        ],
    }

    models_to_check = list(quality_queries.keys()) if model is None else [model]

    if model and model not in quality_queries:
        return (
            f"❌ Modelo '{model}' não reconhecido.\n\n"
            f"Modelos disponíveis: {', '.join(quality_queries.keys())}"
        )

    lines = ["# Resultado dos Quality Checks\n"]
    all_ok = True

    for m in models_to_check:
        lines.append(f"## `{m}`")
        checks = quality_queries[m]
        rows = []

        for check_name, check_sql in checks:
            try:
                result = client.run_query(check_sql, limit=1, source="run_quality_checks")
                if result["rows"]:
                    value = list(result["rows"][0].values())[0]
                    status = "✅ OK" if value == 0 else f"⚠️ FALHOU ({value} registros)"
                    if value != 0:
                        all_ok = False
                else:
                    status = "⚠️ Sem dados"
                rows.append([check_name, status])
            except Exception as exc:
                rows.append([check_name, f"❌ Erro: {exc}"])
                all_ok = False

        lines.append(tabulate(rows, headers=["Check", "Resultado"], tablefmt="github"))
        lines.append("")

    summary = "✅ Todos os checks passaram." if all_ok else "⚠️ Há falhas — investigar antes de apresentar resultados."
    lines.append(f"**Resumo:** {summary}")

    return "\n".join(lines)


# =============================================================================
# HELPERS DE FORMATAÇÃO
# =============================================================================

def _format_result(
    result: dict,
    title: str,
    metadata: dict | None = None,
    caveats: list[str] | None = None,
) -> str:
    """
    Formata o resultado de uma query para apresentação ao agente.

    Inclui:
    - Título e metadados (para citação de fonte)
    - Tabela de dados em markdown
    - Aviso de truncamento se houver mais linhas
    - Armadilhas da métrica (para o agente não interpretar errado)
    - SQL executado (rastreabilidade)
    """
    lines = [f"# {title}\n"]

    # Metadados (citação de fonte)
    if metadata:
        lines.append("**Contexto da consulta:**")
        for k, v in metadata.items():
            lines.append(f"  - **{k}:** {v}")
        lines.append("")

    # Dados
    if result["row_count"] == 0:
        lines.append("⚠️ Nenhum resultado encontrado para os filtros aplicados.")
    else:
        lines.append(
            tabulate(
                result["rows"],
                headers="keys",
                tablefmt="github",
                floatfmt=".2f",
            )
        )
        lines.append(f"\n*{result['row_count']} linhas | {result['elapsed_ms']}ms*")

        if result["truncated"]:
            lines.append(
                "\n⚠️ **Resultado truncado** — há mais linhas além do limite. "
                "Use filtros mais específicos (period, filters) para resultados completos."
            )

    # Armadilhas da métrica
    if caveats:
        lines.append("\n**⚠️ Armadilhas desta métrica:**")
        for c in caveats:
            lines.append(f"  - {c}")

    # SQL executado (obrigatório para citação de fonte)
    lines.append(f"\n<details><summary>SQL executado</summary>\n\n```sql\n{result['sql_executed']}\n```\n</details>")

    return "\n".join(lines)


# =============================================================================
# ENTRYPOINT
# =============================================================================

if __name__ == "__main__":
    logger.info("Iniciando MCP Server — Agentic Data Platform")
    logger.info(f"Project ID: {os.environ.get('GCP_PROJECT_ID', 'NÃO CONFIGURADO')}")
    logger.info(f"Tools registradas: list_metrics, query_metric, run_sql_readonly, get_lineage, run_quality_checks")

    # mcp.run() inicia o loop de eventos e abre o transporte stdio
    # O Claude Desktop conecta via stdin/stdout
    mcp.run()
