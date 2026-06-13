-- =============================================================================
-- models/marts/metricflow_time_spine.sql
--
-- Modelo de calendário exigido pelo MetricFlow para cálculo de métricas
-- com dimensões temporais (séries diárias, semanais, mensais).
--
-- Granularidade: DAY (mínimo exigido pelo MetricFlow).
-- Intervalo: 2019-01-01 a 2030-12-31 (cobre histórico + projeções futuras).
-- Materializado como table para evitar recalculo a cada query.
-- =============================================================================

{{ config(materialized = 'table') }}

with spine as (
    {{ dbt_utils.date_spine(
        datepart    = "day",
        start_date  = "cast('2019-01-01' as date)",
        end_date    = "cast('2030-12-31' as date)"
    ) }}
)

select
    cast(date_day as date) as date_day
from spine
