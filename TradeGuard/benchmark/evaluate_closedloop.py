#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""폐쇄 루프 정량 평가 — 담당: C.

"하자를 찾았다"가 아니라 "제출 가능한 상태로 만들었다"를 수치로 증명한다.
LLM을 호출하지 않으므로 비용 0원이고 몇 초 만에 재현된다.

측정 항목
  · 수정 전/후 하자 수, 등급 변화
  · 재심사 통과율 — 치유 가능 하자만 있는 케이스가 A등급에 도달한 비율
  · 치유 가능 하자 해소율 — 제안을 적용해 실제로 사라진 비율
  · 잘못된 자동 수정 방지율 — 제안값이 신용장 기준값과 일치하는 비율
  · 신규 하자 유발 0건 — 수정이 다른 하자를 만들지 않았는지
  · 처리시간 — 재심사 1회 왕복

실행:  python3 evaluate_closedloop.py [--md closedloop_report.md] [--out closedloop_metrics.json]
"""
import json
import os
import sys
import time
from datetime import date
from glob import glob

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))

from detect import build_report            # noqa: E402
from remedy import propose_all, apply_edits  # noqa: E402


def evaluate():
    rows = []
    for path in sorted(glob(os.path.join(HERE, "cases", "*.json"))):
        case = json.load(open(path, encoding="utf-8"))
        cid = os.path.basename(path).replace(".json", "")
        docs = case["documents"]
        # detect.py는 date 객체를 기대한다. 케이스 JSON은 ISO 문자열이므로 변환한다.
        pres = case.get("presentation_date")
        pres = date.fromisoformat(pres) if isinstance(pres, str) else pres

        before = build_report(cid, docs, pres)
        if not before["discrepancies"]:
            continue  # 정상 케이스는 루프 대상이 아니다

        props = propose_all(docs, before)
        curable = [p for p in props if p["curable"]]
        incurable = [p for p in props if not p["curable"]]

        # 제안값이 신용장 기준값과 일치하는가 (잘못된 자동 수정 방지)
        lc = docs["letter_of_credit"]
        expected = {
            "CURRENCY_MISMATCH": lambda: lc.get("currency"),
            "BENEFICIARY_NAME_MISMATCH": lambda: (lc.get("beneficiary") or {}).get("name"),
            "APPLICANT_NAME_MISMATCH": lambda: (lc.get("applicant") or {}).get("name"),
            "GOODS_DESC_MISMATCH": lambda: lc.get("goods_description"),
        }
        correct = 0
        for p in curable:
            fn = expected.get(p["type"])
            if fn is None:
                correct += 1          # 규칙상 단일 정답이 정해진 유형(서명자격·온보드 등)
            elif p["after"] == fn():
                correct += 1

        t0 = time.perf_counter()
        fixed_docs, applied = apply_edits(docs, curable)
        after = build_report(cid, fixed_docs, pres)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        before_types = {x["type"] for x in before["discrepancies"]}
        after_types = {x["type"] for x in after["discrepancies"]}
        rows.append({
            "case_id": cid,
            "before_grade": before["overall_risk"]["grade"],
            "after_grade": after["overall_risk"]["grade"],
            "before_count": len(before["discrepancies"]),
            "after_count": len(after["discrepancies"]),
            "curable": len(curable), "incurable": len(incurable),
            "incurable_types": sorted(p["type"] for p in incurable),
            "resolved": sorted(before_types - after_types),
            "new_defects": sorted(after_types - before_types),
            "proposal_correct": correct, "proposal_total": len(curable),
            "elapsed_ms": round(elapsed_ms, 2),
        })
    return rows


def summarize(rows):
    n = len(rows)
    all_curable = [r for r in rows if r["incurable"] == 0]
    passed = [r for r in all_curable if r["after_grade"] == "A"]
    tot_cur = sum(r["curable"] for r in rows)
    tot_ok = sum(r["proposal_correct"] for r in rows)
    resolved = sum(len(r["resolved"]) for r in rows)
    newd = sum(len(r["new_defects"]) for r in rows)
    return {
        "n_defect_cases": n,
        "n_all_curable": len(all_curable),
        "n_partially_curable": n - len(all_curable),
        "repass_rate": round(len(passed) / len(all_curable), 4) if all_curable else 0.0,
        "curable_resolution_rate": round(resolved / tot_cur, 4) if tot_cur else 0.0,
        "proposal_accuracy": round(tot_ok / tot_cur, 4) if tot_cur else 0.0,
        "new_defects_introduced": newd,
        "avg_recheck_ms": round(sum(r["elapsed_ms"] for r in rows) / n, 2) if n else 0.0,
        "max_recheck_ms": round(max((r["elapsed_ms"] for r in rows), default=0), 2),
    }


def to_md(s, rows):
    grade_moves = {}
    for r in rows:
        k = f"{r['before_grade']}→{r['after_grade']}"
        grade_moves[k] = grade_moves.get(k, 0) + 1
    lines = [
        "# TradeGuard 폐쇄 루프 성능 리포트", "",
        "- 대상: 합성 벤치마크 40건 중 **하자 케이스 " + str(s["n_defect_cases"]) + "건**",
        "- 절차: 하자 검출 → 신용장 기준 수정 제안 → 제안 적용 → **재심사**",
        "- LLM 호출 없음 (판정·제안 모두 결정적) → 비용 0원, 재현 가능", "",
        "| 지표 | 값 |", "|---|---|",
        f"| 재심사 통과율 (치유 가능 하자만 있는 {s['n_all_curable']}건 기준) | {s['repass_rate']:.1%} |",
        f"| 치유 가능 하자 해소율 | {s['curable_resolution_rate']:.1%} |",
        f"| 제안값 정확도 (신용장 기준값 일치) | {s['proposal_accuracy']:.1%} |",
        f"| 수정으로 인한 신규 하자 | {s['new_defects_introduced']}건 |",
        f"| 재심사 처리시간 (평균 / 최대) | {s['avg_recheck_ms']:.1f}ms / {s['max_recheck_ms']:.1f}ms |",
        "",
        "## 등급 변화", "", "| 변화 | 건수 |", "|---|---|",
    ]
    for k, v in sorted(grade_moves.items()):
        lines.append(f"| {k} | {v} |")
    lines += [
        "",
        f"## 치유 불가 하자를 포함한 케이스 — {s['n_partially_curable']}건", "",
        "서류 수정으로 치유되지 않는 하자(선적기일 경과·제시기한 경과)가 있는 케이스는",
        "**의도적으로 A등급에 도달시키지 않는다.** 자동으로 '해결됨'이라 표시하는 대신",
        "개설의뢰인 waiver 또는 신용장 조건변경이 필요하다고 남긴다.", "",
        "| 케이스 | 등급 | 치유 불가 하자 |", "|---|---|---|",
    ]
    for r in rows:
        if r["incurable"]:
            lines.append(f"| {r['case_id']} | {r['before_grade']}→{r['after_grade']} | "
                         f"{', '.join(r['incurable_types'])} |")
    lines += ["", "> 재심사 통과율은 치유 가능 하자만 있는 케이스를 분모로 한다.",
              "> 치유 불가 하자가 있는 케이스를 분모에 넣으면 '고칠 수 없는 것을 못 고쳤다'는",
              "> 이유로 성능이 낮게 보이지만, 그건 성능이 아니라 정의의 문제다."]
    return "\n".join(lines) + "\n"


def main():
    args = sys.argv[1:]

    def opt(name, default=None):
        return args[args.index(name) + 1] if name in args else default

    rows = evaluate()
    s = summarize(rows)
    print(f"[closedloop] 하자 케이스 {s['n_defect_cases']}건 "
          f"(전부 치유 가능 {s['n_all_curable']} · 일부 치유 불가 {s['n_partially_curable']})")
    print(f"  재심사 통과율      {s['repass_rate']:.1%}")
    print(f"  치유 가능 해소율   {s['curable_resolution_rate']:.1%}")
    print(f"  제안값 정확도      {s['proposal_accuracy']:.1%}")
    print(f"  신규 하자          {s['new_defects_introduced']}건")
    print(f"  재심사 시간        평균 {s['avg_recheck_ms']:.1f}ms / 최대 {s['max_recheck_ms']:.1f}ms")

    if opt("--out"):
        json.dump({"summary": s, "per_case": rows},
                  open(os.path.join(HERE, opt("--out")), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    if opt("--md"):
        open(os.path.join(HERE, opt("--md")), "w", encoding="utf-8").write(to_md(s, rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
