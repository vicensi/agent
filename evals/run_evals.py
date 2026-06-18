"""
run_evals.py — Orquestrador completo: runner → judge → relatório.

Uso:
  # Roda tudo (runner + judge + relatório)
  python run_evals.py

  # Casos específicos
  python run_evals.py --ids E01,E09,E10

  # Só gera relatório de um arquivo já julgado
  python run_evals.py --judged results/run_20240101_120000_judged.json

  # Só runner (sem judge — mais rápido para checar estrutural)
  python run_evals.py --no-judge
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from runner import run, save_results
from judge import run_judge

EVALS_DIR = Path(__file__).parent


# =============================================================================
# RELATÓRIO
# =============================================================================

def generate_report(judged_path: Path) -> str:
    """
    Gera relatório de texto a partir de um arquivo julgado.
    Retorna string formatada para console.
    """
    results = json.loads(judged_path.read_text())

    total = len(results)
    errors = sum(1 for r in results if "error" in r)
    valid  = [r for r in results if "error" not in r]

    # ── Métricas estruturais ──────────────────────────────────────────────────
    structural = [r.get("structural_scores", {}) for r in valid]

    citation_rate = (
        sum(1 for s in structural if s.get("citation_present", False)) / len(structural)
        if structural else 0
    )
    refusal_rate = (
        sum(1 for s in structural if s.get("refused_correctly", False)) / len(structural)
        if structural else 0
    )
    tool_rate = (
        sum(1 for s in structural if s.get("tool_called_correctly", False)) / len(structural)
        if structural else 0
    )
    structural_pass_rate = (
        sum(1 for s in structural if s.get("passed_all_structural", False)) / len(structural)
        if structural else 0
    )

    # ── Métricas do judge ─────────────────────────────────────────────────────
    judge_scores = [r.get("judge_scores") for r in valid if r.get("judge_scores")]
    judged_count = len([j for j in judge_scores if j and "error" not in j and "skipped" not in j])

    avg_quality = (
        sum(j.get("overall_quality", 0) for j in judge_scores if j and "error" not in j)
        / judged_count if judged_count else 0
    )
    hallucination_count = sum(
        1 for j in judge_scores
        if j and j.get("hallucination_detected", False)
    )

    # Critérios do judge por caso
    criteria_scores_all = []
    for j in judge_scores:
        if j and "criteria_text" in j:
            criteria_scores_all.extend(j["criteria_text"].values())
    judge_criteria_rate = (
        sum(1 for v in criteria_scores_all if v is True) / len(criteria_scores_all)
        if criteria_scores_all else 0
    )

    # ── Latência ─────────────────────────────────────────────────────────────
    latencies = [r.get("elapsed_ms", 0) for r in valid if r.get("elapsed_ms", 0) > 0]
    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    max_latency = max(latencies) if latencies else 0

    # ── Por categoria ─────────────────────────────────────────────────────────
    by_category: dict[str, list] = {}
    for r in valid:
        cat = r.get("category", "unknown")
        by_category.setdefault(cat, []).append(r)

    # ── Relatório ──────────────────────────────────────────────────────────────
    lines = [
        "",
        "╔══════════════════════════════════════════════════════════╗",
        "║         AGENTIC DATA PLATFORM — EVAL REPORT              ║",
        f"║         {judged_path.name:<50}║",
        "╚══════════════════════════════════════════════════════════╝",
        "",
        "── RESUMO ──────────────────────────────────────────────────",
        f"  Total de casos : {total}",
        f"  Erros de runner: {errors}",
        f"  Casos válidos  : {len(valid)}",
        f"  Casos julgados : {judged_count}",
        "",
        "── MÉTRICAS ESTRUTURAIS (sem LLM) ──────────────────────────",
        f"  Pass rate geral  : {structural_pass_rate:.0%}",
        f"  Citation rate    : {citation_rate:.0%}  (respostas com seção Fonte)",
        f"  Refusal accuracy : {refusal_rate:.0%}  (recusou quando deveria / não recusou quando não)",
        f"  Tool correctness : {tool_rate:.0%}  (usou a tool esperada)",
        "",
        "── MÉTRICAS QUALITATIVAS (LLM-as-judge) ────────────────────",
        f"  Qualidade média  : {avg_quality:.1f}/5",
        f"  Critérios pass   : {judge_criteria_rate:.0%}  ({sum(1 for v in criteria_scores_all if v)}/{len(criteria_scores_all)} critérios)",
        f"  Alucinações      : {hallucination_count}/{judged_count} casos",
        "",
        "── LATÊNCIA ────────────────────────────────────────────────",
        f"  Média  : {avg_latency/1000:.1f}s",
        f"  Máxima : {max_latency/1000:.1f}s",
        "",
        "── POR CATEGORIA ───────────────────────────────────────────",
    ]

    for cat, cat_results in sorted(by_category.items()):
        cat_pass = sum(
            1 for r in cat_results
            if r.get("structural_scores", {}).get("passed_all_structural", False)
        )
        cat_quality = []
        for r in cat_results:
            j = r.get("judge_scores")
            if j and "overall_quality" in j:
                cat_quality.append(j["overall_quality"])
        avg_q = sum(cat_quality) / len(cat_quality) if cat_quality else None

        qual_str = f" | quality={avg_q:.1f}" if avg_q else ""
        lines.append(f"  {cat:<28}: {cat_pass}/{len(cat_results)} pass{qual_str}")

    lines += [
        "",
        "── CASOS COM PROBLEMAS ─────────────────────────────────────",
    ]

    failed = [
        r for r in valid
        if not r.get("structural_scores", {}).get("passed_all_structural", False)
        or (r.get("judge_scores") and r["judge_scores"].get("hallucination_detected", False))
    ]

    if not failed:
        lines.append("  ✅ Nenhum caso com falha!")
    else:
        for r in failed:
            eid = r["id"]
            q   = r["question"][:60]
            j   = r.get("judge_scores", {}) or {}
            issues = []
            s = r.get("structural_scores", {})
            if not s.get("citation_present"): issues.append("sem_citação")
            if not s.get("refused_correctly"): issues.append("recusa_errada")
            if not s.get("tool_called_correctly"): issues.append("tool_errada")
            if j.get("hallucination_detected"): issues.append("ALUCINAÇÃO")
            lines.append(f"  ❌ {eid}: {q}... [{', '.join(issues)}]")
            if j.get("justification"):
                lines.append(f"     Judge: {j['justification'][:120]}")

    lines += ["", "─" * 60, ""]
    return "\n".join(lines)


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Orquestrador de evals do agente")
    parser.add_argument("--ids", help="IDs separados por vírgula")
    parser.add_argument("--no-judge", action="store_true", help="Pula o LLM-as-judge")
    parser.add_argument("--judged", help="Pula runner e judge, só gera relatório deste arquivo")
    parser.add_argument("--dataset", default=None, help="Dataset JSON alternativo (ex: golden_dataset_v2.json)")
    args = parser.parse_args()

    ids_filter = args.ids.split(",") if args.ids else None

    if args.judged:
        # Só relatório
        judged_path = Path(args.judged)
    else:
        # Runner
        dataset_path = Path(args.dataset) if args.dataset else None
        results = run(ids_filter=ids_filter, dataset_path=dataset_path)
        if not results:
            print("Nenhum resultado para processar.")
            exit(0)

        run_path = save_results(results)

        if args.no_judge:
            judged_path = run_path
        else:
            # Judge — passa o mesmo dataset usado pelo runner
            judged_path = run_judge(run_path, ids_filter=ids_filter, dataset_path=dataset_path)

    # Relatório
    report = generate_report(judged_path)
    print(report)

    # Salva relatório em txt
    report_path = judged_path.parent / judged_path.name.replace(".json", "_report.txt")
    report_path.write_text(report)
    print(f"Relatório salvo em: {report_path}")
