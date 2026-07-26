#!/usr/bin/env python3
"""TradeGuard 하자 검출 엔진 — documents JSON → discrepancy_report JSON

설계 원칙 (하이브리드):
  · 결정적 비교(날짜·금액·통화·항구·서명·본선적재)는 **코드**로 판정 — LLM 환각 원천 차단
  · 의미 비교(상품명세 상응 여부, 회사명 동일성)는 **LLM**으로 판정
  · ANTHROPIC_API_KEY 없으면 토큰 휴리스틱 폴백 — 오프라인 테스트/CI 용
  · 모든 하자에 UCP600 조항 인용(ucp600_kb.json) 부착

사용법:
  python3 detect.py ../samples/DEFECT-001.json [--out report.json]
  입력: benchmark_case JSON(documents 키 존재 시) 또는 {letter_of_credit, commercial_invoice, bill_of_lading} JSON
  ground_truth가 있으면 판정 결과와 자동 대조(TP/FN/FP)를 출력한다.
"""
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

KB = json.loads((Path(__file__).parent / "ucp600_kb.json").read_text(encoding="utf-8"))

SEVERITY_PENALTY = {"high": 25, "medium": 10, "low": 3}


# ---------- 유틸 ----------
def norm(s):
    return re.sub(r"\s+", " ", (s or "").upper()).strip()


def norm_nopunct(s):
    return re.sub(r"[^A-Z0-9 ]", "", norm(s)).strip()


def d(iso):
    return date.fromisoformat(iso) if iso else None


def ref_tokens(s):
    """모델번호·서류번호류 토큰 추출: AH-720, PI-2605, V.088E 등"""
    return set(re.findall(r"\b[A-Z]{1,6}[-.]?\d+[A-Z0-9-]*\b", norm(s)))


def disc(dtype, sev, desc, evidence, kb_key, fix):
    kb = KB[kb_key]
    return {
        "type": dtype, "severity": sev, "description_ko": desc,
        "evidence": evidence,
        "ucp_basis": {"article": kb["article"], "quote_ko": kb["quote_ko"]},
        "suggested_fix_ko": fix,
    }


def ev(doc, field, value):
    return {"doc": doc, "field": field, "value": str(value)}


# ---------- LLM 의미 비교 (폴백 포함) ----------
def _llm_client():
    """Claude/GPT 무관. 키·SDK가 없으면 None → 호출부는 휴리스틱 폴백으로 동작"""
    try:
        from llm import get_client, load_env
        load_env()
        return get_client()
    except ImportError:
        return None


def _token_conflict(lc_desc, inv_desc):
    """참조 토큰(모델번호·PI번호 등) 충돌 탐지. 반환: (송장토큰, 신용장토큰) 또는 None

    UCP600 18(c)의 '상응(correspond)'은 동일(identical)이 아니다. 송장이 신용장 명세의
    일부를 **생략**하는 것은 허용되며, 값이 **다르게** 기재된 경우만 하자다.
    따라서 '없음'이 아니라 '다름'만 잡는다."""
    lc_t, inv_t = ref_tokens(lc_desc), ref_tokens(inv_desc)
    for t in sorted(inv_t - lc_t):
        prefix = re.match(r"[A-Z]+", t).group()
        conflict = [u for u in lc_t if re.match(r"[A-Z]+", u).group() == prefix and u != t]
        if conflict:
            return t, conflict[0]
    return None


def judge_goods_desc(lc_desc, inv_desc):
    """송장 명세가 L/C 45A와 '상응'하는가. 반환: None(정상) 또는 (severity, 사유, 충돌부분)

    판정 순서 (결정적 우선 — LLM 과탐 방지):
      1) 완전 일치        → 정상
      2) 참조 토큰 충돌 無 → 정상 (생략은 허용). **LLM 호출하지 않음 = 비용 절감 + 오탐 0**
      3) 토큰 충돌 有      → 하자 확정. LLM은 '설명 생성'에만 사용 (판정은 코드가 함)
    """
    if norm(inv_desc) == norm(lc_desc):
        return None
    conflict = _token_conflict(lc_desc, inv_desc)
    if conflict is None:
        return None  # 생략·표현 차이일 뿐 — UCP600 18(c)상 정상
    inv_tok, lc_tok = conflict
    reason = f"송장의 '{inv_tok}'가 신용장 45A의 '{lc_tok}'와 불일치"

    # LLM은 판정을 뒤집지 않고, 사람이 읽을 설명만 다듬는다 (선택적)
    client = _llm_client()
    if client:
        try:
            text = client.complete(
                system=("UCP600 18(c) 하자를 한 문장으로 설명하는 은행 서류심사역. "
                        "판정은 이미 '하자'로 확정됐으니 뒤집지 말고 설명만 작성하라. "
                        'JSON만 출력: {"reason_ko": "..."}'),
                user=(f"<lc_45A>{lc_desc}</lc_45A>\n<invoice_desc>{inv_desc}</invoice_desc>\n"
                      f"불일치 부분: 송장 '{inv_tok}' vs 신용장 '{lc_tok}'"),
                max_tokens=200, json_only=True)
            r = json.loads(re.search(r"\{.*\}", text, re.S).group())
            reason = r.get("reason_ko") or reason
        except Exception:
            pass  # 설명 생성 실패 → 코드가 만든 기본 문구 사용
    return ("high", reason, inv_tok)


def judge_name(lc_name, doc_name):
    """회사명 동일성. 반환: None(동일) / ('medium'|'high', 사유)"""
    if norm(lc_name) == norm(doc_name):
        return None
    if norm_nopunct(lc_name) == norm_nopunct(doc_name):
        return ("medium", "구두점·축약 차이 (예: CO., LTD ↔ CO LTD) — 은행 재량이나 지적 위험 있음")
    client = _llm_client()
    if client:
        try:
            text = client.complete(
                system=('두 회사명이 동일 법인을 지칭하는지 판정. 오탈자·다른 회사면 불일치. '
                        'JSON만 출력: {"same": true|false, "reason_ko": "..."}'),
                user=f"A: {lc_name}\nB: {doc_name}", max_tokens=200, json_only=True)
            r = json.loads(re.search(r"\{.*\}", text, re.S).group())
            if r.get("same"):
                return None
            return ("high", r.get("reason_ko", "명칭 불일치"))
        except Exception:
            pass
    return ("high", "명칭 불일치")


# ---------- 검사 규칙 ----------
def run_checks(docs, presentation_date=None):
    """presentation_date: 서류 제시(예정)일. 미지정 시 오늘 날짜를 사용한다."""
    presentation_date = presentation_date or date.today()
    lc = docs.get("letter_of_credit") or {}
    inv = docs.get("commercial_invoice") or {}
    bl = docs.get("bill_of_lading") or {}
    out = []

    # 1) 통화 (18a3) — 결정적
    if lc.get("currency") and inv.get("currency") and lc["currency"] != inv["currency"]:
        out.append(disc("CURRENCY_MISMATCH", "high",
                        f"송장 통화({inv['currency']})가 신용장 통화({lc['currency']})와 다릅니다.",
                        [ev("letter_of_credit", "currency", lc["currency"]),
                         ev("commercial_invoice", "currency", inv["currency"])],
                        "UCP600_18a3", "송장을 신용장과 동일한 통화로 재발행하세요."))

    # 2) 금액 초과 (18b/30b) — 결정적
    if lc.get("amount") is not None and inv.get("total_amount") is not None:
        tol = lc.get("tolerance") or {}
        limit = lc["amount"] * (1 + tol.get("plus_pct", 0) / 100)
        if inv["total_amount"] > limit + 1e-9:
            out.append(disc("AMOUNT_EXCEEDS_LC", "high",
                            f"송장 금액({inv['currency']} {inv['total_amount']:,.2f})이 신용장 허용 한도"
                            f"({lc['currency']} {limit:,.2f})를 초과합니다.",
                            [ev("letter_of_credit", "amount", lc["amount"]),
                             ev("commercial_invoice", "total_amount", inv["total_amount"])],
                            "UCP600_18b", "송장 금액을 신용장 한도 이내로 수정하거나 초과분을 별도 결제(T/T)로 분리하세요."))

    # 3) 수익자/개설의뢰인 명칭 (18a1/18a2) — 의미 비교
    if lc.get("beneficiary", {}).get("name") and inv.get("seller", {}).get("name"):
        j = judge_name(lc["beneficiary"]["name"], inv["seller"]["name"])
        if j:
            out.append(disc("BENEFICIARY_NAME_MISMATCH", j[0],
                            f"송장 발행인이 신용장 수익자와 불일치합니다 — {j[1]}",
                            [ev("letter_of_credit", "beneficiary.name", lc["beneficiary"]["name"]),
                             ev("commercial_invoice", "seller.name", inv["seller"]["name"])],
                            "UCP600_18a1", "송장의 발행인 명의를 신용장 59필드 원문과 동일하게 수정하세요."))
    if lc.get("applicant", {}).get("name") and inv.get("buyer", {}).get("name"):
        j = judge_name(lc["applicant"]["name"], inv["buyer"]["name"])
        if j:
            out.append(disc("APPLICANT_NAME_MISMATCH", j[0],
                            f"송장 수신인이 신용장 개설의뢰인과 불일치합니다 — {j[1]}",
                            [ev("letter_of_credit", "applicant.name", lc["applicant"]["name"]),
                             ev("commercial_invoice", "buyer.name", inv["buyer"]["name"])],
                            "UCP600_18a2", "송장의 수신인 명의를 신용장 50필드 원문과 동일하게 수정하세요."))

    # 4) 상품명세 상응 (18c) — 의미 비교
    if lc.get("goods_description") and inv.get("goods"):
        inv_desc = " / ".join(g.get("description", "") for g in inv["goods"])
        j = judge_goods_desc(lc["goods_description"], inv_desc)
        if j:
            out.append(disc("GOODS_DESC_MISMATCH", j[0],
                            f"송장 상품명세가 신용장 45A와 상응하지 않습니다 — {j[1]}",
                            [ev("letter_of_credit", "goods_description", lc["goods_description"]),
                             ev("commercial_invoice", "goods[].description", inv_desc)],
                            "UCP600_18c", "송장을 재발행하여 상품명세를 신용장 45A 원문과 일치시키세요."))

    # 5) 선적기일 (14조·44C) — 결정적
    ship_date = d((bl.get("shipped_on_board") or {}).get("date"))
    latest = d(lc.get("latest_shipment_date"))
    if ship_date and latest and ship_date > latest:
        out.append(disc("LATE_SHIPMENT", "high",
                        f"선적일({ship_date})이 신용장 최종선적기일({latest})을 {(ship_date - latest).days}일 초과했습니다.",
                        [ev("letter_of_credit", "latest_shipment_date", latest),
                         ev("bill_of_lading", "shipped_on_board.date", ship_date)],
                        "UCP600_14_44C",
                        "선적기일 경과는 서류 수정으로 치유할 수 없습니다. 개설의뢰인의 하자 수락(waiver) 또는 조건변경(amendment)을 요청하세요."))

    # 6) 유효기일/제시기간 (14c·6d) — 결정적 (제시 가능 여부 사전 경고)
    expiry = d((lc.get("expiry") or {}).get("date"))
    pp = lc.get("presentation_period_days") or 21
    if ship_date and expiry:
        deadline = min(expiry, date.fromordinal(ship_date.toordinal() + pp))
        if deadline < presentation_date:
            out.append(disc("LC_EXPIRED_OR_LATE_PRESENTATION", "high",
                            f"서류 제시일({presentation_date})이 제시기한({deadline})을 경과했습니다 "
                            f"(선적일+{pp}일과 유효기일 중 이른 날).",
                            [ev("letter_of_credit", "expiry.date", expiry),
                             ev("bill_of_lading", "shipped_on_board.date", ship_date),
                             ev("_input", "presentation_date", presentation_date)],
                            "UCP600_14c", "즉시 은행과 하자 네고 여부를 협의하고 개설의뢰인 waiver를 요청하세요."))

    # 7) 항구 (20a3) — 결정적
    for f_lc, f_bl in [("port_of_loading", "port_of_loading"), ("port_of_discharge", "port_of_discharge")]:
        if lc.get(f_lc) and bl.get(f_bl) and norm_nopunct(lc[f_lc]) != norm_nopunct(bl[f_bl]):
            out.append(disc("PORT_MISMATCH", "medium",
                            f"B/L의 {f_bl}({bl[f_bl]})가 신용장({lc[f_lc]})과 다릅니다.",
                            [ev("letter_of_credit", f_lc, lc[f_lc]), ev("bill_of_lading", f_bl, bl[f_bl])],
                            "UCP600_20a3", "B/L의 항구 표기를 신용장 44E/44F와 일치하도록 운송사에 정정을 요청하세요."))

    # 8) 본선적재 표기 (20a2) — 결정적
    sob = bl.get("shipped_on_board") or {}
    if bl and not sob.get("indicated"):
        out.append(disc("BL_NO_ONBOARD_NOTATION", "high",
                        "B/L에 본선적재 표기(사전인쇄 문언 또는 on board 부기)가 없습니다.",
                        [ev("bill_of_lading", "shipped_on_board.indicated", sob.get("indicated"))],
                        "UCP600_20a2", "운송사에 선적일이 명기된 ON BOARD 부기를 요청하세요."))

    # 9) 서명·자격 (20a1) — 결정적
    sig = bl.get("signature") or {}
    if bl:
        if not sig.get("signed"):
            out.append(disc("BL_SIGNATURE_DEFECT", "high", "B/L에 서명이 없습니다.",
                            [ev("bill_of_lading", "signature.signed", sig.get("signed"))],
                            "UCP600_20a1", "운송인/선장/대리인의 서명이 포함된 B/L 재발행을 요청하세요."))
        elif sig.get("signer_capacity") in (None, "unclear"):
            # 판정 기준은 '서명자 자격' 하나로 한정한다.
            # carrier_name은 로고·헤더에 인쇄돼 판독 실패가 잦아, 이를 하자 근거로 쓰면
            # 추출 오류가 곧바로 오탐이 된다. 자격 표시 유무가 UCP600 20(a)(i)의 핵심 요건이다.
            out.append(disc("BL_SIGNATURE_DEFECT", "medium",
                            "B/L 서명자의 자격(운송인/선장/대리인) 표시가 없습니다.",
                            [ev("bill_of_lading", "signature.signer_capacity", sig.get("signer_capacity")),
                             ev("bill_of_lading", "signature.carrier_name", sig.get("carrier_name"))],
                            "UCP600_20a1",
                            f"운송사에 'AS AGENT FOR THE CARRIER, {sig.get('carrier_name') or '(운송인 명칭)'}' 등 자격 문구가 명기된 B/L 재발행을 요청하세요."))
    return out


# ---------- 리포트 조립 ----------
def grade(discs):
    score = max(0, 100 - sum(SEVERITY_PENALTY[x["severity"]] for x in discs))
    high = sum(1 for x in discs if x["severity"] == "high")
    if not discs:
        g = "A"
    elif high == 0 and score >= 80:
        g = "B"
    elif high <= 1 and score >= 50:
        g = "C"
    else:
        g = "D"
    return g, score


def build_report(case_id, docs, presentation_date=None):
    discs = run_checks(docs, presentation_date)
    for i, x in enumerate(discs, 1):
        x["id"] = f"DISC-{i:03d}"
    g, score = grade(discs)
    high = sum(1 for x in discs if x["severity"] == "high")
    summary = ("하자가 발견되지 않았습니다. 서류 정합성이 양호합니다." if not discs else
               f"총 {len(discs)}건의 하자 발견 (HIGH {high}건). " +
               ("지급거절 위험이 높으므로 네고 전 하자 치유 또는 개설의뢰인 waiver 확인이 필요합니다."
                if g in "CD" else "경미한 하자로, 수정 후 제시를 권장합니다."))
    return {
        "case_id": case_id,
        "documents_checked": [k for k in ("letter_of_credit", "commercial_invoice", "bill_of_lading") if docs.get(k)],
        "discrepancies": discs,
        "overall_risk": {"grade": g, "score": score, "summary_ko": summary},
    }


def compare_ground_truth(report, gt):
    found = {x["type"] for x in report["discrepancies"]}
    expected = {x["type"] for x in gt["discrepancies"]}
    print("\n=== ground_truth 대조 ===")
    print(f"  TP(정검출): {sorted(found & expected) or '-'}")
    print(f"  FN(미검출): {sorted(expected - found) or '-'}")
    print(f"  FP(오검출): {sorted(found - expected) or '-'}")
    print(f"  등급: 판정 {report['overall_risk']['grade']} / 정답 {gt['overall_risk'].get('grade')}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit("사용법: python3 detect.py <case.json> [--out report.json]")
    data = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    docs = data.get("documents", data)
    case_id = data.get("case_id", Path(args[0]).stem)
    c = _llm_client()
    mode = f"LLM 의미비교 활성({c.name})" if c else "오프라인 휴리스틱 폴백"
    pres = d(data.get("presentation_date"))
    print(f"[detect] case={case_id} · 판정 모드: {mode} · 제시일: {pres or '오늘(' + str(date.today()) + ')'}")
    report = build_report(case_id, docs, pres)
    out_path = None
    if "--out" in sys.argv:
        out_path = Path(sys.argv[sys.argv.index("--out") + 1])
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[detect] 리포트 저장: {out_path}")
    print(json.dumps(report["overall_risk"], ensure_ascii=False, indent=2))
    for x in report["discrepancies"]:
        print(f"  - [{x['severity'].upper():6s}] {x['type']:32s} {x['ucp_basis']['article']}")
    if data.get("ground_truth"):
        compare_ground_truth(report, data["ground_truth"])


if __name__ == "__main__":
    main()
