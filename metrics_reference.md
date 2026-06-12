# Referência de Métricas — E-commerce

> Documento canônico de definição de métricas de negócio.
> Toda divergência entre dashboards deve ser resolvida consultando este arquivo.
> Última atualização: gerado via dbt project.

---

## Como ler este documento

Cada métrica tem cinco seções:

- **Definição** — o que o número representa em linguagem de negócio
- **Fórmula** — como é calculado tecnicamente (com filtros explícitos)
- **Grão** — o menor nível de detalhe disponível
- **Dimensões** — como pode ser quebrado (filtros e agrupamentos válidos)
- **Armadilhas** — casos onde o número engana e como identificá-los

---

## 1. Receita Entregue (`delivered_revenue`)

**Definição**
Quanto o negócio efetivamente faturou — soma do valor de pedidos que chegaram ao cliente. É a métrica de receita principal para metas de negócio, relatórios financeiros e comparativos de período.

**Fórmula**
```sql
SUM(total_amount_brl)
WHERE status = 'delivered'
```

**Grão**
Dia (`created_date`). Pode ser agregada para semana, mês, trimestre ou ano.

**Dimensões disponíveis**
| Dimensão | Exemplo de uso |
|---|---|
| `state` | Receita por UF para análise regional |
| `channel` | Comparar performance app vs site vs marketplace |
| `created_date` | Série temporal de receita diária/mensal |

**Armadilhas**

1. **Confundir com gross_revenue** — pedidos em trânsito (`shipped`) entram na receita bruta mas não na entregue. Em períodos de alto volume de frete, a diferença pode ser significativa.

2. **Comparar períodos com calendário diferente** — meses têm 28–31 dias. Sempre usar receita por dia útil ou normalizar para 30 dias ao comparar meses diferentes.

3. **Picos de final de mês** — operações de e-commerce concentram entregas no fim do mês para bater metas. Um mês pode ter receita alta por antecipação de entregas do mês seguinte.

---

## 2. Receita Bruta (`gross_revenue`)

**Definição**
Volume financeiro de pedidos comprometidos — inclui pedidos aprovados e em transporte, além dos entregues. Representa o "pipeline de receita" que ainda vai se concretizar (ou não, em caso de cancelamento).

**Fórmula**
```sql
SUM(total_amount_brl)
WHERE status IN ('approved', 'shipped', 'delivered')
```

**Grão**
Dia (`created_date`).

**Dimensões disponíveis**
Mesmas de `delivered_revenue`.

**Armadilhas**

1. **gross_revenue sempre >= delivered_revenue** — a diferença são pedidos ainda em rota. Se a diferença crescer, pode indicar problemas logísticos (pedidos presos em `shipped` por muito tempo).

2. **Nunca somar com delivered_revenue** — os conjuntos se sobrepõem. `delivered` já está dentro de `gross`.

3. **Pedidos cancelados após aprovação** — entram em `gross` no momento da aprovação e saem quando o status muda. Análises históricas de gross_revenue podem incluir pedidos que depois foram cancelados.

---

## 3. Receita Líquida (`net_revenue`)

**Definição**
Receita realizada descontando o valor de devoluções. É o número mais próximo da receita que efetivamente permanece no caixa do negócio.

**Fórmula**
```sql
delivered_revenue - returned_revenue
-- = SUM(total_amount_brl WHERE status = 'delivered')
-- - SUM(total_amount_brl WHERE status = 'returned')
```

**Grão**
Dia (`created_date` do pedido original).

**Dimensões disponíveis**
Mesmas de `delivered_revenue`. Recomenda-se sempre acompanhar junto com `return_rate`.

**Armadilhas**

1. **Devolução cross-month** — um pedido entregue em dezembro pode ser devolvido em janeiro. O `net_revenue` de dezembro calculado em tempo real pode ser maior do que o calculado 45 dias depois, quando devoluções atrasadas entram. Para relatórios financeiros fechados, usar janela de corte de 45 dias após o período.

2. **net_revenue negativo em categorias específicas** — se uma categoria tem taxa de devolução > 100% do volume entregue no período (por devoluções de meses anteriores), net_revenue pode ficar negativo. Não é erro — é sinal de problema na categoria.

3. **Não usar como meta mensal sem ajuste de lag** — o número muda retroativamente conforme devoluções chegam. Metas de net_revenue precisam de janela de estabilização.

---

## 4. Receita Devolvida (`returned_revenue`)

**Definição**
Valor total de pedidos que foram devolvidos. Indica o impacto financeiro direto das devoluções — quanto saiu do caixa de volta para o cliente.

**Fórmula**
```sql
SUM(total_amount_brl)
WHERE status = 'returned'
```

**Grão**
Dia (`created_date` do pedido original). Note que a *data da devolução* pode ser diferente da data do pedido.

**Dimensões disponíveis**
| Dimensão | Exemplo de uso |
|---|---|
| `state` | Regiões com maior problema de devolução |
| `channel` | Marketplace com taxa de devolução maior que próprio site |

**Armadilhas**

1. **Data do pedido vs data da devolução** — este modelo usa a data do pedido como referência. Para analisar *quando* a devolução aconteceu (operacional, logístico), você precisaria de um campo `returned_at` na fonte — que pode não existir dependendo do sistema de origem.

2. **Devolução parcial** — alguns sistemas registram devoluções parciais (item específico de um pedido multi-item). Verifique se `total_amount_brl` na tabela de devoluções representa o valor total ou parcial do pedido.

---

## 5. Pedidos Entregues (`delivered_orders`)

**Definição**
Contagem de pedidos únicos que chegaram ao cliente. É o denominador correto para calcular ticket médio e taxa de devolução — use sempre este número, não `order_count` total.

**Fórmula**
```sql
COUNT(DISTINCT order_id)
WHERE status = 'delivered'
```

**Grão**
Pedido individual (`order_id`). Pode ser agregado por qualquer dimensão temporal ou categórica.

**Dimensões disponíveis**
Todas: `state`, `channel`, `created_date`.

**Armadilhas**

1. **Usar `order_count` total como denominador** — erro clássico ao calcular ticket médio ou return_rate. `order_count` inclui cancelados e pendentes, inflando o denominador e distorcendo as taxas.

2. **Pedidos multi-item** — cada `order_id` pode ter vários produtos. `delivered_orders` conta pedidos, não itens. Para análise de produto, usar uma tabela de `order_items` com granularidade de linha.

---

## 6. Ticket Médio (`avg_order_value`)

**Definição**
Valor médio de um pedido entregue. Indica o poder de compra médio por transação e é proxy da capacidade de upsell/cross-sell do negócio.

**Fórmula**
```sql
delivered_revenue / NULLIF(delivered_orders, 0)
-- = SUM(total_amount_brl WHERE delivered) / COUNT(DISTINCT order_id WHERE delivered)
```

**Grão**
Não tem sentido no grão de pedido individual (seria só o valor do pedido). Mínimo útil: semana ou mês.

**Dimensões disponíveis**
| Dimensão | Insight típico |
|---|---|
| `channel` | App tende a ter ticket maior que marketplace |
| `state` | Regiões com poder aquisitivo diferente |
| `created_date` (mês) | Sazonalidade do ticket |

**Armadilhas**

1. **Outliers B2B** — um pedido corporativo de R$ 50k eleva a média do mês inteiro. Sempre verificar distribuição (mediana vs média) quando o ticket médio variar abruptamente.

2. **Descontos distorcendo a tendência** — campanhas de desconto reduzem o ticket médio. Uma queda no ticket médio durante Black Friday é esperada, não problemática. Compare sempre com volume de pedidos.

3. **Ticket médio alto com volume baixo** — pode indicar categoria premium com baixa conversão, não crescimento saudável. Sempre analisar junto com `delivered_orders`.

---

## 7. Clientes Ativos 30d (`active_customers_30d`)

**Definição**
Clientes distintos que realizaram ao menos 1 compra entregue nos últimos 30 dias corridos a partir da data de análise. Indica o tamanho da base engajada de curto prazo.

**Fórmula**
```sql
COUNT(DISTINCT customer_id)
WHERE status = 'delivered'
  AND created_date >= CURRENT_DATE - 30
```

**Grão**
Cliente (`customer_id`). Métrica de snapshot — varia a cada dia conforme janela de 30 dias desliza.

**Dimensões disponíveis**
| Dimensão | Exemplo de uso |
|---|---|
| `state` | Clientes ativos por região |
| `channel` | Retenção por canal de aquisição |

**Armadilhas**

1. **Queda abrupta no dia 31** — clientes que compraram exatamente 31 dias atrás saem da métrica de um dia para o outro. Para monitoramento operacional, usar janela de 60 ou 90 dias ou média móvel de 7 dias de `active_customers_30d`.

2. **Não é cohort** — um cliente que comprou no dia 1 e outro que comprou no dia 29 ambos entram na mesma métrica. Para entender retenção real, usar análise de cohort por mês de primeira compra.

3. **Crescimento artificial** — promoções agressivas podem inflar `active_customers_30d` com clientes de baixa qualidade que não retornam. Acompanhar junto com `avg_order_value` e `return_rate`.

---

## 8. Taxa de Devolução (`return_rate`)

**Definição**
Percentual de pedidos que foram devolvidos sobre o total de pedidos entregues. Indica qualidade do produto, precisão da descrição e satisfação pós-compra.

**Fórmula**
```sql
(COUNT(DISTINCT order_id WHERE returned) 
 / NULLIF(COUNT(DISTINCT order_id WHERE delivered), 0)) * 100
```

**Grão**
Mínimo útil: mês. Em janelas menores, o lag das devoluções distorce muito o número.

**Dimensões disponíveis**
| Dimensão | Insight típico |
|---|---|
| `channel` | Marketplace costuma ter return_rate maior (produto não corresponde à foto) |
| `state` | Regiões com maior tempo de entrega tendem a ter mais devoluções |
| `created_date` (mês) | Sazonalidade — pós-Natal tem pico de devoluções |

**Armadilhas**

1. **Lag de 30–45 dias** — a taxa calculada para o mês atual está incompleta. Devoluções de compras feitas agora chegam em 2–6 semanas. Para análise de return_rate confiável, usar meses fechados com pelo menos 45 dias de lag.

2. **Denominador errado** — usar `order_count` total (inclui cancelados) em vez de `delivered_orders` subestima a taxa. Um pedido cancelado nunca poderia ser devolvido.

3. **Confundir cancelamento com devolução** — cancelamentos antes da entrega não são devoluções. `return_rate` mede apenas `status = 'returned'`, não `cancelled`. São problemas operacionais diferentes com causas raiz diferentes.

---

## Tabela Resumo

| Métrica | Filtro de status | Grão mínimo útil | Principal armadilha |
|---|---|---|---|
| `delivered_revenue` | `delivered` | Dia | Comparar meses sem normalizar por dias úteis |
| `gross_revenue` | `approved, shipped, delivered` | Dia | Somar com delivered_revenue |
| `net_revenue` | derivada | Mês (com 45d lag) | Devolução cross-month distorce fechamentos |
| `returned_revenue` | `returned` | Mês | Data do pedido ≠ data da devolução |
| `delivered_orders` | `delivered` | Dia | Usar como denominador junto com métricas de receita bruta |
| `avg_order_value` | `delivered` | Semana/Mês | Outliers B2B inflam a média |
| `active_customers_30d` | `delivered` + janela 30d | Snapshot diário | Queda abrupta no dia 31 |
| `return_rate` | `returned / delivered` | Mês (com 45d lag) | Calculado em janelas curtas é sempre subestimado |
