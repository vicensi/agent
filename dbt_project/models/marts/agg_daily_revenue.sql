-- Agregada de receita por dia x canal — fonte principal dos dashboards
-- de receita no Looker Studio.

with orders as (

    select * from {{ ref('fct_orders') }}

)

select
    created_date,
    channel,
    count(*)                    as total_orders,
    sum(total_amount_brl)       as total_revenue_brl,
    sum(delivered_revenue_brl)  as delivered_revenue_brl,
    sum(return_amount_brl)      as return_amount_brl,
    sum(net_revenue_brl)        as net_revenue_brl,
    round(avg(total_amount_brl), 2) as avg_ticket_brl
from orders
group by created_date, channel
