"""
runner.py — Executa o golden dataset contra a API e coleta respostas brutas.

Conceito — Por que evals com golden dataset?
  O agente evolui ao longo do tempo: mudanças no system prompt, no catálogo,
  no modelo Claude, nas tools. Sem uma suite de evals, você não sabe se uma
  mudança melhorou ou piorou o comportamento — você confia no feeling.

  O golden dataset é um conjunto fixo de perguntas com critérios de aceitação.
  Ele funciona como uma test suite para o agente: roda antes de qualquer deploy,
  mede regressão, documenta o comportamento esperado.

Conceito — O que medir?
  Há duas classes de métricas:
    1. Métricas estruturais (determinísticas): citation_rate, refusal_accuracy,
       tool_used_correctly — verificadas por código, sem LLM.
    2. Métricas qualitativas (probabilísticas): answer_relevance, no_hallucination,
       completeness — avaliadas por LLM-as-judge (judge.py).

  A combinação das duas dá uma visão completa: o agente cita a fonte sempre? (1)
  Mas a resposta faz sentido? (2)

Uso:
  python runner.py                    # roda todos os casos
  python runner.py --ids E01,E03,E09  # casos específicos
  python runner.py --dry-run          # mostra perguntas sem chamar a API
"""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import httpx  # mais ergonômico que requests para uso sync

EVALS_DIR   = Path(__file__).parent
DATASET     = EVALS_DIR / "golden_dataset.json"  # default; sobrescrito por --dataset
RESULTS_DIR = EVALS_DIR / "results"
API_BASE    = "http://localhost:8001"
TIMEOUT     = 60  # segundos — o agente pode demorar com múltiplas tools


def load_dataset(ids_filter: list[str] | None = None, dataset_path: Path | None = None) -> list[dict]:
    path = dataset_path or DATASET
    cases = json.loads(path.read_text())
    if ids_filter:
        cases = [c for c in cases if c["id"] in ids_filter]
    return cases


def call_ask(question: str, session_id: str) -> dict:
    """Chama POST /ask e retorna o JSON completo da resposta."""
    resp = httpx.post(
        f"{API_BASE}/ask",
        json={"question": question, "session_id": session_id},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def score_structural(case: dict, response: dict) -> dict:
    """
    Verifica critérios estruturais sem LLM.
    Retorna dict com scores booleanos e observações.

    Critérios verificados:
      citation_present : a resposta contém a palavra "Fonte" (seção obrigatória)
      refused_correctly: caso should_refuse=True → agente recusou (refused=True)
                         caso should_refuse=False → agente NÃO recusou
      tool_called      : alguma das expected_tools foi efetivamente chamada
      no_api_error     : resposta não contém indicação de erro interno
    """
    answer = response.get("answer", "")
    tool_calls = response.get("tool_calls", [])
    tools_used = {tc["tool_name"] for tc in tool_calls}
    refused = response.get("refused", False)

    expected_tools = set(case.get("expected_tools", []))
    should_refuse = case.get("should_refuse", False)
    must_cite = case.get("must_cite_source", True)

    scores = {}

    # 1. Citação de fonte presente na resposta textual
    scores["citation_present"] = (
        ("Fonte" in answer or "fonte" in answer or "SQL" in answer or "tool" in answer.lower())
        if must_cite else True  # casos sem obrigação de citar passam automaticamente
    )

    # 2. Comportamento de recusa correto
    if should_refuse:
        scores["refused_correctly"] = refused or (
            "fora do escopo" in answer.lower()
            or "não posso" in answer.lower()
            or "não tenho acesso" in answer.lower()
            or "não é possível" in answer.lower()
        )
    else:
        scores["refused_correctly"] = not refused

    # 3. Usou a tool esperada (se houver expectativa)
    if expected_tools:
        scores["tool_called_correctly"] = bool(expected_tools & tools_used)
    else:
        # Caso should_refuse: esperamos que nenhuma tool tenha sido chamada
        scores["tool_called_correctly"] = len(tool_calls) == 0 if should_refuse else True

    # 4. Sem erro de API visível na resposta
    scores["no_api_error"] = "Erro interno" not in answer and "detail" not in response

    # Resumo
    scores["tools_used"] = list(tools_used)
    scores["expected_tools"] = list(expected_tools)
    scores["passed_all_structural"] = all(
        v for k, v in scores.items()
        if k not in ("tools_used", "expected_tools", "passed_all_structural")
    )

    return scores


def run(ids_filter: list[str] | None = None, dry_run: bool = False, dataset_path: Path | None = None) -> list[dict]:
    cases = load_dataset(ids_filter, dataset_path=dataset_path)
    results = []

    print(f"\n{'='*60}")
    print(f"  EVAL RUNNER — {len(cases)} casos")
    print(f"  API: {API_BASE}")
    print(f"  {'DRY RUN — sem chamadas reais' if dry_run else 'Modo real'}")
    print(f"{'='*60}\n")

    for i, case in enumerate(cases, 1):
        eid      = case["id"]
        category = case["category"]
        question = case["question"]
        session  = f"eval-{eid}-{int(time.time())}"

        print(f"[{i:02d}/{len(cases)}] {eid} ({category})")
        print(f"  Q: {question[:80]}...")

        if dry_run:
            print("  → DRY RUN, pulando chamada\n")
            continue

        start = time.monotonic()
        try:
            response = call_ask(question, session)
            elapsed = int((time.monotonic() - start) * 1000)

            structural = score_structural(case, response)
            status = "✅" if structural["passed_all_structural"] else "❌"

            print(f"  → {status} {elapsed}ms | tools: {structural['tools_used']}")
            print(f"     citation={structural['citation_present']} | "
                  f"refused_ok={structural['refused_correctly']} | "
                  f"tool_ok={structural['tool_called_correctly']}")

            results.append({
                "id": eid,
                "category": category,
                "question": question,
                "session_id": session,
                "elapsed_ms": elapsed,
                "response": response,
                "structural_scores": structural,
                "judge_scores": None,  # preenchido por judge.py
                "timestamp": datetime.utcnow().isoformat(),
            })

        except httpx.HTTPStatusError as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            print(f"  → ❌ HTTP {exc.response.status_code}: {exc.response.text[:100]}")
            results.append({
                "id": eid,
                "category": category,
                "question": question,
                "session_id": session,
                "elapsed_ms": elapsed,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text}",
                "structural_scores": {"passed_all_structural": False},
                "judge_scores": None,
                "timestamp": datetime.utcnow().isoformat(),
            })
        except Exception as exc:
            print(f"  → ❌ Erro: {exc}")
            results.append({
                "id": eid,
                "category": category,
                "question": question,
                "session_id": session,
                "elapsed_ms": 0,
                "error": str(exc),
                "structural_scores": {"passed_all_structural": False},
                "judge_scores": None,
                "timestamp": datetime.utcnow().isoformat(),
            })

        print()

    return results


def save_results(results: list[dict]) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out = RESULTS_DIR / f"run_{ts}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\nResultados salvos em: {out}")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", help="IDs separados por vírgula (ex: V2-E01,V2-E03)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dataset", default=None, help="Caminho para o dataset JSON (padrão: golden_dataset.json)")
    args = parser.parse_args()

    ids_filter = args.ids.split(",") if args.ids else None
    dataset_path = Path(args.dataset) if args.dataset else None
    results = run(ids_filter=ids_filter, dry_run=args.dry_run, dataset_path=dataset_path)

    if results:
        out_path = save_results(results)
        # Passa para o judge automaticamente
        print(f"\nPara avaliar com LLM-as-judge:")
        print(f"  python judge.py --input {out_path}")
