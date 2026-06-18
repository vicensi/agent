# Post LinkedIn — Agentic Data Platform

---

Passei as últimas semanas construindo um projeto que eu chamei de **Agentic Data Platform** — e quero compartilhar o que aprendi no processo.

A ideia surgiu de uma frustração simples: eu trabalhava com pipelines de dados e sabia que os números estavam no warehouse, mas chegar até eles ainda exigia abrir uma query, lembrar o nome da tabela, escrever o GROUP BY certo. Queria testar se dava pra resolver isso com um agente de IA — não um chatbot de respostas genéricas, mas algo que realmente consultasse o banco e te devolvesse o SQL de onde veio cada número.

---

**O que eu quis aprender:**

Não era sobre construir o sistema mais escalável do mundo. Era sobre entender como as peças se conectam de verdade: dbt Core, BigQuery, MCP, FastAPI, LLM tool use. Projetos de estudo que só exercitam uma tecnologia isolada nunca me ensinaram como os problemas aparecem na junção entre elas.

---

**O que eu construí:**

Um pipeline ELT completo com dbt + BigQuery (raw → staging → marts), com um dataset sintético de ~192k pedidos cheio de armadilhas propositais — duplicatas, estados com grafias sujas, devoluções acima do valor do pedido, timestamps com fusos misturados. O staging trata tudo isso antes de chegar nos marts.

Em cima do warehouse, construí dois clientes: um MCP Server que o Claude Desktop usa diretamente, e uma FastAPI com loop agentico que qualquer aplicação pode chamar via HTTP. O agente tem 5 ferramentas: consultar métricas pré-definidas, escrever SQL livre (com validação de segurança), ver linhagem dos modelos, e — a mais recente — gerar specs de gráficos para o Streamlit renderizar em Plotly inline.

---

**O que eu aprendi:**

A parte técnica foi mais fácil do que esperava. A parte difícil foi entender onde os conceitos quebram na prática.

Por exemplo: eu criei métricas com o mesmo nome em três marts diferentes (`avg_ticket_brl` em `fct_orders`, `agg_daily_revenue` e `dim_customers_kpis`). Só quando fui conectar no Looker Studio percebi que elas calculam coisas diferentes — e que pré-calcular uma média num mart materializado cria uma armadilha: `AVG(avg_ticket_brl)` no Looker é média de médias, resultado errado. A solução foi documentar isso no `schema.yml` com `meta: {bi_aggregation: "non_additive"}` e criar campos calculados no Looker. Essa descoberta rendeu uma convenção que ficou formalizada no projeto.

Outra: campos com `CURRENT_DATE()` em marts materializados congelam no momento do `dbt run`. Com dados históricos terminando em 2024-12-30 e hoje sendo 2026, todos os campos de "últimos 30 dias" retornam 0. Parece óbvio em retrospecto — mas a armadilha é sutil o suficiente pra cair sem perceber.

A distinção entre RAG e tool use também ficou muito mais clara construindo isso. RAG é "buscar texto relevante e injetar no contexto". Tool use é "o modelo decide chamar funções reais e usa o resultado". Para dados estruturados, tool use é a abordagem correta — você não quer que o modelo adivinhe um número baseado em texto, você quer que ele execute a query e cite o SQL.

---

**O que eu faria diferente:**

Modelos incrementais desde o início, não como refatoração. Separar o código do MCP Server e do Agent API em packages Python independentes. Adicionar observabilidade (traces, spans) antes do primeiro endpoint, não depois.

---

O projeto está documentado com CLAUDE.md, schema.yml com descrições completas, convenções de nomenclatura, e um blueprint do Looker Studio com os campos calculados necessários para não cair nas armadilhas de métricas não-aditivas.

Se você também está tentando entender como montar uma stack de dados com IA de ponta a ponta — sem pular as partes difíceis — fico feliz em conversar.

#DataEngineering #dbt #BigQuery #LLM #Python #Analytics

---

*Versão alternativa — mais curta, para testar engajamento:*

---

Construí um agente que consulta um warehouse BigQuery em linguagem natural e responde com o SQL de origem.

Não é RAG. É tool use — o modelo chama funções reais que executam queries.

A parte mais interessante não foi a IA. Foi descobrir que `AVG(avg_ticket_brl)` no Looker Studio é média de médias quando o campo foi pré-calculado por dia+canal. Resultado errado. Armadilha clássica de métricas não-aditivas — e que só aparece quando você vai ligar o BI no mart.

Stack: dbt Core + BigQuery + Anthropic Claude + MCP + FastAPI + Streamlit.

O que aprendi: os problemas aparecem nas junções entre as tecnologias, não dentro de cada uma.

#DataEngineering #Analytics #LLM
