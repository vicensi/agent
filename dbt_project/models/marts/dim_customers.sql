-- Dimensão de clientes derivada dos pedidos (fonte denormalizada não tem
-- cadastro de clientes). 1 linha por customer_id; estado/CEP do pedido mais recente.

with orders as (

    select * from {{ ref('stg_ecommerce__orders') }}

),

latest_order as (

    select
        customer_id,
        customer_state,
        customer_zip,
        row_number() over (
            partition by customer_id
            order by created_at desc
        ) as _row_num
    from orders

),

aggregated as (

    select
        customer_id,
        min(created_at)                         as first_order_at,
        max(created_at)                         as last_order_at,
        count(*)                                as total_orders,
        sum(total_amount_brl)                   as lifetime_value_brl,
        countif(has_return)                     as total_returns
    from orders
    group by customer_id

)

select
    a.customer_id,
    l.customer_state,
    l.customer_zip,
    a.first_order_at,
    a.last_order_at,
    a.total_orders,
    a.lifetime_value_brl,
    a.total_returns
from aggregated a
left join latest_order l
    on a.customer_id = l.customer_id
    and l._row_num = 1
