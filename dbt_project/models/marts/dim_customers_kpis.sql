-- =============================================================================
-- models/marts/dim_customers_kpis.sql
--
-- Dimensão enriquecida de clientes com KPIs de negócio para BI.
-- 1 linha por customer_id — snapshot do estado atual do cliente.
--
-- Métricas cobertas:
--   • RFM         — Recência, Frequência, Valor Monetário
--   • Tendência   — comparativo últimas 4 semanas vs 4 anteriores
--   • Retenção    — ativo/inativo, risco de churn
--   • Ticket      — médio, máximo, variação
--   • Devoluções  — taxa e impacto financeiro
--   • Segmento    — classificação automática por comportamento
--
-- Consumo: Looker Studio → conectar diretamente nesta tabela.
-- Atualização: diária via dbt run --select dim_customers_kpis
-- =============================================================================

{{ config(
    materialized         = 'table',
    schema               = 'marts',
    unique_key           = 'customer_id',
    cluster_by           = ['segment', 'state'],
    labels               = {"layer": "mart", "domain": "customers", "refresh": "daily"}
) }}

-- ----------------------------------------------------------------------------
-- BLOCO 1 — Base de pedidos entregues (única fonte de verdade para KPIs)
-- Status em português conforme normalização do staging.

-- ----------------------------------------------------------------------------
with delivered_orders as (
    select
        customer_id,
        order_id,
        created_date            as order_date,
        total_amount_brl,
        status
    from {{ ref('fct_orders') }}
    where status = 'entregue'

),

-- ----------------------------------------------------------------------------
-- BLOCO 2 — Devoluções (separadas para não distorcer métricas de receita)
-- A fonte não tem status 'returned' — devolução é capturada por has_return.

-- ----------------------------------------------------------------------------
returned_orders as (
    select
        customer_id,
        order_id,
        return_amount_brl       as returned_amount_brl,
        created_date            as order_date
    from {{ ref('fct_orders') }}
    where has_return = true
      and return_amount_brl > 0

),

-- ----------------------------------------------------------------------------
-- BLOCO 3 — Dados cadastrais do cliente
-- A fonte é denormalizada (seed): não há tabela de clientes separada.
-- Usamos dim_customers que já consolida 1 linha por customer_id.
-- Campos customer_name/email/city não existem na fonte.

-- ----------------------------------------------------------------------------
customers as (
    select
        customer_id,
        customer_state          as state,
        customer_zip,
        first_order_at,
        last_order_at
    from {{ ref('dim_customers') }}
),

-- ----------------------------------------------------------------------------
-- BLOCO 4 — KPIs globais (toda a vida do cliente)
-- ----------------------------------------------------------------------------
lifetime_metrics as (
    select
        customer_id,

        -- Frequência
        COUNT(DISTINCT order_id)                            as total_orders,
        COUNT(DISTINCT DATE_TRUNC(order_date, MONTH))       as active_months,

        -- Valor
        SUM(total_amount_brl)                               as lifetime_revenue_brl,
        AVG(total_amount_brl)                               as avg_order_value_brl,
        MAX(total_amount_brl)                               as max_order_value_brl,

        -- Recência
        MAX(order_date)                                     as last_order_date,
        MIN(order_date)                                     as first_order_date,
        DATE_DIFF(CURRENT_DATE(), MAX(order_date), DAY)     as days_since_last_order,
        DATE_DIFF(MAX(order_date), MIN(order_date), DAY)    as customer_lifespan_days

    from delivered_orders
    group by customer_id
),

-- ----------------------------------------------------------------------------
-- BLOCO 5 — KPIs de janelas temporais (últimos N dias)
-- Referência sempre em CURRENT_DATE para snapshot diário consistente.
-- ----------------------------------------------------------------------------
windowed_metrics as (
    select
        customer_id,

        -- Últimos 30 dias
        COUNTIF(order_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY))
                                                            as orders_last_30d,
        SUM(IF(order_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY),
               total_amount_brl, 0))                        as revenue_last_30d,

        -- Últimos 60 dias
        COUNTIF(order_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY))
                                                            as orders_last_60d,
        SUM(IF(order_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY),
               total_amount_brl, 0))                        as revenue_last_60d,

        -- Últimos 90 dias
        COUNTIF(order_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY))
                                                            as orders_last_90d,
        SUM(IF(order_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY),
               total_amount_brl, 0))                        as revenue_last_90d,

        -- Últimas 4 semanas vs 4 semanas anteriores (tendência)
        SUM(IF(order_date BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL 28 DAY)
                              AND CURRENT_DATE(),
               total_amount_brl, 0))                        as revenue_last_4w,

        SUM(IF(order_date BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL 56 DAY)
                              AND DATE_SUB(CURRENT_DATE(), INTERVAL 29 DAY),
               total_amount_brl, 0))                        as revenue_prev_4w

    from delivered_orders
    group by customer_id
),

-- ----------------------------------------------------------------------------
-- BLOCO 6 — Métricas de devolução
-- ----------------------------------------------------------------------------
return_metrics as (
    select
        customer_id,
        COUNT(DISTINCT order_id)    as total_returned_orders,
        SUM(returned_amount_brl)    as total_returned_brl
    from returned_orders
    group by customer_id
),

-- ----------------------------------------------------------------------------
-- BLOCO 7 — Cálculo de intervalo médio entre compras (cadência)
-- Útil para detectar clientes com padrão previsível de recompra.
-- ----------------------------------------------------------------------------
order_gaps as (
    select
        customer_id,
        order_date,
        LAG(order_date) OVER (
            PARTITION BY customer_id
            ORDER BY order_date
        )                           as prev_order_date
    from delivered_orders
),

purchase_cadence as (
    select
        customer_id,
        ROUND(AVG(
            DATE_DIFF(order_date, prev_order_date, DAY)
        ), 1)                       as avg_days_between_orders
    from order_gaps
    where prev_order_date is not null
    group by customer_id
),

-- ----------------------------------------------------------------------------
-- BLOCO 8 — Consolidação e cálculo dos KPIs derivados
-- ----------------------------------------------------------------------------
consolidated as (
    select
        c.customer_id,
        c.state,
        c.customer_zip,

        -- ── RECÊNCIA (R) ────────────────────────────────────────────────────
        lm.last_order_date,
        lm.first_order_date,
        lm.days_since_last_order,
        lm.customer_lifespan_days,

        -- ── FREQUÊNCIA (F) ──────────────────────────────────────────────────
        lm.total_orders,
        lm.active_months,
        COALESCE(pc.avg_days_between_orders, 0)         as avg_days_between_orders,

        -- ── MONETÁRIO (M) ───────────────────────────────────────────────────
        lm.lifetime_revenue_brl,
        lm.avg_order_value_brl,
        lm.max_order_value_brl,

        -- ── JANELAS TEMPORAIS ───────────────────────────────────────────────
        COALESCE(wm.orders_last_30d,  0)                as orders_last_30d,
        COALESCE(wm.revenue_last_30d, 0)                as revenue_last_30d,
        COALESCE(wm.orders_last_60d,  0)                as orders_last_60d,
        COALESCE(wm.revenue_last_60d, 0)                as revenue_last_60d,
        COALESCE(wm.orders_last_90d,  0)                as orders_last_90d,
        COALESCE(wm.revenue_last_90d, 0)                as revenue_last_90d,

        -- ── TENDÊNCIA DE RECEITA ─────────────────────────────────────────────
        -- > 1.0 = crescendo | < 1.0 = caindo | null = sem histórico suficiente
        COALESCE(wm.revenue_last_4w, 0)                 as revenue_last_4w,
        COALESCE(wm.revenue_prev_4w, 0)                 as revenue_prev_4w,
        CASE
            WHEN COALESCE(wm.revenue_prev_4w, 0) = 0 THEN NULL
            ELSE CAST(ROUND(
                (wm.revenue_last_4w - wm.revenue_prev_4w) / wm.revenue_prev_4w * 100
            , 1) AS FLOAT64)

        END                                             as revenue_trend_pct,

        -- ── DEVOLUÇÕES ──────────────────────────────────────────────────────
        COALESCE(rm.total_returned_orders, 0)           as total_returned_orders,
        COALESCE(rm.total_returned_brl,    0)           as total_returned_brl,
        CASE
            WHEN lm.total_orders = 0 THEN 0
            ELSE LEAST(ROUND(
                COALESCE(rm.total_returned_orders, 0) / lm.total_orders * 100
            , 1), 100.0)
        END                                             as return_rate_pct,

        -- ── STATUS DE ATIVIDADE ─────────────────────────────────────────────
        CASE
            WHEN lm.days_since_last_order <= 30  THEN 'active'
            WHEN lm.days_since_last_order <= 60  THEN 'at_risk'
            WHEN lm.days_since_last_order <= 90  THEN 'churning'
            ELSE                                      'churned'
        END                                             as activity_status,

        -- ── FLAGS ───────────────────────────────────────────────────────────
        lm.total_orders > 1                             as is_repeat_customer,
        lm.days_since_last_order <= 30                  as is_active_30d,
        COALESCE(rm.total_returned_orders, 0) > 0       as has_returned

    from customers c
    left join lifetime_metrics  lm using (customer_id)
    left join windowed_metrics  wm using (customer_id)
    left join return_metrics    rm using (customer_id)
    left join purchase_cadence  pc using (customer_id)

    -- Só clientes que já fizeram ao menos 1 pedido entregue
    where lm.customer_id is not null
),

-- ----------------------------------------------------------------------------
-- BLOCO 9 — Segmentação RFM simplificada
-- Baseada nos percentis da base para ser auto-ajustável ao longo do tempo.
-- Looker Studio pode filtrar/colorir por segment diretamente.
-- ----------------------------------------------------------------------------
rfm_percentiles as (
    select
        APPROX_QUANTILES(days_since_last_order, 4)[OFFSET(1)]   as r_p25,
        APPROX_QUANTILES(days_since_last_order, 4)[OFFSET(3)]   as r_p75,
        APPROX_QUANTILES(total_orders,           4)[OFFSET(1)]  as f_p25,
        APPROX_QUANTILES(total_orders,           4)[OFFSET(3)]  as f_p75,
        APPROX_QUANTILES(lifetime_revenue_brl,   4)[OFFSET(1)]  as m_p25,
        APPROX_QUANTILES(lifetime_revenue_brl,   4)[OFFSET(3)]  as m_p75
    from consolidated
),

final as (
    select
        c.*,

        -- Score R: 3 = mais recente, 1 = mais antigo
        CASE
            WHEN c.days_since_last_order <= p.r_p25 THEN 3
            WHEN c.days_since_last_order <= p.r_p75 THEN 2
            ELSE 1
        END                                                     as r_score,

        -- Score F: 3 = mais frequente
        CASE
            WHEN c.total_orders >= p.f_p75 THEN 3
            WHEN c.total_orders >= p.f_p25 THEN 2
            ELSE 1
        END                                                     as f_score,

        -- Score M: 3 = maior valor
        CASE
            WHEN c.lifetime_revenue_brl >= p.m_p75 THEN 3
            WHEN c.lifetime_revenue_brl >= p.m_p25 THEN 2
            ELSE 1
        END                                                     as m_score,

        -- Segmento final — 5 categorias legíveis no BI
        CASE
            WHEN c.days_since_last_order <= p.r_p25
             AND c.total_orders          >= p.f_p75
             AND c.lifetime_revenue_brl  >= p.m_p75 THEN 'champions'

            WHEN c.days_since_last_order <= p.r_p25
             AND c.total_orders          >= p.f_p25                 THEN 'loyal'

            WHEN c.days_since_last_order <= p.r_p25
             AND c.total_orders = 1                                 THEN 'new_customer'

            WHEN c.days_since_last_order BETWEEN p.r_p25 AND p.r_p75
             AND c.lifetime_revenue_brl  >= p.m_p25                 THEN 'promising'

            WHEN c.days_since_last_order > p.r_p75
             AND c.total_orders          >= p.f_p25                 THEN 'at_risk'

            ELSE 'lost'
        END                                                     as segment,

        -- Timestamp de geração para auditoria no BI
        CURRENT_TIMESTAMP()                                     as _generated_at

    from consolidated c
    cross join rfm_percentiles p
)

select * from final
