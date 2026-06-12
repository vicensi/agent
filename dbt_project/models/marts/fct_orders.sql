{{ config(
    cluster_by = ['created_date']
) }}

-- Fato de pedidos: 1 linha por order_id.
-- ROADMAP FUTURO (CDC/Airbyte): materializar como incremental
-- (unique_key='order_id', incremental_strategy='merge') quando a fonte
-- tiver updated_at; com seed a recarga é sempre full.

with orders as (

    select * from {{ ref('stg_ecommerce__orders') }}

),

customers as (

    select * from {{ ref('dim_customers') }}

),

final as (

    select
        o.order_id,
        o.customer_id,
        c.customer_state,
        o.product_name,
        o.category,
        o.status,
        o.channel,
        o.payment_method,
        o.quantity,
        o.unit_price_brl,
        o.discount_pct,
        o.gross_amount_brl,
        o.total_amount_brl,
        o.review_score,

        date(o.created_at)                          as created_date,
        date_trunc(date(o.created_at), month)       as created_month,

        case
            when o.status = 'entregue' then o.total_amount_brl
            else 0
        end                                         as delivered_revenue_brl,

        o.has_return,
        date(o.returned_at)                         as returned_date,

        -- devolução não pode exceder o valor do pedido (armadilha da fonte)
        case
            when o.has_return
                then least(coalesce(o.return_amount_brl, 0), o.total_amount_brl)
            else 0
        end                                         as return_amount_brl,

        o.total_amount_brl
            - case
                when o.has_return
                    then least(coalesce(o.return_amount_brl, 0), o.total_amount_brl)
                else 0
              end                                   as net_revenue_brl,

        o.has_return
            and o.returned_at is not null
            and date_trunc(date(o.returned_at), month)
                != date_trunc(date(o.created_at), month)
                                                    as is_cross_month_return

    from orders o
    left join customers c
        using (customer_id)

)

select * from final
