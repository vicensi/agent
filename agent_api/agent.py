"""
agent.py — Loop agentico com tool use da Claude API.

Conceito — Agentic loop:
  O loop agentico é o núcleo de qualquer agente LLM. Funciona assim:

  1. Chamada inicial: envia pergunta + tools disponíveis para a Claude API
  2. Claude decide se precisa de dados → retorna blocos `tool_use`
  3. Para cada `tool_use`: executor chama a tool real e coleta resultado
  4. Resultados enviados de volta como `tool_result` no próximo turno
  5. Repete até stop_reason == "end_turn" (Claude acabou) ou == "max_tokens"
  6. Último bloco `text` do último turno = resposta final

  O loop para automaticamente quando Claude decide que tem dados suficientes.
  Isso é diferente de um chain fixo (ex: RAG) — o agente escolhe quantas
  ferramentas chamar e em qual sequência.

Conceito — System prompt como contrato:
  O system prompt não é só "instrução de comportamento" — é um contrato
  estrutural. Ele define:
    * O que o agente DEVE fazer (citar fonte sempre)
    * O que NUNCA pode fazer (inventar dados)
    * O formato da resposta (seções obrigatórias)
    * Como lidar com perguntas fora de escopo (recusar com explicação)

  Segurança em profundidade: o system prompt é a camada de apresentação.
  A camada real de segurança é o IAM do GCP (agent_readonly) + sql_validator.

Conceito — Citation enforcement:
  A resposta do agente DEVE incluir de onde vieram os dados.
  Implementado em duas camadas:
    1. System prompt: instrução explícita de citar ferramenta + SQL
    2. AskResponse.tool_calls: trilha de auditoria estruturada separada da resposta
  Isso permite que UIs de BI mostrem "Ver SQL de origem" junto com cada número.
"""

import logging
import os
import time
from typing import Any

import anthropic

from tools import TOOL_DEFINITIONS, ToolExecutor

logger = logging.getLogger(__name__)

# =============================================================================
# SYSTEM PROMPT — CONTRATO DO AGENTE
# =============================================================================

SYSTEM_PROMPT = """Você é o Data Agent da Agentic Data Platform, um assistente especializado
em análise de dados de e-commerce. Você tem acesso direto ao warehouse BigQuery via ferramentas.

## Regras invioláveis

1. **NUNCA invente números.** Se não tiver dados, diga isso explicitamente.
2. **SEMPRE cite a fonte** de cada dado: qual ferramenta foi chamada, quais parâmetros,
   o SQL executado (se aplicável). Sem citação = resposta inválida.
3. **Use query_metric por padrão.** Só use run_sql_readonly quando nenhuma métrica cobrir
   a pergunta — e justifique por que.
4. **Recuse educadamente** perguntas fora de escopo (dados externos, previsões sem base,
   informações pessoais de clientes). Explique o que você PODE responder.

## Formato obrigatório da resposta

Para perguntas de dados:

**[Resposta direta em 1-2 frases]**

**Dados:**
[Tabela ou lista com os valores]

**Fonte:**
- Ferramenta: `nome_da_tool`
- Métrica/SQL: `nome_da_metrica` ou bloco SQL
- Período: [período analisado]
- Linhas retornadas: [N]

**Caveats:**
[Se houver ressalvas sobre os dados — ex: dados sintéticos, truncamento, etc.]

Para perguntas fora de escopo:
"Fora do escopo: [explicação]. Posso ajudar com: [alternativas dentro do escopo]."

## Atalhos para perguntas comuns

- **Ticket médio por categoria de produto**: `fct_orders` não tem coluna `category` — vá direto para
  `run_sql_readonly` com JOIN em `marts.dim_products`. Não tente `query_metric` com dimensão `category`.
- **Qualquer análise cruzando produto + categoria**: use JOIN entre `marts.fct_orders` e `marts.dim_products`
  via `product_name`.

## Visualizações (plot_chart)

Use a tool `plot_chart` quando uma visualização ajuda a responder melhor.
Regras:
- Chame `plot_chart` APÓS obter os dados (query_metric ou run_sql_readonly) — nunca antes.
- Use para: comparações entre categorias (bar), evolução temporal (line), distribuição de partes (pie).
- NÃO use para: valor único, ranking muito longo (>10 itens em pie), ou quando o texto já basta.
- Ao chamar, passe os dados extraídos da query — não invente valores.
- O gráfico será renderizado inline no chat pelo frontend — você não precisa descrevê-lo em texto.

## Dados disponíveis

Você tem acesso a dados de e-commerce sintéticos com ~192k pedidos.
Métricas: receita (entregue, bruta, líquida), devoluções, ticket médio,
taxa de retorno, clientes ativos.
Dimensões: canal (app_ios, app_android, web_desktop, web_mobile, marketplace),
estado do cliente, categoria de produto, data, segmento RFM.

Os dados são SINTÉTICOS — não representam uma empresa real.
"""

# Modelo Claude a usar — claude-3-5-haiku é mais rápido e barato para tool use
# claude-3-5-sonnet-20241022 para análises mais complexas
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

# Máximo de iterações do loop — evita loops infinitos em caso de tool errors
MAX_ITERATIONS = 12


# =============================================================================
# FUNÇÃO PRINCIPAL DO LOOP AGENTICO
# =============================================================================

def run_agent(question: str, session_id: str | None = None) -> dict[str, Any]:
    """
    Executa o loop agentico para responder uma pergunta em linguagem natural.

    Args:
        question  : Pergunta do usuário em linguagem natural.
        session_id: Identificador de sessão para rastreamento nos logs.

    Returns:
        dict com:
          - answer         : str — resposta final formatada em markdown
          - tool_calls_log : list[dict] — trilha de auditoria das tools chamadas
          - total_elapsed_ms: int
          - refused        : bool — True se recusou responder
          - iterations     : int — quantas rodadas o loop executou
    """
    start_total = time.monotonic()
    executor = ToolExecutor()
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    logger.info(
        "agent_start",
        extra={"question": question[:200], "session_id": session_id},
    )

    # Histórico de mensagens para o loop multi-turno
    # Cada iteração appenda ao messages — Claude mantém contexto completo
    messages: list[dict] = [
        {"role": "user", "content": question}
    ]

    final_answer = ""
    iterations = 0

    # ==========================================================================
    # LOOP AGENTICO
    # ==========================================================================
    for iteration in range(MAX_ITERATIONS):
        iterations += 1

        logger.info(
            "agent_iteration",
            extra={"iteration": iteration + 1, "session_id": session_id},
        )

        # ── Chama a Claude API ────────────────────────────────────────────────
        #
        # Conceito — parâmetros importantes:
        #   tools      : lista de definições → Claude conhece o que pode chamar
        #   tool_choice: {"type": "auto"} → Claude decide se/quando usar tools
        #                Alternativas: "any" (força pelo menos 1 tool),
        #                             "none" (nunca usa tools)
        #   max_tokens : limite da resposta final (tools têm tokens separados)
        #
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            tool_choice={"type": "auto"},
            messages=messages,
        )

        logger.info(
            "agent_claude_response",
            extra={
                "stop_reason": response.stop_reason,
                "content_types": [b.type for b in response.content],
                "session_id": session_id,
            },
        )

        # ── Coleta blocos de texto e tool_use ────────────────────────────────
        text_blocks = [b for b in response.content if b.type == "text"]
        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        # ── Caso 1: Sem tool calls → resposta final ───────────────────────────
        if response.stop_reason == "end_turn" and not tool_use_blocks:
            final_answer = "\n".join(b.text for b in text_blocks)
            break

        # ── Caso 2: Claude quer usar tools ───────────────────────────────────
        if tool_use_blocks:
            # Adiciona a resposta do Claude ao histórico (inclui blocos tool_use)
            messages.append({
                "role": "assistant",
                "content": response.content,
            })

            # Executa cada tool e coleta resultados
            tool_results = []
            for tool_block in tool_use_blocks:
                tool_result_content = executor.execute(
                    tool_name=tool_block.name,
                    tool_input=tool_block.input,
                )

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_block.id,
                    "content": tool_result_content,
                })

            # Adiciona resultados das tools ao histórico
            # Claude vai ler esses resultados na próxima iteração
            messages.append({
                "role": "user",
                "content": tool_results,
            })

            # Se Claude também retornou texto junto com tool_use,
            # é um reasoning intermediário — não é a resposta final ainda
            if text_blocks:
                logger.debug(
                    "agent_intermediate_text",
                    extra={"text": text_blocks[0].text[:200]},
                )

        # ── Caso 3: stop_reason == "end_turn" mas havia tool_use ─────────────
        # Isso não deveria acontecer, mas tratamos como break do loop
        elif response.stop_reason == "end_turn":
            final_answer = "\n".join(b.text for b in text_blocks)
            break

        # ── Caso 4: max_tokens ─────────────────────────────────────────────
        elif response.stop_reason == "max_tokens":
            final_answer = (
                "\n".join(b.text for b in text_blocks)
                + "\n\n⚠️ Resposta truncada por limite de tokens."
            )
            break

    else:
        # Saiu do for sem break = MAX_ITERATIONS atingido
        logger.warning(
            "agent_max_iterations",
            extra={"session_id": session_id, "iterations": iterations},
        )
        final_answer = (
            "Não foi possível completar a análise no número máximo de iterações. "
            "Tente uma pergunta mais específica."
        )

    total_elapsed_ms = int((time.monotonic() - start_total) * 1000)

    # Detecta recusa: resposta começa com "Fora do escopo" (conforme system prompt)
    refused = final_answer.strip().startswith("Fora do escopo")

    logger.info(
        "agent_complete",
        extra={
            "total_elapsed_ms": total_elapsed_ms,
            "iterations": iterations,
            "tool_calls": len(executor.tool_calls_log),
            "refused": refused,
            "session_id": session_id,
        },
    )

    return {
        "answer": final_answer,
        "tool_calls_log": executor.tool_calls_log,
        "charts": executor.charts_log,
        "total_elapsed_ms": total_elapsed_ms,
        "refused": refused,
        "iterations": iterations,
    }
