"""
judge.py — LLM-as-judge: usa Claude para avaliar qualidade das respostas.

Conceito — LLM-as-judge:
  Algumas propriedades de uma boa resposta de agente são difíceis de verificar
  com código (regex, string matching):
    * "A resposta é relevante para a pergunta?" — subjetivo
    * "O agente inventou algum dado?" — requer raciocínio
    * "A explicação é clara e completa?" — qualitativo

  A solução é usar um LLM separado como avaliador. O judge recebe:
    - A pergunta original
    - Os critérios de aceitação (do golden dataset)
    - A resposta do agente

  E retorna um JSON estruturado com scores por critério + justificativa.

  Importante: o judge deve ser um modelo diferente do agente, ou pelo menos
  uma chamada separada. Isso evita viés (o modelo não avalia suas próprias respostas).
  Em produção: usar claude-sonnet para o agente, claude-opus para o judge.
  Para estudo: usamos o mesmo modelo com prompts diferentes.

Conceito — Por que JSON forçado no judge?
  Se o judge retornar texto livre, você precisa parsear — propenso a erros.
  Com `response_format` ou instrução explícita de JSON, o output é estruturado
  e pode ser salvo diretamente no resultado do eval.

Uso:
  python judge.py --input results/run_20240101_120000.json
  python judge.py --input results/run_20240101_120000.json --ids E01,E03
"""

import argparse
import json
import os
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

JUDGE_MODEL = "claude-haiku-4-5-20251001"
EVALS_DIR   = Path(__file__).parent


# =============================================================================
# PROMPT DO JUDGE
# =============================================================================

JUDGE_SYSTEM = """Você é um avaliador especializado em respostas de agentes de dados.
Sua tarefa é avaliar se a resposta de um agente atende aos critérios fornecidos.

Seja objetivo e criterioso. Avalie exatamente o que é pedido — nem mais, nem menos.
Retorne SOMENTE um JSON válido, sem markdown, sem explicações fora do JSON."""

JUDGE_TEMPLATE = """
## Pergunta feita ao agente
{question}

## Critérios de avaliação (cada um deve ser True ou False)
{criteria_list}

## Resposta do agente
{answer}

## Tools utilizadas pelo agente
{tools_used}

## Tarefa
Avalie cada critério acima e retorne um JSON com este formato exato:
{{
  "criteria_scores": {{
    "criterio_1": true,
    "criterio_2": false,
    ...
  }},
  "overall_quality": 1-5,
  "hallucination_detected": false,
  "justification": "Explicação breve em 2-3 frases do que o agente acertou e errou."
}}

Regras:
- overall_quality: 1=péssimo, 2=ruim, 3=ok, 4=bom, 5=excelente
- hallucination_detected: true se o agente inventou dados sem base nas tools
- Avalie APENAS com base na resposta fornecida — não execute queries reais
"""


def evaluate_case(client: anthropic.Anthropic, result: dict, case: dict) -> dict:
    """
    Usa o LLM como juiz para avaliar um resultado de eval.

    Args:
        client: cliente Anthropic
        result: resultado do runner (com response.answer, tool_calls etc)
        case  : caso do golden dataset (com judge_criteria)

    Returns:
        dict com scores do judge
    """
    if "error" in result:
        return {
            "error": "runner_error — não há resposta para avaliar",
            "criteria_scores": {},
            "overall_quality": 0,
            "hallucination_detected": False,
            "justification": "Avaliação impossível — runner retornou erro.",
        }

    response = result.get("response", {})
    answer   = response.get("answer", "")
    tool_calls = response.get("tool_calls", [])
    tools_used = [tc["tool_name"] for tc in tool_calls]
    criteria   = case.get("judge_criteria", [])

    if not criteria:
        return {"skipped": "Nenhum critério de judge definido para este caso."}

    # Nomeia os critérios como chaves para o JSON
    criteria_keys = {f"criterio_{i+1}": c for i, c in enumerate(criteria)}
    criteria_list = "\n".join(f"- {k}: {v}" for k, v in criteria_keys.items())

    prompt = JUDGE_TEMPLATE.format(
        question=result["question"],
        criteria_list=criteria_list,
        answer=answer[:3000],  # trunca se muito longa
        tools_used=", ".join(tools_used) if tools_used else "nenhuma",
    )

    response_judge = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=1024,
        system=JUDGE_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response_judge.content[0].text.strip()

    # Parse do JSON — strip de markdown code blocks caso o modelo os inclua
    # Ex: ```json\n{...}\n``` → {...}
    stripped = raw
    if stripped.startswith("```"):
        # Remove primeira linha (```json ou ```) e última linha (```)
        lines = stripped.splitlines()
        stripped = "\n".join(
            line for line in lines
            if not line.strip().startswith("```")
        ).strip()

    try:
        parsed = json.loads(stripped)
        # Substitui chaves genéricas pelo texto real do critério
        if "criteria_scores" in parsed:
            parsed["criteria_text"] = {
                criteria_keys[k]: v
                for k, v in parsed["criteria_scores"].items()
                if k in criteria_keys
            }
        return parsed
    except json.JSONDecodeError:
        return {
            "parse_error": True,
            "raw_response": raw,
            "criteria_scores": {},
            "overall_quality": 0,
            "hallucination_detected": False,
            "justification": "Falha ao parsear resposta do judge.",
        }


def run_judge(input_path: Path, ids_filter: list[str] | None = None, dataset_path: Path | None = None) -> Path:
    """
    Lê um arquivo de resultados do runner, avalia cada caso com o judge
    e salva um novo arquivo com os scores do judge adicionados.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    results = json.loads(input_path.read_text())
    # Usa dataset_path se fornecido, senão tenta inferir pelo nome do arquivo de resultados
    # ou cai no padrão golden_dataset.json
    ds_path = dataset_path or (EVALS_DIR / "golden_dataset.json")
    dataset = {c["id"]: c for c in json.loads(ds_path.read_text())}

    if ids_filter:
        results = [r for r in results if r["id"] in ids_filter]

    print(f"\n{'='*60}")
    print(f"  LLM-AS-JUDGE — {len(results)} casos")
    print(f"  Modelo: {JUDGE_MODEL}")
    print(f"{'='*60}\n")

    for i, result in enumerate(results, 1):
        eid  = result["id"]
        case = dataset.get(eid, {})
        print(f"[{i:02d}/{len(results)}] {eid} — avaliando...", end=" ", flush=True)

        judge_scores = evaluate_case(client, result, case)
        result["judge_scores"] = judge_scores

        quality = judge_scores.get("overall_quality", "?")
        halluc  = judge_scores.get("hallucination_detected", "?")
        criteria_results = judge_scores.get("criteria_text", {})
        passed = sum(1 for v in criteria_results.values() if v is True)
        total  = len(criteria_results)

        print(f"quality={quality}/5 | critérios={passed}/{total} | hallucination={halluc}")
        time.sleep(0.5)  # evita rate limiting

    # Salva com sufixo _judged
    out_path = input_path.parent / input_path.name.replace(".json", "_judged.json")
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\nResultados com judge salvos em: {out_path}")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Arquivo de resultados do runner")
    parser.add_argument("--ids", help="IDs separados por vírgula")
    parser.add_argument("--dataset", default=None, help="Dataset JSON usado no run (ex: golden_dataset_v2.json)")
    args = parser.parse_args()

    ids_filter = args.ids.split(",") if args.ids else None
    dataset_path = Path(args.dataset) if args.dataset else None
    out = run_judge(Path(args.input), ids_filter=ids_filter, dataset_path=dataset_path)

    print(f"\nPara gerar relatório:")
    print(f"  python run_evals.py --judged {out}")
