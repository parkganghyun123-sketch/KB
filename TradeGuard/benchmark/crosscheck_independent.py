#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A-2 독립 교차검증 — 담당: A(데이터).

생성기(generate_cases.py)와 '규칙을 공유하지 않는' 별도 구현으로 40건을 재검사한다.
목적은 정확도 측정이 아니라 정답 라벨 검증이다:
  - 유형 검출: 10개 UCP 하자 유형을 독립 로직(tolerance 반영, 상품명세 correspond 규칙 등)
    으로 판정하여 각 케이스의 ground_truth(defect_types)와 diff.
      EXTRA   = 라벨에 없는데 독립검출됨 -> 우발 하자 후보(정답 라벨 오류 가능)
      MISSING = 라벨엔 있으나 독립검출 안 됨 -> 서류상 미성립/의미판정 필요
  - 부가 정합성 스캔: 유형 라벨 밖의 산술/날짜/운임/컨사이니/L/C내부정합 이상치.

실행:  python3 crosscheck_independent.py
"""
import json, os, re, glob
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = os.path.join(HERE, "cases")

def norm(s):
    return re.sub(r"[^A-Z0-9]", "", s.upper()) if s else ""

def d(s):
    return date.fromisoformat(s) if s else None

def core_goods(desc):
    """상용 부가어(수량/인코텀즈/프로포마참조/포장문구)를 제거하고 '내용 토큰'만 남긴다.
    모델번호(PP-482)는 하이픈 포함 한 토큰으로 보존한다. 송장 토큰이 L/C 토큰의
    부분집합이면 correspond(정상), 초과 토큰이 있으면 명세 불일치."""
    x = desc.upper()
    x = re.sub(r"^\s*\d[\d,]*\s+(CARTONS?|CTNS?|PCS|BOXES)\s+OF\s+", "", x)
    x = re.sub(r"AS PER PROFORMA INVOICE NO\.?\s*[A-Z0-9\-]+", " ", x)
    x = re.sub(r"FOB\s+[A-Z]+\s+INCOTERMS\s*\d*", " ", x)
    x = re.sub(r"\bINCOTERMS\s*\d*\b", " ", x)
    x = re.sub(r"PACKED IN[^,]*", " ", x)
    x = re.sub(r"\b\d[\d,]*\s*(PCS|MT|CTN|CTNS|KG|BOXES|CARTONS|SETS|UNITS)\b", " ", x)
    x = x.replace(",", " ")
    toks = set()
    for t in x.split():
        t = t.strip(".")
        if t and t not in ("MODEL", "OF", "AND", "THE", "FOB", "CIF", "CFR"):
            toks.add(t)
    return toks

def audit(case):
    lc = case["documents"]["letter_of_credit"]
    inv = case["documents"]["commercial_invoice"]
    bl = case["documents"]["bill_of_lading"]
    pres = d(case.get("presentation_date"))
    found, notes = set(), []

    tol = lc.get("tolerance") or {}
    cap = lc["amount"] * (1 + tol.get("plus_pct", 0) / 100.0)
    if inv["currency"] == lc["currency"] and inv["total_amount"] > cap + 1e-6:
        found.add("AMOUNT_EXCEEDS_LC")
        notes.append(f"amount {inv['total_amount']} > cap {round(cap,2)}")

    if inv["currency"] != lc["currency"]:
        found.add("CURRENCY_MISMATCH")
        notes.append(f"cur inv={inv['currency']} lc={lc['currency']}")

    extra_tok = core_goods(inv["goods"][0]["description"]) - core_goods(lc["goods_description"])
    if extra_tok:
        found.add("GOODS_DESC_MISMATCH")
        notes.append(f"goods 송장전용토큰={sorted(extra_tok)}")

    if norm(inv["seller"]["name"]) != norm(lc["beneficiary"]["name"]):
        found.add("BENEFICIARY_NAME_MISMATCH")
        notes.append(f"seller '{inv['seller']['name']}' != benef '{lc['beneficiary']['name']}'")

    b_inv = re.sub(r"^(MESSRS\.?|M/S\.?)\s+", "", inv["buyer"]["name"].upper())
    if norm(b_inv) != norm(lc["applicant"]["name"]):
        found.add("APPLICANT_NAME_MISMATCH")
        notes.append(f"buyer '{inv['buyer']['name']}' != applicant '{lc['applicant']['name']}'")

    sob = bl.get("shipped_on_board", {})
    ship, latest = d(sob.get("date")), d(lc.get("latest_shipment_date"))
    if ship and latest and ship > latest:
        found.add("LATE_SHIPMENT"); notes.append(f"ship {ship} > latest {latest}")

    exp = d(lc["expiry"]["date"])
    ppd = lc.get("presentation_period_days") or 21
    if pres and exp and pres > exp:
        found.add("LC_EXPIRED_OR_LATE_PRESENTATION"); notes.append(f"pres {pres} > expiry {exp}")
    if ship and exp and ship > exp:
        found.add("LC_EXPIRED_OR_LATE_PRESENTATION"); notes.append(f"ship {ship} > expiry {exp}")
    if pres and ship and (pres - ship).days > ppd:
        found.add("LC_EXPIRED_OR_LATE_PRESENTATION"); notes.append(f"pres-ship {(pres-ship).days}d > {ppd}")

    if norm(bl["port_of_loading"]) != norm(lc.get("port_of_loading")):
        found.add("PORT_MISMATCH"); notes.append(f"POL {bl['port_of_loading']} vs {lc.get('port_of_loading')}")
    if norm(bl["port_of_discharge"]) != norm(lc.get("port_of_discharge")):
        found.add("PORT_MISMATCH"); notes.append(f"POD {bl['port_of_discharge']} vs {lc.get('port_of_discharge')}")

    sig = bl.get("signature", {})
    if not sig.get("signed", False):
        found.add("BL_SIGNATURE_DEFECT"); notes.append("bl not signed")
    else:
        ok = sig.get("signer_capacity") in ("carrier", "master", "agent_for_carrier", "agent_for_master")
        if not ok or not sig.get("carrier_name"):
            found.add("BL_SIGNATURE_DEFECT")
            notes.append(f"sig cap={sig.get('signer_capacity')} carrier={sig.get('carrier_name')}")

    if not sob.get("indicated", False):
        found.add("BL_NO_ONBOARD_NOTATION"); notes.append("no on-board notation")

    return found, notes

def extra_checks(case):
    lc = case["documents"]["letter_of_credit"]
    inv = case["documents"]["commercial_invoice"]
    bl = case["documents"]["bill_of_lading"]
    a = []
    g = inv["goods"][0]
    # 실제 상업송장은 인쇄된 수량×단가가 총액과 정확히 일치한다.
    # 과거 허용오차 1%는 단가 반올림으로 생긴 수십 USD 오차를 통과시켜
    # 사람 눈에는 보이는데 라벨에는 없는 '우발 하자'를 남겼다 → 엄격 비교로 전환.
    if g.get("quantity") and g.get("unit_price"):
        calc = round(g["quantity"] * g["unit_price"], 2)
        if abs(calc - g["amount"]) > 0.01:
            a.append(f"산술 qty*price={calc}!=amount={g['amount']}")
    if abs(sum(x["amount"] for x in inv["goods"]) - inv["total_amount"]) > 0.01:
        a.append("산술 goods합!=total_amount")
    # 포장 개수 정합성 — 거래 단위가 CTN이면 수량 자체가 포장 개수여야 한다.
    # (L/C "500 CTN" vs B/L "20 CARTONS" 같은 모순 방지)
    m = re.match(r"\s*([\d,]+)\s*CARTONS", (bl.get("goods_description") or "").upper())
    if m and g.get("unit") == "CTN":
        n = int(m.group(1).replace(",", ""))
        if n != g["quantity"]:
            a.append(f"포장수량 B/L={n} CARTONS != 송장 {g['quantity']} CTN")
    marks = (inv.get("shipping_marks") or "")
    mm = re.search(r"C/NO\.\s*1-([\d,]+)", marks.upper())
    if mm and g.get("unit") == "CTN" and int(mm.group(1).replace(",", "")) != g["quantity"]:
        a.append(f"화인 C/NO. 1-{mm.group(1)} != 송장 {g['quantity']} CTN")
    # 무게 단위(MT)는 "톤 수 // 25"가 아니라 총중량(순중량) 기준 25kg 백 수여야 한다.
    # (100 MT를 "4 CARTONS"로 찍는 물리적으로 불가능한 값 방지 — 독립 재계산으로 검증)
    if g.get("unit") == "MT":
        nm = re.match(r"\s*([\d,]+)\s*(BAGS|CARTONS)", (bl.get("goods_description") or "").upper())
        wm = re.match(r"\s*([\d,.]+)\s*KGS", (inv.get("net_weight") or "").upper())
        if nm and wm:
            n = int(nm.group(1).replace(",", ""))
            net_kg = float(wm.group(1).replace(",", ""))
            expected = max(1, round(net_kg / 25))
            if abs(n - expected) > 1:
                a.append(f"포장수량 B/L={n} != 순중량 {net_kg}kg 기준 예상 {expected}백(25kg/백)")
    iss, exp = d(lc.get("issue_date")), d(lc["expiry"]["date"])
    ship = d(bl.get("shipped_on_board", {}).get("date"))
    latest = d(lc.get("latest_shipment_date"))
    pres = d(case.get("presentation_date"))
    if iss and latest and latest < iss:
        a.append(f"L/C내부 latest_ship {latest} < issue {iss}")
    if iss and exp and exp < iss:
        a.append(f"L/C내부 expiry {exp} < issue {iss}")
    if iss and ship and ship < iss:
        a.append(f"B/L 선적일 {ship} < L/C 발행일 {iss} (14(i) 허용이나 비현실적)")
    if pres and ship and pres < ship:
        a.append(f"제시일 {pres} < 선적일 {ship}")
    amt = lc["amount"]
    if amt < 30000 or amt > 300000:
        a.append(f"거래규모 {amt:,.0f} (스펙 30k~300k 이탈)")
    reqs = " ".join((r.get("requirements") or "") for r in lc.get("documents_required", [])).upper()
    ft = bl.get("freight_terms")
    if "FREIGHT COLLECT" in reqs and ft and ft != "COLLECT":
        a.append(f"운임 COLLECT요구 vs B/L={ft}")
    if "FREIGHT PREPAID" in reqs and ft and ft != "PREPAID":
        a.append(f"운임 PREPAID요구 vs B/L={ft}")
    return a

def main():
    files = sorted(glob.glob(os.path.join(CASES, "*.json")))
    problems = []
    print(f"{'case':13} {'label':7} {'라벨':44} 독립검출")
    print("-" * 116)
    for fp in files:
        c = json.load(open(fp, encoding="utf-8"))
        gt = set(c["defect_types"])
        found, notes = audit(c)
        extra, missing = found - gt, gt - found
        flag = ""
        if extra:
            flag += f"  [EXTRA={sorted(extra)}]"
        if missing:
            flag += f"  [MISSING={sorted(missing)}]"
        print(f"{c['case_id']:13} {c['label']:7} {str(sorted(gt)):44} {str(sorted(found))}{flag}")
        if extra or missing:
            problems.append((c["case_id"], sorted(extra), sorted(missing), notes))

    print("\n" + "=" * 60)
    print("[유형 라벨 교차검증]")
    if not problems:
        print("  40건 전부 라벨==독립검출. 우발 하자 0건, 미검출 0건.")
    else:
        for cid, ex, mi, notes in problems:
            print(f"  [{cid}] EXTRA={ex} MISSING={mi}")
            for n in notes:
                print(f"      - {n}")

    print("\n[부가 정합성 이상치]")
    any_a = False
    for fp in files:
        c = json.load(open(fp, encoding="utf-8"))
        a = extra_checks(c)
        if a:
            any_a = True
            print(f"  [{c['case_id']}] " + " | ".join(a))
    if not any_a:
        print("  이상치 없음.")

if __name__ == "__main__":
    main()
