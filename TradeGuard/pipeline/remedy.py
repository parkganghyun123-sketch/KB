#!/usr/bin/env python3
"""TradeGuard 수정 제안 — 하자를 '고칠 수 있는 값'으로 번역한다.

설계 원칙 세 가지:

1. **제안값은 LLM이 생성하지 않는다.** 전부 신용장(L/C) 기재값에서 결정적으로 도출한다.
   서류 하자의 정의 자체가 "신용장 조건과 다르다"이므로, 정답은 이미 신용장에 있다.
   LLM에 맡기면 그럴듯하지만 틀린 값을 만들어낼 수 있고, 그건 금융에서 최악이다.

2. **고칠 수 없는 하자를 고칠 수 있다고 말하지 않는다.**
   선적기일 경과·제시기한 경과는 이미 일어난 사실이라 서류를 다시 써도 치유되지 않는다.
   이 구분이 '서류를 아는 도구'와 '문자열을 바꾸는 도구'를 가른다.

3. **적용은 사람이 승인한다.** 이 모듈은 제안만 만들고 반영하지 않는다(apply는 별도 호출).

용어: curable(치유 가능) — ISBP/실무에서 서류 재발행·정정으로 해소되는 하자.
"""
from copy import deepcopy

# 하자 유형 → (치유 가능 여부, 대상 문서, 대상 필드, 조치 주체)
# 치유 불가 유형은 사유를 함께 둔다. 화면에 그대로 노출해 오해를 막는다.
REMEDY_MAP = {
    "CURRENCY_MISMATCH": {
        "curable": True, "doc": "commercial_invoice", "field": "currency",
        "actor_ko": "수출자(송장 재발행)", "basis_field": ":32B: 통화"},
    "AMOUNT_EXCEEDS_LC": {
        "curable": True, "doc": "commercial_invoice", "field": "total_amount",
        "actor_ko": "수출자(송장 재발행)", "basis_field": ":32B: 금액 · :39A: 과부족허용"},
    "BENEFICIARY_NAME_MISMATCH": {
        "curable": True, "doc": "commercial_invoice", "field": "seller.name",
        "actor_ko": "수출자(송장 재발행)", "basis_field": ":59: 수익자"},
    "APPLICANT_NAME_MISMATCH": {
        "curable": True, "doc": "commercial_invoice", "field": "buyer.name",
        "actor_ko": "수출자(송장 재발행)", "basis_field": ":50: 개설의뢰인"},
    "GOODS_DESC_MISMATCH": {
        "curable": True, "doc": "commercial_invoice", "field": "goods[0].description",
        "actor_ko": "수출자(송장 재발행)", "basis_field": ":45A: 상품명세"},
    "BL_SIGNATURE_DEFECT": {
        "curable": True, "doc": "bill_of_lading", "field": "signature.signer_capacity",
        "actor_ko": "운송사(B/L 정정·재발행)", "basis_field": "UCP600 20(a)(i)"},
    "BL_NO_ONBOARD_NOTATION": {
        "curable": True, "doc": "bill_of_lading", "field": "shipped_on_board.indicated",
        "actor_ko": "운송사(ON BOARD 부기)", "basis_field": "UCP600 20(a)(ii)"},
    "PORT_MISMATCH": {
        "curable": True, "doc": "bill_of_lading", "field": None,  # 아래에서 방향 결정
        "actor_ko": "운송사(B/L 정정)", "basis_field": ":44E:/:44F: 항구"},
    # ---- 치유 불가 ----
    "LATE_SHIPMENT": {
        "curable": False,
        "reason_ko": "선적은 이미 완료된 사실이라 서류 수정으로 되돌릴 수 없습니다.",
        "actor_ko": "개설의뢰인 waiver 또는 신용장 조건변경(amendment)"},
    "LC_EXPIRED_OR_LATE_PRESENTATION": {
        "curable": False,
        "reason_ko": "제시기한 경과는 시간의 문제라 서류 수정으로 치유되지 않습니다.",
        "actor_ko": "개설의뢰인 waiver 또는 신용장 조건변경(amendment)"},
}


def _get(obj, path):
    cur = obj
    for part in path.replace("]", "").replace("[", ".").split("."):
        if part == "":
            continue
        cur = cur[int(part)] if part.isdigit() else (cur or {}).get(part)
        if cur is None:
            return None
    return cur


def _set(obj, path, value):
    parts = [p for p in path.replace("]", "").replace("[", ".").split(".") if p != ""]
    cur = obj
    for p in parts[:-1]:
        cur = cur[int(p)] if p.isdigit() else cur.setdefault(p, {})
    last = parts[-1]
    if last.isdigit():
        cur[int(last)] = value
    else:
        cur[last] = value


def _amount_target(lc):
    """신용장 한도. 과부족 허용이 있으면 그 한도까지는 정상이므로 한도를 그대로 쓴다."""
    tol = (lc.get("tolerance") or {}).get("plus_pct", 0) or 0
    return round(lc["amount"] * (1 + tol / 100), 2)


def propose(docs, disc):
    """하자 1건 → 수정 제안 1건. 반환 dict는 UI·평가 스크립트가 그대로 쓴다."""
    lc = docs.get("letter_of_credit") or {}
    inv = docs.get("commercial_invoice") or {}
    bl = docs.get("bill_of_lading") or {}
    dtype = disc["type"]
    spec = REMEDY_MAP.get(dtype)
    base = {"discrepancy_id": disc.get("id"), "type": dtype, "severity": disc.get("severity")}

    if spec is None:
        return {**base, "curable": False, "reason_ko": "자동 제안 규칙이 정의되지 않은 유형입니다.",
                "actor_ko": "은행 협의 필요"}
    if not spec["curable"]:
        return {**base, "curable": False, "reason_ko": spec["reason_ko"], "actor_ko": spec["actor_ko"]}

    doc, field = spec["doc"], spec["field"]
    target = {"commercial_invoice": inv, "bill_of_lading": bl}[doc]

    if dtype == "CURRENCY_MISMATCH":
        after = lc.get("currency")
    elif dtype == "AMOUNT_EXCEEDS_LC":
        after = _amount_target(lc)
    elif dtype == "BENEFICIARY_NAME_MISMATCH":
        after = (lc.get("beneficiary") or {}).get("name")
    elif dtype == "APPLICANT_NAME_MISMATCH":
        after = (lc.get("applicant") or {}).get("name")
    elif dtype == "GOODS_DESC_MISMATCH":
        # 신용장 45A 원문을 그대로 옮긴다. UCP600 18(c)는 '상응'을 요구하므로
        # 신용장 문구와 동일하게 쓰는 것이 가장 안전한 치유다.
        after = lc.get("goods_description")
    elif dtype == "BL_SIGNATURE_DEFECT":
        if not (bl.get("signature") or {}).get("signed"):
            field = "signature.signed"
            after = True
        else:
            after = "agent_for_carrier"
    elif dtype == "BL_NO_ONBOARD_NOTATION":
        after = True
    elif dtype == "PORT_MISMATCH":
        # 어느 쪽 항구가 어긋났는지 근거(evidence)에서 되짚는다.
        f = next((e["field"] for e in disc.get("evidence", [])
                  if e["doc"] == "letter_of_credit" and "port" in e["field"]), "port_of_loading")
        field, after = f, lc.get(f)
    else:
        return {**base, "curable": False, "reason_ko": "제안 도출 불가", "actor_ko": "은행 협의 필요"}

    if after is None:
        return {**base, "curable": False,
                "reason_ko": "신용장에서 기준값을 읽지 못해 제안을 만들 수 없습니다.",
                "actor_ko": "은행 협의 필요"}

    return {**base, "curable": True, "doc": doc, "field": field,
            "before": _get(target, field), "after": after,
            "actor_ko": spec["actor_ko"],
            "basis_ko": f"신용장 {spec['basis_field']} 기준",
            "suggested_fix_ko": disc.get("suggested_fix_ko")}


def propose_all(docs, report):
    return [propose(docs, x) for x in report.get("discrepancies", [])]


def apply_edits(docs, edits):
    """승인된 제안만 반영한 **새 문서 사본**을 만든다(원본 불변).

    edits: propose()가 낸 dict 중 curable=True인 것들. 사용자가 값을 바꿨다면
    after를 수정해 넘기면 그대로 반영된다 — 최종 값의 주인은 사람이다.
    반환: (수정된 docs, 실제 적용 목록)
    """
    out = deepcopy(docs)
    applied = []
    for e in edits or []:
        if not e.get("curable") or not e.get("doc") or not e.get("field"):
            continue
        target = out.get(e["doc"])
        if target is None:
            continue
        before = _get(target, e["field"])
        _set(target, e["field"], e["after"])
        # 송장 총액을 바꾸면 품목 금액·단가도 함께 맞춰야 서류가 내적으로 일관된다.
        if e["doc"] == "commercial_invoice" and e["field"] == "total_amount":
            g = (target.get("goods") or [{}])[0]
            if g.get("quantity"):
                g["unit_price"] = round(e["after"] / g["quantity"], 2)
                g["amount"] = round(g["unit_price"] * g["quantity"], 2)
                target["total_amount"] = g["amount"]
        applied.append({**e, "before": before, "after": _get(target, e["field"])})
    return out, applied
