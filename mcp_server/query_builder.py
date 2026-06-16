"""
query_builder.py — Geração de SQL parametrizado a partir de definições de métricas.

Conceito — Por que gerar SQL em vez de chamar MetricFlow?
  MetricFlow tem um runtime que gera SQL, mas requer o dbt project instalado e
  configurado. O MCP server é um processo leve e independente — não queremos
  acoplá-lo ao dbt runtime. Em vez disso, traduzimos a definição de métrica
  (sql_expr, source_table, dimensões) em SQL BigQuery diretamente.

  [PRODUÇÃO] A alternativa correta seria:
    1. Usar a dbt Semantic Layer API (gRPC) que o MetricFlow expõe
    2. Ou a dbt Cloud Semantic Layer via JDBC (Snowflake/BigQuery)
  Esses endpoints retornam SQL compilado sem expor o banco diretamente.
  Para estudo, geramos o SQL manualmente para aprender o mapeamento.

Estrutura do SQL gerado:
  SELECT
    {dimensoes},                    -- GROUP BY columns
    {sql_expr} AS {metric_name}     -- medida da métrica
  FROM `{project}.{table}`
  WHERE {period_filter}             -- filtro de período
    AND {dimension_filters}         -- filtros adicionais
  GROUP BY {dimensoes}
  ORDER BY {primeira_dimensao}
  LIMIT {limit}
"""

import logging
from datetime import date, datetime, timedelta
from typing import Any

from catalog import MetricDefinition, get_metric

logger = logging.getLogger(__name__)

# Colunas que representam datas e recebem formatação especial no ORDER BY
DATE_DIMENSIONS = {"created_date", "returned_date", "created_month"}

# Mapeamento de period shortcuts para ranges SQL
def _resolve_period(period: str | None) -> tuple[str, str] | None:
    """
    Converte um período legível em (data_inicio, data_fim) para o WHERE.

    Formatos suportados:
      "last_7d"    → últimos 7 dias
      "last_30d"   → últimos 30 dias
      "last_90d"   → últimos 90 dias
      "last_180d"  → últimos 180 dias
      "last_365d"  → último ano
      "2024-01"    → Janeiro de 2024
      "2024-Q1"    → Q1 de 2024 (Jan-Mar)
      "2024"       → ano completo 2024
      None         → sem filtro de período (toda a história)
    """
    if period is None:
        return None

    today = date.today()

    # Shortcuts de janela deslizante
    rolling_windows = {
        "last_7d": 7,
        "last_30d": 30,
        "last_90d": 90,
        "last_180d": 180,
        "last_365d": 365,
    }
    if period in rolling_windows:
        days = rolling_windows[period]
        start = today - timedelta(days=days)
        return start.isoformat(), today.isoformat()

    # Mês específico: "2024-01"
    if len(period) == 7 and period[4] == "-":
        try:
            year, month = int(period[:4]), int(period[5:])
            start = date(year, month, 1)
            if month == 12:
                end = date(year + 1, 1, 1) - timedelta(days=1)
            else:
                end = date(year, month + 1, 1) - timedelta(days=1)
            return start.isoformat(), end.isoformat()
        except ValueError:
            pass

    # Quarter: "2024-Q1", "2024-Q2", etc.
    if len(period) == 7 and "Q" in period.upper():
        try:
            year = int(period[:4])
            q = int(period[-1])
            if q not in (1, 2, 3, 4):
                raise ValueError("Quarter inválido")
            start_month = (q - 1) * 3 + 1
            end_month = start_month + 2
            start = date(year, start_month, 1)
            if end_month == 12:
                end = date(year, 12, 31)
            else:
                end = date(year, end_month + 1, 1) - timedelta(days=1)
            return start.isoformat(), end.isoformat()
        except (ValueError, IndexError):
            pass

    # Ano completo: "2024"
    if len(period) == 4 and period.isdigit():
        year = int(period)
        return f"{year}-01-01", f"{year}-12-31"

    raise ValueError(
        f"Período '{period}' não reconhecido. "
        "Formatos válidos: last_7d, last_30d, last_90d, last_180d, last_365d, "
        "2024-01 (mês), 2024-Q1 (quarter), 2024 (ano), ou None (sem filtro)."
    )


def build_metric_query(
    metric_def: MetricDefinition,
    project_id: str,
    dimensions: list[str] | None = None,
    period: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 500,
) -> str:
    """
    Gera SQL BigQuery para uma métrica com dimensões, período e filtros opcionais.

    Args:
        metric_def  : Definição da métrica do catalog.py
        project_id  : GCP project ID (ex: "projeto-agents-499023")
        dimensions  : Colunas para GROUP BY (ex: ["channel", "customer_state"])
        period      : Período (ex: "last_30d", "2024-Q1")
        filters     : Filtros adicionais (ex: {"channel": "app_ios"})
        limit       : Máximo de linhas (default 500)

    Returns:
        SQL string válido para BigQuery

    Raises:
        ValueError  : Dimensão não disponível para esta métrica ou período inválido
    """
    dimensions = dimensions or []
    filters = filters or {}

    # ── VALIDAÇÃO DE DIMENSÕES ────────────────────────────────────────────────
    # Verifica se as dimensões solicitadas estão disponíveis para esta métrica
    invalid_dims = set(dimensions) - set(metric_def.available_dimensions)
    if invalid_dims:
        raise ValueError(
            f"Dimensões não disponíveis para '{metric_def.name}': {sorted(invalid_dims)}. "
            f"Dimensões válidas: {metric_def.available_dimensions}"
        )

    # ── TABELA COMPLETA ───────────────────────────────────────────────────────
    full_table = f"`{project_id}.{metric_def.source_table}`"

    # ── CLÁUSULAS SELECT ──────────────────────────────────────────────────────
    select_parts = []

    # Dimensões primeiro (convenção: dimensões antes das métricas no SELECT)
    for dim in dimensions:
        select_parts.append(f"    {dim}")

    # Expressão da métrica
    select_parts.append(
        f"    {metric_def.sql_expr} AS {metric_def.name}"
    )

    select_clause = ",\n".join(select_parts)

    # ── WHERE ────────────────────────────────────────────────────────────────
    where_parts = []

    # Filtro de período
    period_range = _resolve_period(period)
    if period_range:
        start, end = period_range
        where_parts.append(
            f"    created_date BETWEEN '{start}' AND '{end}'"
        )

    # Filtros de dimensão (ex: channel = 'app_ios')
    for col, val in filters.items():
        if isinstance(val, str):
            where_parts.append(f"    {col} = '{val}'")
        elif isinstance(val, (int, float)):
            where_parts.append(f"    {col} = {val}")
        elif isinstance(val, (list, tuple)):
            quoted = ", ".join(f"'{v}'" if isinstance(v, str) else str(v) for v in val)
            where_parts.append(f"    {col} IN ({quoted})")
        elif val is None:
            where_parts.append(f"    {col} IS NULL")

    where_clause = ""
    if where_parts:
        where_clause = "WHERE\n" + "\nAND\n".join(where_parts)

    # ── GROUP BY ─────────────────────────────────────────────────────────────
    group_by_clause = ""
    if dimensions:
        group_by_clause = "GROUP BY\n    " + ",\n    ".join(dimensions)

    # ── ORDER BY ─────────────────────────────────────────────────────────────
    order_by_clause = ""
    if dimensions:
        # Prioriza dimensões de data no ORDER BY (mais natural para séries temporais)
        date_dims = [d for d in dimensions if d in DATE_DIMENSIONS]
        other_dims = [d for d in dimensions if d not in DATE_DIMENSIONS]
        order_dims = date_dims + other_dims
        order_by_clause = "ORDER BY\n    " + ",\n    ".join(order_dims)

    # ── MONTAGEM DO SQL ───────────────────────────────────────────────────────
    parts = [
        f"-- Métrica: {metric_def.label}",
        f"-- Fonte: {metric_def.source_table}",
        f"-- Gerado por: query_builder.build_metric_query()",
        "",
        "SELECT",
        select_clause,
        f"FROM {full_table}",
    ]

    if where_clause:
        parts.append(where_clause)
    if group_by_clause:
        parts.append(group_by_clause)
    if order_by_clause:
        parts.append(order_by_clause)

    parts.append(f"LIMIT {limit}")

    sql = "\n".join(parts)

    logger.info(
        "sql_built",
        extra={
            "metric": metric_def.name,
            "dimensions": dimensions,
            "period": period,
            "filters": list(filters.keys()),
        },
    )

    return sql
