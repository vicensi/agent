-- Receita líquida nunca pode ser negativa: a devolução é limitada
-- ao valor do pedido no fct_orders. Falha se alguma linha violar.

select
    order_id,
    total_amount_brl,
    return_amount_brl,
    net_revenue_brl
from {{ ref('fct_orders') }}
where net_revenue_brl < 0
