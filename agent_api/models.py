"""
models.py — Modelos Pydantic de request e response da Agent API.

Pydantic serve dois propósitos aqui:
  1. Validação automática dos dados de entrada (FastAPI rejeita requests inválidas
     antes de chegar no código do agente)
  2. Serialização da resposta para JSON com tipos corretos

[PRODUÇÃO] Adicionar: autenticação (Bearer token), rate limiting por API key,
versionamento de contrato (/v1/ask, /v2/ask).
"""

from pydantic import BaseModel, Field


# =============================================================================
# REQUEST MODELS
# =============================================================================

class AskRequest(BaseModel):
    """
    Request para o endpoint POST /ask.
    O campo principal é question — linguagem natural sem restrições de formato.
    """
    question: str = Field(
        ...,
        description="Pergunta em linguagem natural sobre os dados de e-commerce.",
        examples=["Qual a receita líquida por canal nos últimos 90 dias?"],
        min_length=3,
        max_length=1000,
    )
    session_id: str | None = Field(
        default=None,
        description="Identificador de sessão para rastreamento (opcional).",
    )


class QueryRequest(BaseModel):
    """
    Request para POST /query — query estruturada sem passar pelo agente.
    Útil para integrações como Power BI que já sabem qual métrica querem.
    """
    metric: str = Field(
        ...,
        description="Nome da métrica. Use GET /metrics para ver as disponíveis.",
        examples=["delivered_revenue", "net_revenue", "return_rate"],
    )
    dimensions: list[str] | None = Field(
        default=None,
        description="Colunas para GROUP BY. Variam por métrica.",
        examples=[["channel", "customer_state"]],
    )
    period: str | None = Field(
        default=None,
        description=(
            "Período de análise. "
            "Opções: last_7d | last_30d | last_90d | last_180d | last_365d | "
            "YYYY-MM | YYYY-QN | YYYY | None (toda a história)"
        ),
        examples=["last_90d", "2024-Q1", "2024-01"],
    )
    filters: dict | None = Field(
        default=None,
        description="Filtros adicionais como dict coluna:valor.",
        examples=[{"channel": "app_ios"}],
    )
    limit: int = Field(
        default=500,
        ge=1,
        le=500,
        description="Máximo de linhas retornadas.",
    )


# =============================================================================
# RESPONSE MODELS
# =============================================================================

class ChartSpec(BaseModel):
    """
    Especificação de um gráfico gerado pelo agente via tool plot_chart.
    O frontend (Streamlit) usa essa spec para renderizar Plotly inline.
    """
    chart_type: str = Field(
        description="Tipo do gráfico: bar | bar_horizontal | line | pie.",
    )
    title: str = Field(description="Título do gráfico.")
    labels: list[str] = Field(description="Categorias ou períodos do eixo X.")
    values: list = Field(
        description="Valores numéricos: list[float] para série única, list[list[float]] para múltiplas séries.",
    )
    series_names: list[str] | None = Field(
        default=None,
        description="Nomes das séries (quando values for list[list[float]]).",
    )
    x_label: str | None = Field(default=None, description="Rótulo do eixo X.")
    y_label: str | None = Field(default=None, description="Rótulo do eixo Y.")


class ToolCallRecord(BaseModel):
    """
    Registro de uma tool call executada durante o processamento da pergunta.
    Compõe a 'citação de fonte' obrigatória em toda resposta.
    """
    tool_name: str = Field(description="Nome da tool chamada.")
    parameters: dict = Field(description="Parâmetros usados na chamada.")
    sql_executed: str | None = Field(
        default=None,
        description="SQL enviado ao BigQuery (quando aplicável).",
    )
    row_count: int | None = Field(
        default=None,
        description="Número de linhas retornadas pelo BigQuery.",
    )
    elapsed_ms: int | None = Field(
        default=None,
        description="Tempo de execução da query em ms.",
    )


class AskResponse(BaseModel):
    """
    Response do endpoint POST /ask.

    O campo answer é a resposta formatada em markdown.
    O campo tool_calls é a trilha de auditoria — todas as tools chamadas
    pelo agente para responder a pergunta, com seus parâmetros e SQL.
    """
    answer: str = Field(description="Resposta formatada em markdown com citação de fonte.")
    tool_calls: list[ToolCallRecord] = Field(
        description="Trilha de auditoria: todas as tools chamadas e seus parâmetros.",
    )
    charts: list[ChartSpec] = Field(
        default_factory=list,
        description="Gráficos gerados pelo agente via plot_chart, para renderização inline no frontend.",
    )
    question: str = Field(description="Pergunta original recebida.")
    session_id: str | None = Field(default=None)
    total_elapsed_ms: int = Field(description="Tempo total de processamento em ms.")
    refused: bool = Field(
        default=False,
        description="True se o agente recusou responder (pergunta fora de escopo).",
    )


class QueryResponse(BaseModel):
    """Response do endpoint POST /query — resultado direto sem agente."""
    metric: str
    label: str
    rows: list[dict]
    row_count: int
    columns: list[str]
    sql_executed: str
    elapsed_ms: int
    truncated: bool
    period: str | None
    dimensions: list[str] | None


class MetricInfo(BaseModel):
    """Informação de uma métrica no catálogo."""
    name: str
    label: str
    description: str
    unit: str
    available_dimensions: list[str]
    caveats: list[str]


class MetricsResponse(BaseModel):
    """Response do endpoint GET /metrics."""
    metrics: list[MetricInfo]
    total: int
