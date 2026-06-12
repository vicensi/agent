-- Dimensão de produtos derivada dos pedidos (fonte denormalizada não tem
-- catálogo). 1 linha por product_name.

with orders as (

    select * from {{ ref('stg_ecommerce__orders') }}
    where product_name is not null

)

select
    product_name,
    max(category)            as category,
    min(unit_price_brl)      as min_unit_price_brl,
    round(avg(unit_price_brl), 2) as avg_unit_price_brl,
    max(unit_price_brl)      as max_unit_price_brl,
    sum(quantity)            as total_units_sold,
    count(*)                 as total_orders,
    sum(total_amount_brl)    as total_revenue_brl
from orders
group by product_name
