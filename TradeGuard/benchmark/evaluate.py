#!/usr/bin/env python3
"""TradeGuard 벤치마크 평가기 — 40건 일괄 검출 → 정량 지표

산출 지표 (기술설명서에 그대로 인용할 수치):
  · 하자 유형 단위 정밀도/재현율/F1 (micro)
  · 하자 유형별 성적표
  · 케이스 단위 판정 정확도 (하자 있음/없음 이진)
  · 정상 케이스 오탐률 — 특히 "함정 정상" 4건
  · 등급(A~D) 일치율

사용법:
  python3 evaluate.py --cases cases --out metrics.json [--md report.md]
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
from detect import build_report, _llm_client, d as parse_date  # noqa: E402


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def main():
    args = sys.argv[1:]
    cases_dir = Path(args[args.index("--cases") + 1]) if "--cases" in args else Path("cases")
    out_path = Path(args[args.index("--out") + 1]) if "--out" in args else None
    md_path = Path(args[args.index("--md") + 1]) if "--md" in args else None

    files = sorted(cases_dir.glob("*.json"))
    if not files:
        sys.exit(f"케이스 없음: {cases_dir}")

    _c = _llm_client()
    mode = f"LLM 의미비교 활성({_c.name})" if _c else "오프라인 휴리스틱 폴백"
    print(f"[evaluate] {len(files)}건 · 판정 모드: {mode}\n")

    tp = fp = fn = 0
    per_type = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    case_correct = grade_match = 0
    clean_fp_cases, trap_results, failures = [], [], []

    for f in files:
        case = json.loads(f.read_text(encoding="utf-8"))
        gt = case["ground_truth"]
        rep = build_report(case["case_id"], case["documents"], parse_date(case.get("presentation_date")))
        found = {d["type"] for d in rep["discrepancies"]}
        expect = {d["type"] for d in gt["discrepancies"]}

        c_tp, c_fp, c_fn = found & expect, found - expect, expect - found
        tp += len(c_tp); fp += len(c_fp); fn += len(c_fn)
        for t in c_tp: per_type[t]["tp"] += 1
        for t in c_fp: per_type[t]["fp"] += 1
        for t in c_fn: per_type[t]["fn"] += 1

        if bool(found) == bool(expect):
            case_correct += 1
        if rep["overall_risk"]["grade"] == gt["overall_risk"]["grade"]:
            grade_match += 1
        if case["label"] == "clean" and found:
            clean_fp_cases.append((case["case_id"], sorted(found)))
        if case["label"] == "clean" and case["difficulty"] == "hard":
            trap_results.append((case["case_id"], "PASS" if not found else f"FAIL({sorted(found)})",
                                 case["scenario_note_ko"].split("·")[-1].strip()))
        if c_fp or c_fn:
            failures.append({"case_id": case["case_id"], "fp": sorted(c_fp), "fn": sorted(c_fn)})

    p, r, f1 = prf(tp, fp, fn)
    n = len(files)
    n_clean = sum(1 for f in files if json.loads(f.read_text(encoding="utf-8"))["label"] == "clean")

    metrics = {
        "mode": mode, "n_cases": n,
        "discrepancy_level": {"tp": tp, "fp": fp, "fn": fn,
                              "precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)},
        "case_level": {"accuracy": round(case_correct / n, 4),
                       "grade_accuracy": round(grade_match / n, 4),
                       "clean_false_positive_rate": round(len(clean_fp_cases) / n_clean, 4)},
        "per_type": {t: dict(v, **dict(zip(("precision", "recall", "f1"),
                     map(lambda x: round(x, 4), prf(v["tp"], v["fp"], v["fn"])))))
                     for t, v in sorted(per_type.items())},
        "trap_cases": [{"case_id": c, "result": s, "note": nt} for c, s, nt in trap_results],
        "failures": failures,
    }

    print("=== 하자 단위 (micro) ===")
    print(f"  TP {tp} · FP {fp} · FN {fn}")
    print(f"  정밀도 {p:.1%} · 재현율 {r:.1%} · F1 {f1:.3f}\n")
    print("=== 케이스 단위 ===")
    print(f"  하자 유무 판정 정확도: {case_correct}/{n} ({case_correct / n:.1%})")
    print(f"  등급 일치율:           {grade_match}/{n} ({grade_match / n:.1%})")
    print(f"  정상 케이스 오탐률:    {len(clean_fp_cases)}/{n_clean} ({len(clean_fp_cases) / n_clean:.1%})\n")
    print("=== 하자 유형별 ===")
    for t, v in metrics["per_type"].items():
        print(f"  {t:34s} TP{v['tp']:3d} FP{v['fp']:3d} FN{v['fn']:3d}  "
              f"P {v['precision']:.0%} R {v['recall']:.0%}")
    if trap_results:
        print("\n=== 함정 정상 (오탐 저항) ===")
        for c, s, nt in trap_results:
            print(f"  {c} {s:6s} - {nt}")
    if failures:
        print("\n=== 오류 케이스 ===")
        for x in failures:
            print(f"  {x['case_id']} FP={x['fp']} FN={x['fn']}")

    if out_path:
        out_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[evaluate] 지표 저장: {out_path}")
    if md_path:
        lines = [f"# TradeGuard 하자 검출 정확도 리포트", "",
                 f"- 벤치마크: 합성 케이스 {n}건 (하자 {n - n_clean} · 정상 {n_clean}, 함정 정상 {len(trap_results)} 포함)",
                 f"- 판정 모드: {mode}", "",
                 "## 종합 지표", "",
                 "| 지표 | 값 |", "|---|---|",
                 f"| 하자 검출 정밀도 | {p:.1%} |", f"| 하자 검출 재현율 | {r:.1%} |",
                 f"| F1 | {f1:.3f} |",
                 f"| 케이스 판정 정확도 | {case_correct / n:.1%} |",
                 f"| 등급 일치율 | {grade_match / n:.1%} |",
                 f"| 정상 케이스 오탐률 | {len(clean_fp_cases) / n_clean:.1%} |", "",
                 "## 하자 유형별", "", "| 유형 | TP | FP | FN | 정밀도 | 재현율 |", "|---|---|---|---|---|---|"]
        for t, v in metrics["per_type"].items():
            lines.append(f"| {t} | {v['tp']} | {v['fp']} | {v['fn']} | {v['precision']:.0%} | {v['recall']:.0%} |")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"[evaluate] 리포트 저장: {md_path}")


if __name__ == "__main__":
    main()
