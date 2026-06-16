# Looker Studio Dashboard — Blueprint Completo
**Projeto**: Agentic Data Platform — E-Commerce Analytics  
**Data**: 2026-06-16  
**Fontes**: `fct_orders`, `agg_daily_revenue`, `dim_customers_kpis`, `dim_products`

---

## Estrutura do Relatório: 3 Páginas

| Página | Nome | Fonte principal | Foco |
|---|---|---|---|
| 1 | Visão Geral de Receita | `agg_daily_revenue` + `fct_orders` | KPIs de receita + evolução temporal |
| 2 | Clientes & RFM | `dim_customers_kpis` | Segmentação, LTV, atividade |
| 3 | Produtos & Operacional | `fct_orders` + `dim_products` | Categorias, status, review |

---

## Página 1 — Visão Geral de Receita

**Fonte**: `agg_daily_revenue` (primária) + `fct_orders` (para métricas de pedido)

### Configuração da página
- Renomear: "Visão Geral"
- Filtro de data padrão: Inserir → Controle → Intervalo de datas
  - Campo: `created_date`
  - Valor padrão: Últimos 90 dias (ou período total dos dados)

### Componentes

#### Faixa de KPI Cards (topo — 4 cartões lado a lado)
> Inserir → Gráfico → Cartão de Pontuação

| Cartão | Métrica | Campo | Formato |
|---|---|---|---|
| 1 | Receita Entregue | `SUM(delivered_revenue_brl)` | Moeda BRL, 0 casas |
| 2 | Receita Líquida | `SUM(net_revenue_brl)` | Moeda BRL, 0 casas |
| 3 | Total de Pedidos | `SUM(total_orders)` | Número inteiro |
| 4 | Ticket Médio | Campo calculado ⚠️ | Moeda BRL, 2 casas |

> ⚠️ **Cartão 4 — Ticket Médio**: `avg_ticket_brl` é não-aditivo (pré-calculado por dia+canal).
> Criar campo calculado: **Recurso → Gerenciar campos calculados → Adicionar campo**
> Nome: `Ticket Médio` / Fórmula: `SUM(total_revenue_brl) / SUM(total_orders)`

#### Gráfico de Linha — Evolução Diária de Receita
> Inserir → Gráfico → Gráfico de linhas

- **Dimensão**: `created_date`
- **Granularidade**: Semana (melhor que dia para visualização)
- **Métricas**:
  - `SUM(delivered_revenue_brl)` → linha 1 (azul)
  - `SUM(net_revenue_brl)` → linha 2 (verde)
  - `SUM(return_amount_brl)` → linha 3 (vermelho)
- **Classificar por**: `created_date` crescente
- **Período de comparação**: Ativar "Comparar com período anterior"

#### Gráfico de Barras Empilhadas — Receita por Canal
> Inserir → Gráfico → Gráfico de barras

- **Dimensão**: `channel`
- **Métricas**:
  - `SUM(delivered_revenue_brl)`
  - `SUM(return_amount_brl)`
- **Tipo**: Barras empilhadas
- **Paleta**: Azul / Vermelho

#### Tabela — Top 10 Dias por Receita
> Inserir → Gráfico → Tabela

- **Dimensão**: `created_date`
- **Métricas**: `SUM(delivered_revenue_brl)`, `SUM(total_orders)`, `SUM(total_revenue_brl) / SUM(total_orders)` (campo calculado Ticket Médio)
- **Classificar por**: `delivered_revenue_brl` decrescente
- **Número de linhas**: 10

#### Controles de Filtro (canto superior direito)
- Controle de período: já descrito acima
- Controle de lista (canal): `channel` — multi-seleção

---

## Página 2 — Clientes & RFM

**Fonte**: `dim_customers_kpis`

> **Configurar fonte**: Adicionar dados → BigQuery → `dim_customers_kpis`

### Componentes

#### KPI Cards (topo — 4 cartões)

| Cartão | Métrica | Campo | Fórmula | Fonte |
|---|---|---|---|---|
| 1 | Total de Clientes | Record Count | `COUNT(customer_id)` | `dim_customers_kpis` |
| 2 | Clientes por Segmento | Filtro por `segment` no gráfico de pizza | — | `dim_customers_kpis` |
| 3 | LTV Médio | `AVG(lifetime_revenue_brl)` | Moeda BRL | `dim_customers_kpis` |
| 4 | Ticket Médio (global) | Campo calculado ⚠️ | Moeda BRL | `fct_orders` |

> ⚠️ **Cartão 2**: `is_active_30d` usa `CURRENT_DATE()` — com dados históricos (fim em 2024-12-30)
> todos os clientes terão `is_active_30d = FALSE`. Substituído por distribuição de segmentos.
>
> ⚠️ **Cartão 4**: `avg_order_value_brl` de `dim_customers_kpis` é não-aditivo (avg por cliente).
> Usar fonte `fct_orders` com campo calculado: `SUM(total_amount_brl) / COUNT(order_id)`

#### Gráfico de Pizza — Distribuição de Segmentos RFM
> Inserir → Gráfico → Gráfico de pizza

- **Dimensão**: `segment`
- **Métrica**: `COUNT(customer_id)` (Record Count)
- **Legenda**: Direita
- Valores de `segment` esperados: `champions`, `loyal`, `new_customer`, `promising`, `at_risk`, `lost`

**Dica de cores sugeridas:**
- champions → #1a7f37 (verde escuro)
- loyal → #2da44e (verde)
- new_customer → #0969da (azul)
- promising → #bf8700 (amarelo)
- at_risk → #cf4f00 (laranja)
- lost → #cf222e (vermelho)

#### Gráfico de Barras — LTV Médio por Segmento
> Inserir → Gráfico → Gráfico de barras horizontais

- **Dimensão**: `segment`
- **Métricas**: `AVG(lifetime_revenue_brl)`, `AVG(avg_order_value_brl)`
- **Classificar por**: `AVG(lifetime_revenue_brl)` decrescente

#### Gráfico de Dispersão — RFM Score
> Inserir → Gráfico → Gráfico de dispersão

- **Dimensão**: `segment`
- **Eixo X**: `AVG(r_score)` (Recência)
- **Eixo Y**: `AVG(m_score)` (Monetário)
- **Tamanho da bolha**: `AVG(f_score)` (Frequência)
- Isso mostra visualmente onde cada segmento está no espaço RFM

#### Gráfico de Colunas — LTV Total e Médio por Segmento
> Inserir → Gráfico → Gráfico de colunas agrupadas

- **Dimensão**: `segment`
- **Métricas**:
  - `SUM(lifetime_revenue_brl)` → coluna 1 (receita total do segmento)
  - `AVG(lifetime_revenue_brl)` → coluna 2 (LTV médio por cliente)
- **Classificar por**: `SUM(lifetime_revenue_brl)` decrescente

> ~~Receita por Cohort (30d/60d/90d)~~: removido — `revenue_last_30d/60d/90d` usam
> `CURRENT_DATE()` e retornam 0 com dados históricos. Para análise de período,
> use filtro de data interativo sobre `agg_daily_revenue` na Página 1.

#### Tabela — Top Clientes por LTV
> Inserir → Gráfico → Tabela

- **Dimensões**: `customer_id`, `state`, `segment`, `activity_status`
- **Métricas**: `lifetime_revenue_brl`, `avg_order_value_brl`, `days_since_last_order`, `return_rate_pct`
- **Classificar por**: `lifetime_revenue_brl` decrescente
- **Número de linhas**: 20
- **Barras de dados**: Ativar em `lifetime_revenue_brl`

#### Mapa de Calor — Receita por Estado
> Inserir → Gráfico → Gráfico de mapa — Mapa preenchido

- **Dimensão**: `state`
- **Métrica**: `SUM(lifetime_revenue_brl)`
- **Tipo de localização**: Estado (Brasil)
- **Paleta**: Branco → Azul

---

## Página 3 — Produtos & Operacional

**Fontes**: `fct_orders` (primária) + `dim_products` (join)

### Componentes

#### KPI Cards (topo — 4 cartões) — fonte: `fct_orders`

| Cartão | Métrica | Campo |
|---|---|---|
| 1 | Pedidos Entregues | `COUNTIF(status, "entregue")` |
| 2 | Taxa de Cancelamento | Campo calculado: `COUNTIF(status,"cancelado") / COUNT(order_id) * 100` |
| 3 | Taxa de Devolução | `AVG(has_return) * 100` → converte bool para % |
| 4 | Review Score Médio | `AVG(review_score)` |

#### Gráfico de Barras — Receita por Categoria de Produto
> Usar `dim_products` como fonte

> Inserir → Gráfico → Gráfico de barras horizontais

- **Dimensão**: `category`
- **Métricas**: `SUM(total_revenue_brl)`, `SUM(total_units_sold)`
- **Classificar por**: `total_revenue_brl` decrescente

#### Gráfico de Barras — Top 15 Produtos por Receita
> Usar `dim_products` como fonte

- **Dimensão**: `product_name`
- **Métrica**: `SUM(total_revenue_brl)`
- **Classificar por**: `total_revenue_brl` decrescente
- **Número de linhas**: 15

#### Gráfico de Pizza — Distribuição de Status de Pedidos
> Usar `fct_orders` como fonte

- **Dimensão**: `status`
- **Métrica**: `COUNT(order_id)`
- Valores: `entregue`, `enviado`, `cancelado`, `aguardando_pagamento`, `aprovado`

#### Gráfico de Barras — Receita por Método de Pagamento
> Usar `fct_orders` como fonte

- **Dimensão**: `payment_method`
- **Métricas**: `SUM(delivered_revenue_brl)`, `COUNT(order_id)`
- **Tipo**: Barras duplas agrupadas

#### Gráfico de Linha — Evolução do Review Score
> Usar `fct_orders` como fonte

- **Dimensão**: `created_date` (granularidade: Mês)
- **Métrica**: `AVG(review_score)`
- **Intervalo Y**: 0 a 5
- **Linha de referência**: 4.0 (benchmark de qualidade)

#### Tabela — Pedidos com Devolução no Mês Seguinte
> Usar `fct_orders` — filtrar `is_cross_month_return = true`

- **Dimensões**: `order_id`, `status`, `payment_method`, `created_date`
- **Métricas**: `total_amount_brl`, `return_amount_brl`
- **Filtro fixo no gráfico**: `is_cross_month_return = true`

---

## Campos Calculados Necessários (criar em Recurso → Gerenciar campos calculados)

```
# ── Fonte: agg_daily_revenue ─────────────────────────────────────────────────

# Ticket Médio (substitui avg_ticket_brl — não-aditivo)
Ticket Médio = SUM(total_revenue_brl) / SUM(total_orders)

# Taxa de Devolução por período (%)
Taxa de Devolução = SUM(return_amount_brl) / SUM(total_revenue_brl) * 100

# ── Fonte: fct_orders ─────────────────────────────────────────────────────────

# Ticket Médio de pedidos entregues
Ticket Médio Entregue = SUM(delivered_revenue_brl) / COUNTIF(status, "entregue")

# Taxa de Cancelamento (%)
Taxa de Cancelamento = COUNTIF(status, "cancelado") / COUNT(order_id) * 100

# Taxa de Devolução por pedido (%)
Taxa de Devolução (pedidos) = COUNTIF(has_return, TRUE) / COUNT(order_id) * 100
```

> ⚠️ **Removido**: `active_customers_30d = COUNTIF(is_active_30d, TRUE)` — campo baseado
> em `CURRENT_DATE()`, retorna 0 com dados históricos. Para contagem de clientes ativos
> em um período, aplique filtro de data sobre `fct_orders` e use `COUNT(DISTINCT customer_id)`.

---

## Configurações de Tema e Layout

**Tema**: Inserir → Tema e layout
- Fundo: Branco (#FFFFFF)
- Cor primária: #1a56db (azul)
- Cor de acento: #e3a008 (amarelo)
- Fonte: Google Sans ou Roboto

**Cabeçalho de cada página**: Caixa de texto com nome da página + data de atualização automática

---

## Perguntas do Golden Dataset → Visual

| Pergunta (golden_dataset) | Visual no Dashboard | Página |
|---|---|---|
| "Qual a receita líquida total?" | KPI Card Receita Líquida | 1 |
| "Como evoluiu a receita entregue?" | Gráfico de linha temporal | 1 |
| "Qual canal gera mais receita?" | Gráfico de barras por canal | 1 |
| "Quantos clientes ativos nos últimos 30d?" | Filtro de data sobre fct_orders + COUNT(DISTINCT customer_id) | 2 |
| "Qual segmento tem maior LTV?" | Barras LTV por segmento | 2 |
| "Qual a taxa de devolução?" | KPI Card + linha temporal | 1 e 3 |
| "Qual categoria gera mais receita?" | Barras por categoria | 3 |
| "Qual método de pagamento é mais usado?" | Barras payment_method | 3 |
| "Qual o review score médio?" | KPI Card + linha temporal | 3 |
| "Quais produtos lideram em receita?" | Top 15 produtos | 3 |
