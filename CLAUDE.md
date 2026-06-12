# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Projeto

Pipeline ELT de analytics de e-commerce: **dbt Core + BigQuery + Looker Studio**.
A especificação completa da arquitetura está em `prompt.md` (fonte das convenções).

### Arquitetura — IMPLEMENTAÇÃO ATUAL (Seeds)

```
CSV (dbt_project/seeds/seed_ecommerce_sintetico.csv)
 ↓ dbt seed
BigQuery dataset: raw        (espelho fiel, zero transformação)
 ↓ dbt run
BigQuery dataset: staging    (views — dedup, casts, renames, sanidade)
 ↓
BigQuery dataset: marts      (tables — joins, métricas de negócio)
 ↓
Looker Studio                (conecta SOMENTE nos marts, via SA agent_readonly)
```

**ROADMAP FUTURO (não implementar sem pedido explícito):** PostgreSQL, Airbyte/CDC/Debezium,
Docker, modelos incrementais, Airflow/Dagster, GitHub Actions, Evidently, MLOps.
Os marts não devem mudar quando a ingestão migrar de Seeds para CDC.

## Estrutura

```
dbt_project/
├── dbt_project.yml          # schemas: raw (seeds), staging (views), marts (tables)
├── packages.yml             # dbt_utils + dbt_expectations
├── profiles.yml             # gitignored — env_var(GCP_PROJECT_ID, GCP_KEYFILE_PATH...)
├── seeds/seed_ecommerce_sintetico.csv   # ~192k linhas, denormalizado, com armadilhas
├── models/
│   ├── raw/sources.yml      # source seed_raw (staging lê só via source())
│   ├── staging/stg_ecommerce__orders.sql
│   └── marts/               # fct_orders, dim_customers, dim_products, agg_daily_revenue
├── tests/assert_revenue_non_negative.sql
├── docs/metrics.md          # blocos {% docs %} das métricas
├── docs/setup_gcp.md        # criação dos datasets + SA agent_readonly
└── macros/                  # generate_schema_name (schemas exatos), normalize_estado
```

Raiz: `gerar_dataset_ecommerce.py` (gerador do CSV sintético), `prompt.md` (spec),
`requirements.txt`, `.venv/` (gitignored).

## Comandos

Sempre usar o ambiente virtual:

```bash
source .venv/bin/activate
cd dbt_project
```

```bash
dbt deps                  # instalar packages
dbt parse                 # validar projeto sem conexão (única validação possível sem GCP)
dbt seed                  # carregar CSV no raw (lento: ~192k linhas)
dbt seed --full-refresh   # recarga
dbt run                   # staging + marts
dbt test                  # testes de qualidade
dbt run --select staging  # só uma camada
dbt run --select +fct_orders
dbt docs generate && dbt docs serve --port 8080
```

**GCP ainda não configurado** — `dbt debug/seed/run/test` requerem as env vars
`GCP_PROJECT_ID` e `GCP_KEYFILE_PATH` (ver `.env.example` e `dbt_project/docs/setup_gcp.md`).

## Convenções obrigatórias

- **Camadas**: staging = view (dedup, cast, rename, sanidade — sem joins/negócio);
  marts = table (joins, métricas). Marts referenciam staging via `ref()` — **nunca** `source()`.
- **Nomes**: `stg_<fonte>__<tabela>`, `fct_<entidade>`, `dim_<entidade>`, `agg_<gran>_<métrica>`.
  Colunas em inglês snake_case; valores monetários com sufixo `_brl`.
- **Testes mínimos**: PK → `not_null` + `unique`; FK → `relationships`;
  status/categoria → `accepted_values`; monetários → `dbt_utils.accepted_range` (min 0).
  Status válidos: `aprovado, enviado, entregue, cancelado, aguardando_pagamento`.
- **Docs**: `description` em todo mart, PK, FK e métrica; métricas complexas em
  `docs/metrics.md` com `{% docs %}` e referenciadas via `{{ doc(...) }}`.
- **Segurança**: nenhuma credencial hardcoded (tudo `env_var()`); `profiles.yml`,
  `secrets/` e `*key.json` gitignored; Looker Studio/analistas só enxergam `marts`
  via SA `agent_readonly` (`docs/setup_gcp.md`).

## Dados — armadilhas conhecidas da fonte (tratadas no staging)

O CSV sintético contém problemas propositais (ver `gerar_dataset_ecommerce.py`):
duplicatas exatas e quasi-duplicatas de `order_id`; timestamps com fusos misturados
e naive (staging usa `SAFE_CAST AS TIMESTAMP`, naive = UTC); estados com grafias sujas
(macro `normalize_estado`); `desconto_pct > 100` (→ NULL); quantidade negativa em
devoluções (→ `ABS`); `valor_devolucao > valor_total` (capado com `LEAST` no fct);
devoluções cruzando o mês (`is_cross_month_return`).

## Manutenção deste arquivo

Atualizar este CLAUDE.md sempre que houver: nova tabela/mart, novo teste,
nova convenção, mudança de arquitetura, novo package ou alteração de segurança.
