{% docs delivered_revenue_brl %}
Receita realizada, considerando apenas pedidos com `status = 'entregue'`.
Pedidos cancelados, aguardando pagamento ou em trânsito não entram neste valor.
Fórmula: `SUM(total_amount_brl) WHERE status = 'entregue'`
{% enddocs %}

{% docs net_revenue_brl %}
Receita líquida do pedido: valor total menos o valor devolvido.
O valor devolvido é limitado ao valor do pedido (`LEAST(valor_devolucao, valor_total)`),
pois a fonte contém devoluções registradas acima do total — nunca pode ser negativa.
Fórmula: `total_amount_brl - LEAST(return_amount_brl, total_amount_brl)`
{% enddocs %}
