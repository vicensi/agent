# Exercício — Quebrar um contrato dbt de propósito

> Objetivo: entender na prática o que o contrato protege e como o erro aparece.
> Tempo estimado: 10 minutos.

---

## Setup: garantir que o contrato está funcionando

Com o arquivo `dim_customers_kpis_contract.yml` ativo, rode:

```bash
dbt build --select dim_customers_kpis --target prod
```

Resultado esperado: `1 of 1 OK` — modelo materializa normalmente.

---

## Cenário 1 — Remover uma coluna declarada no contrato

**O que simula:** um desenvolvedor refatora o SQL e remove `revenue_trend_pct`
porque achou que ninguém usava. O contrato impede que isso chegue em produção silenciosamente.

**Passo 1:** no arquivo `dim_customers_kpis.sql`, remova a coluna `revenue_trend_pct` do SELECT final:

```sql
-- ANTES (no bloco final do SELECT):
revenue_trend_pct,

-- DEPOIS: delete essa linha
-- (apenas deletar — não mexer em mais nada)
```

**Passo 2:** rode o build:

```bash
dbt build --select dim_customers_kpis --target prod
```

**Erro esperado:**

```
Compilation Error in model dim_customers_kpis
  
  Contract violated: column revenue_trend_pct declared in schema.yml
  was not found in the model's output.
  
  Columns declared in contract but missing from model:
    - revenue_trend_pct (expected: float64)
  
  To fix: either add the column back to the model SQL,
  or remove it from the contract in schema.yml.
```

**O que o erro está dizendo:** o contrato declara `revenue_trend_pct` com tipo `float64`.
O SQL não retorna essa coluna. O dbt recusou o build — a tabela no BigQuery não foi tocada.

---

## Cenário 2 — Mudar o tipo de uma coluna

**O que simula:** alguém muda `customer_id` de `INT64` para `STRING` no SQL
(ex: adicionou um prefixo como `CONCAT('CUST-', customer_id)`).
O dashboard do Looker Studio espera inteiro — quebra silenciosamente sem contrato.
Com contrato, quebra com mensagem clara antes de chegar no warehouse.

**Passo 1:** no `dim_customers_kpis.sql`, mude o cast de `customer_id`:

```sql
-- ANTES:
c.customer_id,

-- DEPOIS (simula uma "melhoria" mal planejada):
CONCAT('CUST-', CAST(c.customer_id AS STRING))  as customer_id,
```

**Passo 2:** rode o build:

```bash
dbt build --select dim_customers_kpis --target prod
```

**Erro esperado:**

```
Compilation Error in model dim_customers_kpis

  Contract violated: type mismatch for column customer_id.
  
  Expected (from contract): int64
  Got (from model):         string
  
  To fix: either revert the column type in the model SQL,
  or update data_type in schema.yml to 'string' (and validate downstream impact first).
```

**O que o erro está dizendo:** o tipo `string` não é compatível com o contrato `int64`.
O dbt bloqueou antes de materializar. O Looker Studio nunca viu a mudança.

---

## Cenário 3 — Adicionar coluna nova SEM declarar no contrato

**Comportamento diferente:** adicionar uma coluna *a mais* no SQL, sem declará-la no contrato, **não quebra o build** — o contrato é um subconjunto mínimo garantido, não uma lista exaustiva.

**Passo 1:** adicione uma coluna nova no SELECT final do modelo:

```sql
-- Adicionar ao bloco final:
ROUND(lifetime_revenue_brl / NULLIF(customer_lifespan_days, 0), 2)  as revenue_per_day,
```

**Passo 2:** rode o build:

```bash
dbt build --select dim_customers_kpis --target prod
```

**Resultado esperado:** `1 of 1 OK` — build passa normalmente.

**O que isso significa:** a coluna `revenue_per_day` existe no BigQuery mas não está no contrato.
Se alguém usar `revenue_per_day` no Looker Studio e você remover depois sem atualizar o contrato,
o dashboard quebra — mas o dbt não avisa. Para colunas críticas consumidas downstream,
adicione-as ao contrato com `data_type` correto.

---

## Desfazendo as mudanças

Após o exercício, reverta o SQL para o estado original:

```bash
git checkout models/marts/dim_customers_kpis.sql
```

Ou manualmente: restaure `revenue_trend_pct` e o cast original de `customer_id`.

---

## Resumo do que o contrato protege

| Situação | Sem contrato | Com contrato |
|---|---|---|
| Coluna removida do SQL | Build passa, Looker Studio quebra | Build falha com ContractError |
| Tipo muda (int → string) | Build passa, joins downstream quebram | Build falha com ContractError |
| Coluna nova adicionada | Build passa, coluna disponível | Build passa, coluna disponível (sem aviso) |
| Schema.yml desatualizado | Sem validação | Build falha até yml ser atualizado |

**Regra prática:** declare no contrato todas as colunas que são consumidas
por ferramentas externas (Looker Studio, notebooks, APIs). Colunas internas
de debug podem ficar fora.

---

## Integrando ao CI/CD

No seu pipeline de PR (GitHub Actions, Cloud Build), adicione:

```yaml
# .github/workflows/dbt_ci.yml
- name: dbt build com validação de contrato
  run: |
    dbt deps
    dbt build --select state:modified+ --defer --state ./prod-artifacts --target prod
```

Assim, qualquer PR que modifique um modelo com contrato é validado automaticamente
antes do merge — sem precisar de revisão manual de tipos.
