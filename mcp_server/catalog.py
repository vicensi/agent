"""
catalog.py — Catálogo estático de métricas do e-commerce.

Por que um catálogo Python em vez de parsear o YAML do MetricFlow?
  - O MCP server é um processo independente do dbt — não tem dependência de runtime dbt.
  - O catálogo aqui é a "tradução" das métricas do YAML para SQL que o BigQuery executa.
  - As descriptions são propositalmente longas: o agente LLM lê isso para decidir
    qual métrica usar. Descriptions ruins = escolhas erradas do agente.

[PRODUÇÃO] Este catálogo poderia ser gerado automaticamente a partir do dbt manifest.json
(artefato gerado por `dbt docs generate`) que já contém todas as métricas compiladas.
Para estudo, mantemos o catálogo manual para entender o mapeamento explicitamente.
"""

from dataclasses import dataclass, field


@dataclass
class MetricDefinition:
    """
    Define como uma métrica é calculada e quando usá-la.

    sql_expr     : expressão SQL que vai no SELECT (pode usar colunas de source_table)
    source_table : tabela do BigQuery sem o project_id (ex: "marts.fct_orders")
    available_dimensions : colunas que podem ser usadas em GROUP BY / WHERE
    description  : texto que o agente lê — seja específico sobre o que inclui/exclui
    label        : nome legível para humanos
    unit         : "BRL", "count", "%", "days"
    """
    name: str
    label: str
    description: str
    sql_expr: str
    source_table: str
    available_dimensions: list[str]
    unit: str
    caveats: list[str] = field(default_factory=list)


# =============================================================================
# CATÁLOGO DE MÉTRICAS
#
# Convenções SQL:
#   - Todas as expressões referenciam colunas de marts.fct_orders
#   - Status em português: 'entregue', 'aprovado', 'enviado', 'cancelado', 'aguardando_pagamento'
#   - Colunas monetárias com sufixo _brl
# =============================================================================

METRICS: dict[str, MetricDefinition] = {

    # ── 1. RECEITA ENTREGUE ──────────────────────────────────────────────────
    "delivered_revenue": MetricDefinition(
        name="delivered_revenue",
        label="Receita Entregue (BRL)",
        description=(
            "Soma do valor total de pedidos com status = 'entregue', em BRL. "
            "Esta é a métrica CANÔNICA de receita — use sempre que o contexto for "
            "'quanto vendemos de verdade'. Exclui pedidos pendentes, cancelados e devolvidos. "
            "Grão mínimo: dia. Use dimensões channel e state para segmentação."
        ),
        sql_expr="SUM(CASE WHEN status = 'entregue' THEN total_amount_brl ELSE 0 END)",
        source_table="marts.fct_orders",
        available_dimensions=["created_date", "channel", "status", "customer_state"],
        unit="BRL",
        caveats=[
            "Não somar com gross_revenue — os conjuntos se sobrepõem.",
            "Pedidos cancelados ou aguardando_pagamento nunca entram nesta métrica.",
        ],
    ),

    # ── 2. RECEITA BRUTA ────────────────────────────────────────────────────
    "gross_revenue": MetricDefinition(
        name="gross_revenue",
        label="Receita Bruta (BRL)",
        description=(
            "Soma do valor total de pedidos com status IN ('aprovado', 'enviado', 'entregue'), em BRL. "
            "Representa o volume financeiro comprometido — inclui pedidos ainda em trânsito. "
            "Use para entender o pipeline de receita, não a receita realizada. "
            "Para receita realizada, use delivered_revenue."
        ),
        sql_expr=(
            "SUM(CASE WHEN status IN ('aprovado', 'enviado', 'entregue') "
            "THEN total_amount_brl ELSE 0 END)"
        ),
        source_table="marts.fct_orders",
        available_dimensions=["created_date", "channel", "status", "customer_state"],
        unit="BRL",
        caveats=[
            "gross_revenue >= delivered_revenue sempre. Diferença = pedidos em trânsito.",
            "Não somar com delivered_revenue — os conjuntos se sobrepõem.",
        ],
    ),

    # ── 3. RECEITA LÍQUIDA ───────────────────────────────────────────────────
    "net_revenue": MetricDefinition(
        name="net_revenue",
        label="Receita Líquida (BRL)",
        description=(
            "Receita entregue menos o valor total de devoluções, em BRL. "
            "Fórmula: SUM(net_revenue_brl) — coluna pré-calculada no mart. "
            "Use quando quiser a receita real após estornos e devoluções. "
            "Se net_revenue divergir muito de delivered_revenue, investigar return_rate."
        ),
        sql_expr="SUM(net_revenue_brl)",
        source_table="marts.fct_orders",
        available_dimensions=["created_date", "channel", "status", "customer_state"],
        unit="BRL",
        caveats=[
            "Devoluções podem ocorrer em mês diferente da compra (cross-month return). "
            "Ao analisar por mês, o net_revenue pode cair por devoluções de compras anteriores.",
        ],
    ),

    # ── 4. RECEITA DEVOLVIDA ────────────────────────────────────────────────
    "returned_revenue": MetricDefinition(
        name="returned_revenue",
        label="Receita Devolvida (BRL)",
        description=(
            "Soma de return_amount_brl para pedidos com has_return = true, em BRL. "
            "A fonte não tem status 'returned' — devolução é capturada pela flag has_return. "
            "Use para monitorar o impacto financeiro de devoluções."
        ),
        sql_expr="SUM(CASE WHEN has_return = true THEN return_amount_brl ELSE 0 END)",
        source_table="marts.fct_orders",
        available_dimensions=["created_date", "channel", "customer_state"],
        unit="BRL",
        caveats=[
            "O mês da devolução pode diferir do mês da compra (cross-month return).",
            "Para análise de cohort de devolução, use a data do evento, não a data do pedido.",
        ],
    ),

    # ── 5. PEDIDOS ENTREGUES ────────────────────────────────────────────────
    "delivered_orders": MetricDefinition(
        name="delivered_orders",
        label="Pedidos Entregues",
        description=(
            "Contagem de pedidos únicos com status = 'entregue'. "
            "Use como denominador para calcular ticket médio, taxa de devolução e conversão. "
            "Não use order_count (que inclui todos os status) como denominador quando o "
            "numerador for uma métrica de receita entregue — os conjuntos não batem."
        ),
        sql_expr="COUNT(DISTINCT CASE WHEN status = 'entregue' THEN order_id END)",
        source_table="marts.fct_orders",
        available_dimensions=["created_date", "channel", "customer_state"],
        unit="count",
        caveats=[
            "Usar sempre como denominador junto com métricas de receita entregue.",
        ],
    ),

    # ── 6. TICKET MÉDIO ─────────────────────────────────────────────────────
    "avg_order_value": MetricDefinition(
        name="avg_order_value",
        label="Ticket Médio (BRL)",
        description=(
            "Receita entregue dividida pelo número de pedidos entregues, em BRL. "
            "Fórmula: SUM(delivered_revenue_brl) / COUNT(DISTINCT order_id para entregues). "
            "Métrica central para acompanhar saúde do negócio — queda no ticket com "
            "volume estável indica migração para produtos de menor valor ou excesso de desconto."
        ),
        sql_expr=(
            "SAFE_DIVIDE("
            "SUM(CASE WHEN status = 'entregue' THEN total_amount_brl ELSE 0 END), "
            "COUNT(DISTINCT CASE WHEN status = 'entregue' THEN order_id END)"
            ")"
        ),
        source_table="marts.fct_orders",
        available_dimensions=["created_date", "channel", "customer_state"],
        unit="BRL",
        caveats=[
            "Sensível a outliers (pedidos B2B de alto valor). "
            "Se o ticket subir abruptamente, verificar pedidos atípicos no período.",
        ],
    ),

    # ── 7. TAXA DE DEVOLUÇÃO ────────────────────────────────────────────────
    "return_rate": MetricDefinition(
        name="return_rate",
        label="Taxa de Devolução (%)",
        description=(
            "Percentual de pedidos com devolução sobre o total de pedidos entregues. "
            "Fórmula: (COUNT pedidos com has_return=true / COUNT pedidos entregues) * 100. "
            "Benchmarks: fashion 20–30%, eletrônicos 5–10%, alimentos <2%. "
            "Acima de 15% em qualquer categoria merece investigação de causa raiz."
        ),
        sql_expr=(
            "SAFE_DIVIDE("
            "COUNTIF(has_return = true AND status = 'entregue'), "
            "COUNT(DISTINCT CASE WHEN status = 'entregue' THEN order_id END)"
            ") * 100"
        ),
        source_table="marts.fct_orders",
        available_dimensions=["created_date", "channel", "customer_state"],
        unit="%",
        caveats=[
            "Devoluções têm lag — pedido entregue em dezembro pode ser devolvido em janeiro. "
            "Taxa em janelas curtas tende a ser subestimada.",
            "Para análise precisa, usar janela de 90 dias ou comparar cohorts de compra.",
        ],
    ),

    # ── 8. CLIENTES ATIVOS 30D ──────────────────────────────────────────────
    "active_customers_30d": MetricDefinition(
        name="active_customers_30d",
        label="Clientes Ativos — últimos 30 dias",
        description=(
            "Contagem de customer_id distintos com ao menos 1 pedido entregue nos últimos 30 dias. "
            "Use para medir retenção de curto prazo. "
            "Compare com o total de clientes para calcular taxa de ativação. "
            "ATENÇÃO: este filtro de 30 dias é aplicado fixo — não use com period para evitar duplo filtro."
        ),
        sql_expr=(
            "COUNT(DISTINCT CASE "
            "WHEN status = 'entregue' "
            "AND created_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY) "
            "THEN customer_id END)"
        ),
        source_table="marts.fct_orders",
        available_dimensions=["channel", "customer_state"],
        unit="count",
        caveats=[
            "Janela fixa de 30 dias — não compatível com filtro period.",
            "Clientes que compraram há 31 dias saem abruptamente. "
            "Para tendências suaves, usar query_metric com dim_customers_kpis.",
        ],
    ),
}


def get_metric(name: str) -> MetricDefinition | None:
    """Retorna a definição de uma métrica ou None se não existir."""
    return METRICS.get(name)


def list_metric_names() -> list[str]:
    """Retorna os nomes de todas as métricas disponíveis."""
    return list(METRICS.keys())


def format_catalog_for_agent() -> str:
    """
    Formata o catálogo completo para ser retornado pelo tool list_metrics().
    O texto gerado é lido pelo agente para escolher qual métrica usar.
    Qualidade do texto aqui impacta diretamente a precisão do agente.
    """
    lines = ["# Catálogo de Métricas — Agentic Data Platform\n"]
    lines.append("Fonte primária: `marts.fct_orders` (BigQuery)\n")
    lines.append("Para consultar uma métrica, use `query_metric(metric=<name>, ...)`\n\n")

    for i, (name, m) in enumerate(METRICS.items(), 1):
        lines.append(f"## {i}. `{name}` — {m.label}")
        lines.append(f"**Unidade:** {m.unit}")
        lines.append(f"**Descrição:** {m.description}")
        lines.append(f"**Dimensões disponíveis:** {', '.join(m.available_dimensions)}")
        if m.caveats:
            lines.append("**Armadilhas:**")
            for c in m.caveats:
                lines.append(f"  - {c}")
        lines.append("")

    return "\n".join(lines)
