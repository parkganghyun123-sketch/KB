#!/usr/bin/env python3
"""TradeGuard 종단(End-to-End) 평가 — 서류 이미지 → 추출 → 하자검출 → 정답 대조

evaluate.py와의 차이:
  · evaluate.py     : 정답 JSON을 검출기에 직접 투입 → **규칙 정합성(상한 성능)**
  · evaluate_e2e.py : PNG 이미지부터 시작 → **실제 서비스 성능** ← 기술설명서에 쓸 수치

산출 지표 2종:
  ① 필드 추출 정확도 — (정확 추출 필드 수) / (정답 필드 수)
  ② 하자 검출 정밀도/재현율/F1 — 추출 오류가 전파된 상태에서의 성능

전제: 케이스 PNG가 렌더링돼 있어야 함
  python3 generate_cases.py --out cases --render     # 또는
  python3 ../render/render.py cases/*.json --out cases/rendered --png

사용법:
  python3 evaluate_e2e.py --cases cases --images cases/rendered --limit 5     # 비용 가드
  python3 evaluate_e2e.py --cases cases --images cases/rendered --out e2e.json --md e2e.md
"""
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
from detect import build_report, d as parse_date  # noqa: E402
from extract import classify, extract as extract_doc  # noqa: E402
from llm import get_client, image_block, load_env  # noqa: E402

DOC_KEYS = [("letter_of_credit", "lc"), ("commercial_invoice", "invoice"), ("bill_of_lading", "bl")]
# 대략 비용(USD/1M tokens) — 추정 출력용. 실제 청구액과 다를 수 있음
COST = {"gpt-4o": (2.5, 10.0), "gpt-4o-mini": (0.15, 0.6),
        "claude-sonnet-4-5": (3.0, 15.0), "claude-haiku-4-5-20251001": (1.0, 5.0)}
EST_TOKENS_PER_DOC = (4200, 900)  # (입력, 출력) 대략치


# 채점 제외 필드 — 서류에 인쇄되지 않아 추출이 원천 불가능한 것들.
# 정답 JSON에는 있지만 이미지에 없으므로, 감점하면 모델을 부당하게 벌하는 셈이 된다.
#   country : 주소 문자열에 국가명은 있으나 ISO 코드(KR/VN)는 어디에도 인쇄되지 않음
#   doc_type: 파이프라인이 분류 단계에서 결정 (별도 지표로 측정)
EXCLUDE_KEYS = {"field_confidence", "unreadable_fields", "country", "doc_type"}


def flatten(obj, prefix=""):
    """중첩 JSON → {경로: 값} 평탄화. 비교 단위를 필드로 통일한다."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in EXCLUDE_KEYS:
                continue
            out.update(flatten(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(flatten(v, f"{prefix}[{i}]"))
    elif obj is not None:
        out[prefix] = obj
    return out


def norm_val(v):
    return str(v).strip().upper() if not isinstance(v, (int, float)) else round(float(v), 2)


def field_accuracy(truth: dict, got: dict):
    """반환: (정확 필드 수, 정답 필드 수, 틀린 필드 목록)"""
    t, g = flatten(truth), flatten(got)
    wrong = []
    hit = 0
    for k, tv in t.items():
        gv = g.get(k)
        if gv is not None and norm_val(gv) == norm_val(tv):
            hit += 1
        else:
            wrong.append((k, tv, gv))
    return hit, len(t), wrong


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def estimate_cost(n_docs, main_model, classify_model_name):
    ci, co = COST.get(main_model, (3.0, 15.0))
    hi, ho = COST.get(classify_model_name, (0.2, 0.8))
    ti, to = EST_TOKENS_PER_DOC
    main = n_docs * (ti * ci + to * co) / 1e6
    cls = n_docs * (1300 * hi + 30 * ho) / 1e6
    return main + cls


def main():
    load_env()
    args = sys.argv[1:]

    def opt(name, default=None):
        return args[args.index(name) + 1] if name in args else default

    cases_dir = Path(opt("--cases", "cases"))
    images_dir = Path(opt("--images", str(cases_dir / "rendered")))
    limit = int(opt("--limit", "0") or 0)
    out_path = opt("--out")
    md_path = opt("--md")

    client = get_client()
    if client is None:
        sys.exit("LLM 키 없음. .env를 확인하세요 (진단: python3 ../pipeline/llm.py --check)")

    files = sorted(cases_dir.glob("*.json"))
    if limit:
        # 하자·정상을 균형 있게 섞어 뽑는다 (앞에서 자르면 편향됨)
        defects = [f for f in files if f.stem.startswith("DEFECT")][: (limit + 1) // 2]
        cleans = [f for f in files if f.stem.startswith("CLEAN")][: limit // 2]
        files = sorted(defects + cleans)
    if not files:
        sys.exit(f"케이스 없음: {cases_dir}")

    from llm import DEFAULTS, classify_model
    import os
    main_model = os.environ.get("TG_MODEL") or DEFAULTS[client.name]["main"]
    n_docs = len(files) * 3
    est = estimate_cost(n_docs, main_model, classify_model())
    print(f"[e2e] 프로바이더 {client.name} · 모델 {main_model} · 케이스 {len(files)}건({n_docs}장)")
    print(f"[e2e] 예상 비용 약 ${est:.2f} — 중단하려면 지금 Ctrl+C (3초 후 시작)\n")
    time.sleep(3)

    hit_sum = tot_sum = 0
    tp = fp = fn = 0
    cls_ok = cls_tot = 0
    per_case, worst_fields = [], defaultdict(int)
    t0 = time.time()

    for i, f in enumerate(files, 1):
        case = json.loads(f.read_text(encoding="utf-8"))
        cid = case["case_id"]
        print(f"[{i}/{len(files)}] {cid} … ", end="", flush=True)
        extracted, c_hit, c_tot = {}, 0, 0
        try:
            for doc_key, suffix in DOC_KEYS:
                img = images_dir / f"{cid}_{suffix}.png"
                if not img.exists():
                    raise FileNotFoundError(f"이미지 없음: {img} (렌더링 먼저 실행)")
                blocks = [image_block(img)]
                cls_tot += 1
                if classify(client, blocks) == doc_key:
                    cls_ok += 1
                got, _ = extract_doc(client, blocks, doc_key)
                extracted[doc_key] = got
                h, t, wrong = field_accuracy(case["documents"][doc_key], got)
                c_hit += h
                c_tot += t
                for k, _, _ in wrong:
                    worst_fields[f"{doc_key}.{k}"] += 1
        except Exception as e:
            print(f"❌ {str(e)[:120]}")
            per_case.append({"case_id": cid, "error": str(e)[:200]})
            continue

        rep = build_report(cid, extracted, parse_date(case.get("presentation_date")))
        found = {x["type"] for x in rep["discrepancies"]}
        expect = {x["type"] for x in case["ground_truth"]["discrepancies"]}
        c_tp, c_fp, c_fn = found & expect, found - expect, expect - found
        tp += len(c_tp); fp += len(c_fp); fn += len(c_fn)
        hit_sum += c_hit; tot_sum += c_tot

        acc = c_hit / c_tot if c_tot else 0
        mark = "✅" if not c_fp and not c_fn else "⚠️"
        detail = ""
        if c_fp:
            detail += f"  FP={sorted(c_fp)}"
        if c_fn:
            detail += f"  FN={sorted(c_fn)}"
        print(f"{mark} 필드 {acc:.0%} · TP{len(c_tp)} FP{len(c_fp)} FN{len(c_fn)}{detail}")
        per_case.append({"case_id": cid, "field_accuracy": round(acc, 4),
                         "tp": sorted(c_tp), "fp": sorted(c_fp), "fn": sorted(c_fn),
                         "grade": rep["overall_risk"]["grade"],
                         "grade_truth": case["ground_truth"]["overall_risk"]["grade"]})

    p, r, f1 = prf(tp, fp, fn)
    fa = hit_sum / tot_sum if tot_sum else 0
    ok_cases = [c for c in per_case if "error" not in c]
    grade_acc = sum(1 for c in ok_cases if c["grade"] == c["grade_truth"]) / len(ok_cases) if ok_cases else 0

    print("\n" + "=" * 52)
    print("=== 종단 성능 (기술설명서에 인용할 수치) ===")
    print(f"  ① 필드 추출 정확도 : {fa:.1%}  ({hit_sum}/{tot_sum} 필드)")
    print(f"  ② 문서 분류 정확도 : {cls_ok / cls_tot:.1%}  ({cls_ok}/{cls_tot} 장)")
    print(f"  ③ 하자 검출        : 정밀도 {p:.1%} · 재현율 {r:.1%} · F1 {f1:.3f}")
    print(f"  ④ 등급 일치율      : {grade_acc:.1%}")
    print(f"  소요 {time.time() - t0:.0f}초 · 실패 케이스 {len(per_case) - len(ok_cases)}건")
    if worst_fields:
        print("\n=== 추출 오류가 잦은 필드 TOP 8 (프롬프트 개선 대상) ===")
        for k, v in sorted(worst_fields.items(), key=lambda x: -x[1])[:8]:
            print(f"  {v:3d}회  {k}")

    fp_types = defaultdict(int)
    for c in ok_cases:
        for t in c.get("fp", []):
            fp_types[t] += 1
    if fp_types:
        print("\n=== 오탐(FP) 유형별 — 정밀도를 깎는 주범 ===")
        for k, v in sorted(fp_types.items(), key=lambda x: -x[1]):
            print(f"  {v:3d}건  {k}")

    metrics = {"provider": client.name, "model": main_model, "n_cases": len(files),
               "field_accuracy": round(fa, 4), "classify_accuracy": round(cls_ok / cls_tot, 4) if cls_tot else 0,
               "discrepancy": {"tp": tp, "fp": fp, "fn": fn, "precision": round(p, 4),
                               "recall": round(r, 4), "f1": round(f1, 4)},
               "grade_accuracy": round(grade_acc, 4),
               "worst_fields": dict(sorted(worst_fields.items(), key=lambda x: -x[1])[:20]),
               "per_case": per_case}
    if out_path:
        Path(out_path).write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[e2e] 지표 저장: {out_path}")
    if md_path:
        Path(md_path).write_text(
            f"""# TradeGuard 종단 성능 리포트

- 파이프라인: 서류 이미지 → 추출({client.name} / {main_model}) → 하자 검출 → 정답 대조
- 대상: 합성 벤치마크 {len(files)}건 ({len(files) * 3}장)

| 지표 | 값 |
|---|---|
| 필드 추출 정확도 | {fa:.1%} |
| 문서 분류 정확도 | {cls_ok / cls_tot:.1%} |
| 하자 검출 정밀도 | {p:.1%} |
| 하자 검출 재현율 | {r:.1%} |
| 하자 검출 F1 | {f1:.3f} |
| 등급 일치율 | {grade_acc:.1%} |

※ 본 수치는 서류 이미지를 입력으로 하는 종단 성능이며, 규칙 정합성 검증 결과
(`accuracy_report.md`)와는 측정 대상이 다르다.
""", encoding="utf-8")
        print(f"[e2e] 리포트 저장: {md_path}")


if __name__ == "__main__":
    main()
