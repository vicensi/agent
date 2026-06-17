# Agentic Data Platform — E-Commerce Analytics

Pipeline ELT completo de e-commerce com **agente de IA conversacional** sobre o warehouse. Construído como projeto de estudo para consolidar dbt Core, BigQuery, MCP, FastAPI e LLM tool use num sistema coeso de ponta a ponta.

> **"Fazer uma pergunta em português e receber a resposta com o SQL de origem"** — esse era o objetivo.

---

## O que foi construído

```
CSV sintético (~192k pedidos)
        │
        ▼  dbt seed
BigQuery: raw          ← espelho fiel, zero transformação
        │
        ▼  dbt run (views)
BigQuery: staging      ← dedup, casts, renames, sanidade de dados
        │
        ▼  dbt run (tables)
BigQuery: marts        ← joins, métricas de negócio, RFM, agregações
        │
   ┌────┴────────────────────┐
   │                         │
   ▼                         ▼
MCP Server            FastAPI Agent API
(Claude Desktop)      (POST /ask, /query, /metrics)
                             │
                             ▼
                      Streamlit Chat UI
                      (chat + Plotly inline)
```

**Camadas do warehouse:**

| Modelo | Tipo | Grão | Propósito |
|---|---|---|---|
| `fct_orders` | table | 1 linha / pedido | Fato central com receita, devoluções, status |
| `dim_customers` | table | 1 linha / cliente | Dimensão básica derivada dos pedidos |
| `dim_customers_kpis` | table | 1 linha / cliente | RFM, LTV, segmentação, atividade |
| `dim_products` | table | 1 linha / produto | Receita e volume por produto/categoria |
| `agg_daily_revenue` | table | 1 linha / dia+canal | Receita agregada para dashboards |

---

## Stack

| Camada | Tecnologia |
|---|---|
| Transformação | dbt Core 1.8 + dbt_utils + dbt_expectations |
| Warehouse | Google BigQuery |
| Agente LLM | Anthropic Claude Haiku (tool use) |
| Protocolo IA | MCP (Model Context Protocol) |
| API | FastAPI + Pydantic + uvicorn |
| UI | Streamlit + Plotly |
| Linguagem | Python 3.11 |
| BI | Looker Studio |

---

## Como rodar

**Pré-requisitos:** Python 3.11+, conta GCP com BigQuery, API key Anthropic.

```bash
# 1. Clonar e criar ambiente virtual
git clone <repo>
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Configurar variáveis de ambiente
cp .env.example .env
# Preencher: GCP_PROJECT_ID, GCP_KEYFILE_PATH, ANTHROPIC_API_KEY

# 3. Configurar GCP (ver dbt_project/docs/setup_gcp.md)
# Criar datasets raw, staging, marts + service account agent_readonly

# 4. Carregar dados e rodar transformações
cd dbt_project
dbt deps
dbt seed          # ~192k linhas → BigQuery raw
dbt run           # staging + marts
dbt test          # suite de qualidade

# 5. MCP Server — Claude Desktop
cd mcp_server && python server.py

# 6. FastAPI Agent (outro terminal)
cd agent_api && python main.py     # porta 8001

# 7. Streamlit UI (outro terminal)
streamlit run streamlit_app/app.py
```

---

## Qualidade dos dados

O CSV sintético tem armadilhas propositais — todas tratadas no `staging`:

| Problema | Tratamento |
|---|---|
| Duplicatas exatas e quasi-duplicatas de `order_id` | `ROW_NUMBER()` com dedup por `order_id, created_at` |
| Timestamps com fusos misturados (UTC + naive) | `SAFE_CAST AS TIMESTAMP`, naive → UTC |
| Estados com grafias sujas (`S.Paulo`, `sp`, `São Paulo`) | Macro `normalize_estado` |
| `desconto_pct > 100` | → NULL |
| Quantidade negativa em devoluções | `ABS` |
| `valor_devolucao > valor_total` | `LEAST(valor_devolucao, valor_total)` |
| Devoluções cruzando o mês | Flag `is_cross_month_return` |

**Testes dbt:** `not_null`, `unique`, `relationships`, `accepted_values`, `dbt_utils.accepted_range`, assertion customizada `assert_revenue_non_negative`.

---

## O Agente

O agente usa **tool use** da API da Anthropic — não RAG. A diferença: em vez de buscar texto em documentos, o agente chama funções reais que executam SQL no BigQuery.

**Tools disponíveis:**

| Tool | Quando o agente usa |
|---|---|
| `list_metrics` | Para conhecer métricas disponíveis antes de consultar |
| `query_metric` | Caminho principal — 8 métricas pré-definidas com SQL gerado |
| `run_sql_readonly` | Quando nenhuma métrica cobre a pergunta (com validação de segurança) |
| `get_lineage` | Para entender origem e colunas de um modelo antes de escrever SQL |
| `plot_chart` | Após obter dados — registra spec de gráfico para o Streamlit renderizar |

**Loop agentico:** até 8 iterações. O agente decide quantas tools chamar — não existe chain fixo.

**Segurança em profundidade:**
- `sql_validator.py` bloqueia qualquer coisa que não seja `SELECT/CTE/UNION`
- Schema `marts` obrigatório — staging e raw são inacessíveis
- BigQuery: role `agent_readonly` com `roles/bigquery.dataViewer` restrito

---

## Decisões de arquitetura

**Seeds em vez de CDC agora.** Os marts não sabem de onde vieram os dados — a interface entre staging e marts é puramente `ref()`. Migrar para Airbyte + PostgreSQL no futuro não requer tocar nos marts. A decisão de usar seeds foi explícita, não uma limitação técnica.

**MCP Server + FastAPI como duas superfícies do mesmo núcleo.** O `agent_api/tools.py` importa diretamente `catalog.py`, `bq_client.py` e `query_builder.py` do `mcp_server/`. A lógica de negócio fica em um lugar só. MCP serve o Claude Desktop via stdio; FastAPI serve qualquer cliente HTTP. Em produção, o `agent_api` chamaria o MCP server via HTTP em vez de import direto.

**Claude Haiku em vez de Sonnet.** Para tool use com queries estruturadas, Haiku é rápido, barato e suficientemente preciso. O system prompt faz o trabalho pesado de definir comportamento — o modelo executa.

**Métricas aditivas vs não-aditivas documentadas.** `avg_ticket_brl` em `agg_daily_revenue` parece reutilizável no Looker Studio, mas `AVG(avg_ticket_brl)` é média de médias — resultado errado. Convenção formalizada: campos não-aditivos recebem `meta: {bi_aggregation: "non_additive"}` no schema.yml e instrução de uso no Looker.

**`CURRENT_DATE()` proibido em marts consumidos por BI.** Campos como `revenue_last_30d` em `dim_customers_kpis` usam `CURRENT_DATE()` no momento do `dbt run`. Com dados históricos, esses campos retornam 0 permanentemente. Documentado com `meta: {bi_warning: "CURRENT_DATE() — valor zero com dados históricos"}` e substituído por filtros interativos no Looker.

---

## O que eu faria diferente

**Modelos incrementais desde o início.** `dbt seed --full-refresh` para 192k linhas já é lento para estudo. Com dados reais o `unique_key` e `incremental_strategy` deveriam estar no design inicial.

**Separar mcp_server e agent_api em packages Python independentes.** O import direto via `sys.path.insert` funciona localmente, mas em produção com containers separados, o `mcp_server` deveria ser um package instalável.

**Observabilidade desde o primeiro endpoint.** O logging estruturado existe, mas sem traces distribuídos. Em produção, cada `POST /ask` precisaria de um trace com spans por ferramenta chamada e query executada.

**Testes de contrato dbt em todos os marts.** O `contract: enforced: true` evita breaking changes silenciosos. Adicionei só no `dim_customers_kpis` — deveria ter adicionado em todos desde o início.

**Golden dataset versionado como fixture de CI.** Os evals do agente dependem de um arquivo JSON estático. Em produção, esse dataset deveria rodar automaticamente no PR para detectar regressões de qualidade.

---

## Estrutura do projeto

```
agent-create-v2/
├── dbt_project/
│   ├── models/
│   │   ├── raw/          sources.yml
│   │   ├── staging/      stg_ecommerce__orders.sql
│   │   └── marts/        fct_orders, dim_*, agg_daily_revenue + schemas
│   ├── seeds/            seed_ecommerce_sintetico.csv (~192k linhas)
│   ├── tests/            assert_revenue_non_negative.sql
│   ├── macros/           generate_schema_name, normalize_estado
│   └── docs/             metrics.md, setup_gcp.md, looker_studio_blueprint.md
├── mcp_server/
│   ├── server.py         MCP server (stdio transport)
│   ├── catalog.py        8 métricas de negócio
│   ├── query_builder.py  gerador de SQL parametrizado
│   ├── bq_client.py      cliente BigQuery singleton
│   └── sql_validator.py  validação de segurança para SQL livre
├── agent_api/
│   ├── main.py           FastAPI — /ask, /query, /metrics, /health
│   ├── agent.py          loop agentico (Claude API tool use)
│   ├── tools.py          TOOL_DEFINITIONS + ToolExecutor (5 tools)
│   └── models.py         Pydantic — AskRequest, AskResponse, ChartSpec
├── streamlit_app/
│   └── app.py            Chat UI com Plotly inline + seção de fontes/SQL
└── gerar_dataset_ecommerce.py  gerador do CSV sintético com armadilhas
```
