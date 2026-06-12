# Setup GCP — BigQuery + service account `agent_readonly`

Passo a passo para quando o projeto GCP estiver disponível. Nenhum destes
comandos foi executado ainda — o projeto dbt usa `env_var()` e está pronto
para apontar para qualquer projeto.

## 1. Variáveis de ambiente

```bash
export GCP_PROJECT_ID="<seu-projeto>"
export GCP_KEYFILE_PATH="<caminho>/dev_key.json"
export GCP_KEYFILE_PROD_PATH="<caminho>/prod_key.json"
```

## 2. Criar os datasets

```bash
bq mk --location=southamerica-east1 --dataset "${GCP_PROJECT_ID}:raw"
bq mk --location=southamerica-east1 --dataset "${GCP_PROJECT_ID}:staging"
bq mk --location=southamerica-east1 --dataset "${GCP_PROJECT_ID}:marts"
```

## 3. Service account `agent_readonly` (somente leitura nos marts)

```bash
# 1. Criar service account
gcloud iam service-accounts create agent-readonly \
  --display-name="Agent Readonly — Looker Studio / Analytics" \
  --project="${GCP_PROJECT_ID}"

# 2. Exportar email da SA
export SA_EMAIL="agent-readonly@${GCP_PROJECT_ID}.iam.gserviceaccount.com"

# 3. jobUser no projeto (necessário para executar queries)
gcloud projects add-iam-policy-binding "${GCP_PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/bigquery.jobUser"

# 4. dataViewer APENAS no dataset marts
bq add-iam-policy-binding \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/bigquery.dataViewer" \
  "${GCP_PROJECT_ID}:marts"

# 5. Garantir que raw e staging NÃO têm esse binding (default já é fechado)
bq get-iam-policy "${GCP_PROJECT_ID}:raw"
bq get-iam-policy "${GCP_PROJECT_ID}:staging"

# 6. Gerar chave JSON (secrets/ está no .gitignore — nunca versionar)
mkdir -p ./secrets
gcloud iam service-accounts keys create ./secrets/agent_readonly_key.json \
  --iam-account="${SA_EMAIL}"
```

> Nota: o acesso do `agent_readonly` aos marts é concedido por IAM binding no
> dataset (passo 4), não pela config `grant_access_to` do dbt — essa config
> serve para authorized views, não para conceder SELECT em tabelas.

## 4. Validar e rodar o pipeline

```bash
source ../.venv/bin/activate
dbt debug              # valida conexão
dbt deps               # instala packages
dbt seed               # carrega o CSV no dataset raw (~192k linhas, pode demorar)
dbt run                # staging (views) + marts (tables)
dbt test               # testes de qualidade
dbt docs generate      # documentação
```

## 5. Conectar Looker Studio com `agent_readonly`

1. No Looker Studio: **Add data** → **BigQuery**
2. Escolher **Service Account** como método de autenticação
3. Upload do `agent_readonly_key.json`
4. Navegar para `project > marts > fct_orders` / `agg_daily_revenue`
   (raw e staging não aparecem — sem permissão)

**Nunca** conectar Looker Studio (ou qualquer analista) aos datasets `raw` ou `staging`.
