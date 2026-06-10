# Agente de IA para Análise de Negócio com PostgreSQL, dbt e Dataviz

## Visão Geral

Um agente de IA conectado ao ecossistema de dados da empresa pode atuar como um analista virtual, respondendo perguntas de negócio em linguagem natural e gerando insights automaticamente.

### Componentes da Arquitetura

* **PostgreSQL**: Armazena os dados operacionais e históricos.
* **dbt (Data Build Tool)**: Responsável pela transformação dos dados e definição das métricas de negócio.
* **Dataviz (Power BI, Tableau, Superset, Metabase)**: Camada de visualização e monitoramento.
* **LLM (Large Language Model)**: Responsável por interpretar perguntas, consultar dados e gerar respostas.

---

# Caso de Uso 1: Análise de Queda nas Vendas

## Pergunta

> Por que o faturamento caiu 12% em maio?

## O que o agente faz

1. Consulta as métricas de faturamento no dbt.
2. Compara os resultados com períodos anteriores.
3. Identifica regiões, produtos e canais afetados.
4. Gera um resumo executivo.

## Exemplo de Resposta

* Faturamento total caiu 12%.
* Região Sul foi responsável por 65% da queda.
* Produto X teve redução de 18% nas vendas.
* Canal online manteve crescimento de 3%.

## Valor para o Negócio

Permite identificar rapidamente as causas da redução de receita sem necessidade de análises manuais.

---

# Caso de Uso 2: Predição de Churn

## Pergunta

> Quais clientes possuem maior risco de cancelamento?

## O que o agente faz

1. Consulta histórico de compras.
2. Analisa frequência de uso.
3. Verifica tempo desde a última interação.
4. Utiliza modelos preditivos.

## Exemplo de Resposta

* 247 clientes apresentam risco superior a 80%.
* Receita potencial em risco: R$ 320 mil.
* Principal fator: ausência de compras há mais de 90 dias.

## Valor para o Negócio

Permite ações preventivas antes da perda do cliente.

---

# Caso de Uso 3: Investigação da Inadimplência

## Pergunta

> O que explica o aumento da inadimplência nos últimos meses?

## O que o agente faz

1. Analisa contratos e pagamentos.
2. Compara perfis de clientes.
3. Identifica segmentos mais afetados.

## Exemplo de Resposta

* Inadimplência cresceu 7%.
* Clientes entre 18 e 25 anos concentram 40% do aumento.
* Empréstimos acima de R$ 20.000 apresentaram maior deterioração.

## Valor para o Negócio

Auxilia na revisão de políticas de crédito.

---

# Caso de Uso 4: Recomendação de Campanhas

## Pergunta

> Qual campanha possui maior potencial de gerar receita?

## O que o agente faz

1. Analisa histórico de compras.
2. Executa análises de afinidade entre produtos.
3. Identifica oportunidades de cross-sell e upsell.

## Exemplo de Resposta

* Clientes que compram Produto A possuem 68% de probabilidade de adquirir Produto B.
* Potencial estimado de receita adicional: R$ 150 mil.

## Valor para o Negócio

Melhora a eficiência das campanhas de marketing.

---

# Caso de Uso 5: Resumo Executivo Diário

## Pergunta

> Faça um resumo executivo do negócio hoje.

## O que o agente faz

1. Consulta indicadores do dia.
2. Detecta anomalias.
3. Compara com períodos anteriores.
4. Gera um relatório resumido.

## Exemplo de Resposta

### Indicadores do Dia

* Receita: +8,3%
* Novos clientes: +12%
* Ticket médio: -2,1%
* Conversão: +4,5%

### Destaques

* Região Sudeste lidera crescimento.
* Produto Premium foi responsável por 35% da receita.
* Nenhuma anomalia crítica detectada.

## Valor para o Negócio

Permite acompanhamento executivo em tempo real.

---

# Arquitetura Recomendada

```text
Usuário
   │
   ▼
Agente de IA
   │
   ├── PostgreSQL
   │     └── Dados operacionais
   │
   ├── dbt
   │     └── Métricas e camada semântica
   │
   ├── Dataviz
   │     └── Dashboards e KPIs
   │
   ├── Modelos de Machine Learning
   │     └── Previsões e recomendações
   │
   └── Catálogo de Dados
         └── Documentação e governança
```

---

# Exemplo de Pergunta Executiva

> Qual foi a principal causa da queda do lucro nos últimos 15 dias e quais ações devo tomar?

## Fluxo de Resposta

1. O agente consulta métricas no dbt.
2. Analisa dados detalhados no PostgreSQL.
3. Verifica dashboards e indicadores.
4. Detecta padrões históricos.
5. Gera recomendações acionáveis.

## Benefícios

* Democratização do acesso aos dados.
* Menor dependência de times técnicos.
* Redução do tempo de análise.
* Tomada de decisão mais rápida.
* Insights automatizados em linguagem natural.
