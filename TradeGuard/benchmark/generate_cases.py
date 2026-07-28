#!/usr/bin/env python3
"""TradeGuard 합성 벤치마크 생성기 — 40건(하자 20 · 정상 20)

prompts/02_synthetic_benchmark.md의 배분표를 코드로 구현한다.
결정적 생성(시드 고정)이므로 API 키 없이 즉시 40건을 만들 수 있고, 재현 가능하다.

  · 정상 20건 = 완전정상 16 + "함정 정상" 4 (하자처럼 보이나 UCP600상 정상 → 오탐 측정용)
  · 하자 20건 = 10개 유형 배분 + 복합 하자 2건
  · 각 케이스에 ground_truth(discrepancy_report 형식) 자동 부착

사용법:
  python3 generate_cases.py --out cases            # 40건 JSON 생성
  python3 generate_cases.py --out cases --render   # 생성 후 render.py로 HTML까지
"""
import json
import math
import random
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

SEED = 20260803
ROOT = Path(__file__).resolve().parent.parent

# ---------- 시나리오 풀 (가상 기업명) ----------
INDUSTRIES = [
    ("CNC MACHINED ALUMINUM HOUSING PARTS", "AH", "PCS", 21.0, "7616.99"),
    ("POLYPROPYLENE RESIN COMPOUND", "PP", "MT", 1450.0, "3902.10"),
    ("COTTON KNITTED T-SHIRTS", "TS", "PCS", 6.4, "6109.10"),
    ("INSTANT RAMEN NOODLE (BOX OF 40)", "RN", "CTN", 18.5, "1902.30"),
    ("LITHIUM-ION BATTERY CELLS 3.7V", "BC", "PCS", 3.2, "8507.60"),
    ("AUTOMOTIVE RUBBER SEAL KIT", "RS", "SET", 9.8, "4016.93"),
    ("STAINLESS STEEL PIPE FITTINGS", "SF", "PCS", 12.6, "7307.29"),
    ("LED PANEL LIGHT 40W", "LP", "PCS", 15.3, "9405.11"),
]
EXPORTERS = [
    ("HANSOL PRECISION CO., LTD.", "217 MIEUM SANDAN-RO, GANGSEO-GU, BUSAN, KOREA"),
    ("DAEYANG POLYMER CO., LTD.", "88 GONGDAN-RO, YEOSU-SI, JEONNAM, KOREA"),
    ("SEJIN TEXTILE CORPORATION", "45 SANDAN 3-RO, DAEGU, KOREA"),
    ("NARAE FOODS CO., LTD.", "12 SIKPUM-RO, IKSAN-SI, JEONBUK, KOREA"),
    ("KOWON ENERGY CELL CO., LTD.", "300 TECHNO 2-RO, DAEJEON, KOREA"),
]
# 업종코드(INDUSTRIES 3번째 원소) → 실제로 그 품목을 취급할 법한 수출자만 매칭
# (예: KOWON ENERGY CELL은 배터리만 수출, 라면(NARAE FOODS)을 수출하지 않도록 고정)
EXPORTERS_BY_CODE = {
    "AH": [EXPORTERS[0]],  # HANSOL PRECISION → 정밀가공 부품
    "PP": [EXPORTERS[1]],  # DAEYANG POLYMER → 수지/폴리머
    "TS": [EXPORTERS[2]],  # SEJIN TEXTILE → 섬유
    "RN": [EXPORTERS[3]],  # NARAE FOODS → 식품
    "BC": [EXPORTERS[4]],  # KOWON ENERGY CELL → 배터리
    "RS": [EXPORTERS[1]],  # DAEYANG POLYMER → 고무/폴리머
    "SF": [EXPORTERS[0]],  # HANSOL PRECISION → 금속가공
    "LP": [EXPORTERS[0]],  # HANSOL PRECISION → 정밀전자부품
}

MIN_AMOUNT, MAX_AMOUNT = 30_000, 300_000

# 업종코드 → 단위(unit)당 순중량(kg). net/gross weight, packages 산정에 사용
WEIGHT_KG_PER_UNIT = {
    "AH": 0.42,     # PCS, CNC 알루미늄 하우징
    "PP": 1000.0,   # MT, 수지 컴파운드 (1 MT = 1000kg)
    "TS": 0.16,     # PCS, 니트 티셔츠
    "RN": 7.5,      # CTN, 라면 40개입 박스
    "BC": 0.048,    # PCS, 리튬이온 배터리 셀
    "RS": 1.3,      # SET, 고무 씰 키트
    "SF": 0.85,     # PCS, 스테인리스 파이프 피팅
    "LP": 1.9,      # PCS, LED 패널 라이트
}


def qty_for_price(price, rng):
    """거래금액(qty*price)이 스펙 범위(MIN_AMOUNT~MAX_AMOUNT)에 들도록 qty를 역산한다."""
    low = max(1, math.ceil(MIN_AMOUNT / price))
    high = max(low, math.floor(MAX_AMOUNT / price))
    qty = rng.randint(low, high)
    step = 100 if high >= 1000 else (10 if high >= 100 else 1)
    qty = min(high, max(low, round(qty / step) * step))
    return qty
BUYERS = [
    ("MEKONG INDUSTRIAL TRADING CO., LTD", "88 NGUYEN HUE BLVD, DISTRICT 1, HO CHI MINH CITY, VIETNAM",
     "VN", "HO CHI MINH CITY, VIETNAM", "VIETIN COMMERCIAL JOINT STOCK BANK, HO CHI MINH CITY"),
    ("PACIFIC RIM IMPORTS INC.", "1400 HARBOR BLVD, LONG BEACH, CA 90802, USA",
     "US", "LOS ANGELES, USA", "FIRST PACIFIC NATIONAL BANK, LOS ANGELES"),
    ("SHANGHAI HONGDA TRADE CO., LTD", "2200 PUDONG AVENUE, SHANGHAI, CHINA",
     "CN", "SHANGHAI, CHINA", "BANK OF EASTERN CHINA, SHANGHAI"),
    ("BHARAT GLOBAL SOURCING PVT LTD", "17 MARINE DRIVE, MUMBAI 400020, INDIA",
     "IN", "NHAVA SHEVA, INDIA", "INDUS MERCANTILE BANK, MUMBAI"),
    ("GULF STAR GENERAL TRADING LLC", "PO BOX 4471, DEIRA, DUBAI, UAE",
     "AE", "JEBEL ALI, UAE", "EMIRATES COMMERCE BANK, DUBAI"),
]
VESSELS = ["SUNRISE GLORY", "PACIFIC VOYAGER", "HANJIN AURORA", "EVER PROSPER", "OCEAN SENTINEL"]
CARRIERS = ["KOREA MARINE TRANSPORT CO., LTD.", "DONGBANG SHIPPING LINE", "SEOHAE CONTAINER LINES"]

# 하자 배분표: (유형, 건수, 난이도)
DEFECT_PLAN = [
    ("AMOUNT_EXCEEDS_LC", 2, "easy"),
    ("CURRENCY_MISMATCH", 1, "easy"),
    ("GOODS_DESC_MISMATCH", 3, "hard"),
    ("BENEFICIARY_NAME_MISMATCH", 2, "hard"),
    ("LATE_SHIPMENT", 2, "easy"),
    ("LC_EXPIRED_OR_LATE_PRESENTATION", 2, "medium"),
    ("PORT_MISMATCH", 2, "medium"),
    ("BL_SIGNATURE_DEFECT", 2, "medium"),
    ("BL_NO_ONBOARD_NOTATION", 2, "medium"),
    ("COMPOSITE", 2, "hard"),  # 2개 하자 동시
]
# 함정 정상 4종: 하자처럼 보이나 UCP600상 정상
TRAP_KINDS = ["tolerance_ok", "bl_generic_desc", "partial_ok", "presentation_edge"]

UCP = json.loads((ROOT / "pipeline" / "ucp600_kb.json").read_text(encoding="utf-8"))


def ev(doc, field, value):
    return {"doc": doc, "field": field, "value": str(value)}


def set_invoice_amount(inv, target):
    """송장 금액을 바꾸되 **수량 × 단가 = 총액**이 정확히 성립하도록 맞춘다.

    총액에서 단가를 역산해 2자리로 반올림하면 인쇄된 송장의 산술이 어긋난다
    (예: 500 × 19.11 = 9,555.00 인데 총액은 9,556.52 → 차이 1.52).
    실제 상업송장은 내적으로 일관되므로 단가를 먼저 확정하고 총액을 파생시킨다.
    반환: 실제로 적용된 총액"""
    g = inv["goods"][0]
    price = round(target / g["quantity"], 2)
    amount = round(price * g["quantity"], 2)
    g["unit_price"], g["amount"], inv["total_amount"] = price, amount, amount
    return amount


def gt_item(idx, dtype, sev, desc, evidence, kb_key, fix):
    kb = UCP[kb_key]
    return {"id": f"DISC-{idx:03d}", "type": dtype, "severity": sev, "description_ko": desc,
            "evidence": evidence, "ucp_basis": {"article": kb["article"], "quote_ko": kb["quote_ko"]},
            "suggested_fix_ko": fix}


def base_case(rng, seq):
    """정합성이 완전한 서류 3종을 만든다. 하자는 이후 mutate에서 주입."""
    goods, code, unit, price, hs = rng.choice(INDUSTRIES)
    exp_name, exp_addr = rng.choice(EXPORTERS_BY_CODE[code])
    buy_name, buy_addr, cc, discharge, bank = rng.choice(BUYERS)
    model = f"{code}-{rng.randint(100, 999)}"
    qty = qty_for_price(price, rng)
    amount = round(qty * price, 2)
    pi_no = f"PI-{rng.randint(2000, 2699)}"

    issue = date(2026, 5, 1) + timedelta(days=rng.randint(0, 25))
    latest_ship = issue + timedelta(days=rng.randint(45, 70))
    ship = latest_ship - timedelta(days=rng.randint(1, 10))
    expiry = latest_ship + timedelta(days=rng.randint(18, 30))
    inv_date = ship - timedelta(days=rng.randint(0, 3))
    loading = "BUSAN, KOREA" if rng.random() < 0.7 else "INCHEON, KOREA"
    lc_no = f"LC-2026-{rng.randint(10000, 99999)}"
    desc_full = (f"{goods}, MODEL {model}, {qty:,} {unit} AS PER PROFORMA INVOICE NO. {pi_no}, "
                 f"FOB {loading.split(',')[0]} INCOTERMS 2020")

    # 서류 현실성 필드: 포장/중량/용적/부킹번호 (업종별 단위중량 기반 역산)
    # ⚠️ 거래 단위가 이미 CTN(카톤)이면 수량 자체가 포장 개수다.
    #    이를 다시 25로 나누면 B/L에 "20 CARTONS", L/C에 "500 CTN"이 찍혀
    #    사람 눈에는 명백한 수량 불일치인데 정답 라벨에는 없는 '우발 하자'가 된다.
    pkg_count = qty if unit == "CTN" else max(1, qty // 25)
    net_weight_kg = round(qty * WEIGHT_KG_PER_UNIT[code], 1)
    gross_weight_kg = round(net_weight_kg * 1.08, 1)
    measurement_cbm = round(pkg_count * 0.045, 2)
    booking_no = f"BKG{rng.randint(100000, 999999)}"
    shipping_marks = f"{buy_name.split()[0]} / {discharge.split(',')[0]} / C/NO. 1-{pkg_count}"

    lc = {
        "doc_type": "letter_of_credit", "lc_number": lc_no,
        "issuing_bank": bank, "advising_bank": "KB KOOKMIN BANK, SEOUL",
        "issue_date": issue.isoformat(),
        "expiry": {"date": expiry.isoformat(), "place": "SEOUL, KOREA"},
        "applicant": {"name": buy_name, "address": buy_addr, "country": cc},
        "beneficiary": {"name": exp_name, "address": exp_addr, "country": "KR"},
        "currency": "USD", "amount": amount, "tolerance": None,
        "available_with_by": "ANY BANK BY NEGOTIATION",
        "partial_shipments": "NOT ALLOWED", "transhipment": "NOT ALLOWED",
        "port_of_loading": loading, "port_of_discharge": discharge,
        "latest_shipment_date": latest_ship.isoformat(),
        "goods_description": desc_full,
        "documents_required": [
            {"doc_name": "SIGNED COMMERCIAL INVOICE", "copies": "IN 3 ORIGINALS",
             "requirements": "INDICATING THIS L/C NUMBER"},
            {"doc_name": "FULL SET OF CLEAN ON BOARD OCEAN BILLS OF LADING", "copies": "3/3 ORIGINALS",
             "requirements": f"MADE OUT TO ORDER OF {bank.split(',')[0]} MARKED FREIGHT COLLECT AND NOTIFY APPLICANT"},
            {"doc_name": "PACKING LIST", "copies": "IN 3 COPIES", "requirements": None},
        ],
        "additional_conditions": ["ALL DOCUMENTS MUST BE ISSUED IN ENGLISH"],
        "presentation_period_days": 21, "confirmation_instructions": "WITHOUT",
        "field_confidence": {}, "unreadable_fields": [],
    }
    inv = {
        "doc_type": "commercial_invoice", "invoice_number": f"INV-2026-{rng.randint(1000, 9999)}",
        "invoice_date": inv_date.isoformat(), "lc_number_ref": lc_no,
        "seller": {"name": exp_name, "address": exp_addr},
        "buyer": {"name": buy_name, "address": buy_addr},
        "currency": "USD", "total_amount": amount,
        "goods": [{"description": f"{goods}, MODEL {model}, AS PER PROFORMA INVOICE NO. {pi_no}",
                   "quantity": qty, "unit": unit, "unit_price": price, "amount": amount, "hs_code": hs}],
        "incoterms": {"term": "FOB", "place": loading.split(",")[0]},
        "payment_terms": "L/C AT SIGHT",
        "shipping_marks": shipping_marks,
        "country_of_origin": "REPUBLIC OF KOREA",
        "port_of_loading": loading, "port_of_discharge": discharge,
        "packages": f"{pkg_count} CARTONS",
        "net_weight": f"{net_weight_kg:,.1f} KGS", "gross_weight": f"{gross_weight_kg:,.1f} KGS",
        "signed": True, "field_confidence": {}, "unreadable_fields": [],
    }
    bl = {
        "doc_type": "bill_of_lading", "bl_number": f"{rng.choice(['KMT', 'DBS', 'SHC'])}-{rng.randint(1000000, 9999999)}",
        "issue_date": ship.isoformat(),
        "shipped_on_board": {"indicated": True, "date": ship.isoformat(),
                             "method": rng.choice(["on_board_notation", "pre_printed"])},
        "vessel_name": rng.choice(VESSELS), "voyage_number": f"V.{rng.randint(10, 199)}E",
        "place_of_receipt": loading.split(",")[0] + " CY",
        "port_of_loading": loading, "port_of_discharge": discharge,
        "place_of_delivery": discharge.split(",")[0] + " CY",
        "shipper": {"name": exp_name, "address": exp_addr},
        "consignee": {"raw_text": f"TO ORDER OF {bank.split(',')[0]}", "is_to_order": True},
        "notify_party": f"{buy_name}, {buy_addr}",
        "goods_description": f"{pkg_count} CARTONS OF {goods}",
        "container_numbers": [f"{rng.choice(['KMTU', 'DBSU', 'SHCU'])}{rng.randint(1000000, 9999999)}"],
        "shipping_marks": shipping_marks, "package_count": f"{pkg_count} CTNS",
        "gross_weight": f"{gross_weight_kg:,.1f} KGS", "measurement": f"{measurement_cbm:.2f} CBM",
        "booking_number": booking_no,
        "freight_terms": "COLLECT", "clean": True, "originals_count": 3,
        "signature": {"signed": True, "carrier_name": rng.choice(CARRIERS),
                      "signer_capacity": rng.choice(["carrier", "agent_for_carrier", "master"])},
        "field_confidence": {}, "unreadable_fields": [],
    }
    meta = {"goods": goods, "model": model, "qty": qty, "unit": unit, "price": price,
            "loading": loading, "discharge": discharge, "exp_name": exp_name, "pi_no": pi_no,
            # 기본 제시일: 선적 후 5~14일 (제시기한 21일 이내 = 정상)
            "presentation_date": (ship + timedelta(days=rng.randint(5, 14))).isoformat()}
    return {"letter_of_credit": lc, "commercial_invoice": inv, "bill_of_lading": bl}, meta


# ---------- 하자 주입 ----------
def inject(dtype, docs, meta, rng, idx):
    lc, inv, bl = docs["letter_of_credit"], docs["commercial_invoice"], docs["bill_of_lading"]

    if dtype == "AMOUNT_EXCEEDS_LC":
        over = set_invoice_amount(inv, round(lc["amount"] * rng.uniform(1.03, 1.12), 2))
        # 반올림 후에도 초과가 유지되는지 보장 — 단가 반올림이 금액을 한도 아래로 끌어내릴 수 있다.
        while over <= lc["amount"]:
            over = set_invoice_amount(inv, over + inv["goods"][0]["quantity"] * 0.01)
        return gt_item(idx, dtype, "high",
                       f"송장 금액(USD {over:,.2f})이 신용장 금액(USD {lc['amount']:,.2f})을 초과합니다.",
                       [ev("letter_of_credit", "amount", lc["amount"]),
                        ev("commercial_invoice", "total_amount", over)],
                       "UCP600_18b", "송장 금액을 신용장 한도 이내로 수정하세요.")

    if dtype == "CURRENCY_MISMATCH":
        inv["currency"] = "EUR"
        return gt_item(idx, dtype, "high", "송장 통화(EUR)가 신용장 통화(USD)와 다릅니다.",
                       [ev("letter_of_credit", "currency", "USD"),
                        ev("commercial_invoice", "currency", "EUR")],
                       "UCP600_18a3", "송장을 신용장과 동일한 통화(USD)로 재발행하세요.")

    if dtype == "GOODS_DESC_MISMATCH":
        m = meta["model"]
        prefix, num = m.split("-")
        digits = list(num)
        digits[0], digits[1] = digits[1], digits[0]  # 720 → 270 식 자리바꿈 (hard)
        wrong = f"{prefix}-{''.join(digits)}"
        if wrong == m:  # 990처럼 자리바꿈이 무의미한 경우 → 마지막 자리 변조
            last = (int(digits[-1]) + rng.randint(1, 8)) % 10
            wrong = f"{prefix}-{''.join(digits[:-1])}{last}"
        assert wrong != m, f"하자 주입 실패: {m}"
        inv["goods"][0]["description"] = inv["goods"][0]["description"].replace(m, wrong)
        return gt_item(idx, dtype, "high",
                       f"송장 상품명세의 모델번호({wrong})가 신용장 45A({m})와 불일치합니다.",
                       [ev("letter_of_credit", "goods_description", lc["goods_description"]),
                        ev("commercial_invoice", "goods[0].description", inv["goods"][0]["description"])],
                       "UCP600_18c", f"송장 모델번호를 '{m}'으로 수정하세요.")

    if dtype == "BENEFICIARY_NAME_MISMATCH":
        orig = inv["seller"]["name"]
        wrong = orig.replace("CO., LTD.", "COMPANY LTD").replace("CORPORATION", "CORP.")
        if wrong == orig:
            wrong = orig[:-1] + "S"
        inv["seller"]["name"] = wrong
        return gt_item(idx, dtype, "high",
                       f"송장 발행인({wrong})이 신용장 수익자({orig})와 불일치합니다.",
                       [ev("letter_of_credit", "beneficiary.name", orig),
                        ev("commercial_invoice", "seller.name", wrong)],
                       "UCP600_18a1", f"송장 발행인 명의를 '{orig}'로 수정하세요.")

    if dtype == "LATE_SHIPMENT":
        latest = date.fromisoformat(lc["latest_shipment_date"])
        over = latest + timedelta(days=rng.randint(2, 8))
        bl["shipped_on_board"]["date"] = over.isoformat()
        bl["issue_date"] = over.isoformat()
        # 선적일이 뒤로 밀렸으므로 제시일도 함께 밀어 제시일<선적일 모순을 방지한다
        meta["presentation_date"] = (over + timedelta(days=rng.randint(5, 14))).isoformat()
        return gt_item(idx, dtype, "high",
                       f"선적일({over})이 최종선적기일({latest})을 {(over - latest).days}일 초과했습니다.",
                       [ev("letter_of_credit", "latest_shipment_date", latest),
                        ev("bill_of_lading", "shipped_on_board.date", over)],
                       "UCP600_14_44C", "개설의뢰인의 하자 수락(waiver) 또는 조건변경을 요청하세요.")

    if dtype == "LC_EXPIRED_OR_LATE_PRESENTATION":
        # 선적일을 과거로 크게 당겨 제시기한(선적+21일)이 이미 경과하도록
        old_ship = date(2026, 5, 10) + timedelta(days=rng.randint(0, 10))
        # issue_date도 함께 앞당겨 issue ≤ latest_ship ≤ expiry 불변식을 보장한다
        # (기존 버그: issue_date를 갱신하지 않아 최종선적기일이 발행일보다 앞서는 모순 발생 — DEFECT-011)
        lc["issue_date"] = (old_ship - timedelta(days=rng.randint(5, 15))).isoformat()
        lc["latest_shipment_date"] = (old_ship + timedelta(days=5)).isoformat()
        lc["expiry"]["date"] = (old_ship + timedelta(days=25)).isoformat()
        bl["shipped_on_board"]["date"] = old_ship.isoformat()
        bl["issue_date"] = old_ship.isoformat()
        inv["invoice_date"] = old_ship.isoformat()
        deadline = old_ship + timedelta(days=21)
        meta["presentation_date"] = (deadline + timedelta(days=rng.randint(3, 12))).isoformat()  # 기한 경과 후 제시
        return gt_item(idx, dtype, "high",
                       f"서류 제시기한({deadline})이 이미 경과했습니다.",
                       [ev("letter_of_credit", "expiry.date", lc["expiry"]["date"]),
                        ev("bill_of_lading", "shipped_on_board.date", old_ship)],
                       "UCP600_14c", "즉시 은행과 협의하고 개설의뢰인 waiver를 요청하세요.")

    if dtype == "PORT_MISMATCH":
        wrong = "GWANGYANG, KOREA" if "BUSAN" in bl["port_of_loading"] else "BUSAN, KOREA"
        bl["port_of_loading"] = wrong
        return gt_item(idx, dtype, "medium",
                       f"B/L 선적항({wrong})이 신용장 44E({lc['port_of_loading']})와 다릅니다.",
                       [ev("letter_of_credit", "port_of_loading", lc["port_of_loading"]),
                        ev("bill_of_lading", "port_of_loading", wrong)],
                       "UCP600_20a3", "B/L 선적항 표기 정정을 운송사에 요청하세요.")

    if dtype == "BL_SIGNATURE_DEFECT":
        bl["signature"]["signer_capacity"] = "unclear"
        return gt_item(idx, dtype, "medium",
                       "B/L 서명자의 자격(운송인/선장/대리인) 표시가 없습니다.",
                       [ev("bill_of_lading", "signature.signer_capacity", "unclear")],
                       "UCP600_20a1",
                       f"'AS AGENT FOR THE CARRIER, {bl['signature']['carrier_name']}' 자격 문구 명기를 요청하세요.")

    if dtype == "BL_NO_ONBOARD_NOTATION":
        bl["shipped_on_board"] = {"indicated": False, "date": None, "method": None}
        return gt_item(idx, dtype, "high",
                       "B/L에 본선적재 표기(사전인쇄 문언 또는 on board 부기)가 없습니다.",
                       [ev("bill_of_lading", "shipped_on_board.indicated", False)],
                       "UCP600_20a2", "운송사에 선적일이 명기된 ON BOARD 부기를 요청하세요.")
    raise ValueError(dtype)


# ---------- 함정 정상 ----------
def apply_trap(kind, docs, meta, rng):
    lc, inv, bl = docs["letter_of_credit"], docs["commercial_invoice"], docs["bill_of_lading"]
    if kind == "tolerance_ok":
        lc["tolerance"] = {"plus_pct": 10.0, "minus_pct": 10.0}
        new = set_invoice_amount(inv, round(lc["amount"] * 1.07, 2))  # 한도 내 초과
        # 함정의 성립 조건: 반올림 후에도 '초과이면서 허용치 이내'여야 한다.
        assert lc["amount"] < new <= lc["amount"] * 1.10, f"tolerance 함정 무효화: {new}"
        return "송장 금액이 신용장 금액보다 7% 크지만 39A 과부족 허용 10% 이내 → 정상"
    if kind == "bl_generic_desc":
        bl["goods_description"] = "GENERAL MERCHANDISE AS PER INVOICE"
        return "B/L 명세가 일반 표현이나 UCP600 14(e)상 저촉되지 않음 → 정상"
    if kind == "partial_ok":
        lc["partial_shipments"] = "ALLOWED"
        return "분할선적 허용 조건이며 실제 단일 선적 → 정상"
    if kind == "presentation_edge":
        latest = date.fromisoformat(lc["latest_shipment_date"])
        bl["shipped_on_board"]["date"] = latest.isoformat()  # 기일 당일 선적 (경계값)
        bl["issue_date"] = latest.isoformat()
        return "선적일이 최종선적기일과 같은 날(경계값) → 정상"
    raise ValueError(kind)


def grade_of(discs):
    penalty = {"high": 25, "medium": 10, "low": 3}
    score = max(0, 100 - sum(penalty[x["severity"]] for x in discs))
    high = sum(1 for x in discs if x["severity"] == "high")
    if not discs:
        return "A", 100
    if high == 0 and score >= 80:
        return "B", score
    if high <= 1 and score >= 50:
        return "C", score
    return "D", score


def main():
    args = sys.argv[1:]
    out = Path(args[args.index("--out") + 1]) if "--out" in args else Path("cases")
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)

    plan = []
    for dtype, n, diff in DEFECT_PLAN:
        plan += [(dtype, diff)] * n
    cases = []

    # 하자 20건
    for i, (dtype, diff) in enumerate(plan, 1):
        docs, meta = base_case(rng, i)
        gts = []
        if dtype == "COMPOSITE":
            pair = rng.sample(["LATE_SHIPMENT", "GOODS_DESC_MISMATCH", "BL_SIGNATURE_DEFECT",
                               "AMOUNT_EXCEEDS_LC", "PORT_MISMATCH"], 2)
            types = pair
            for k, t in enumerate(pair, 1):
                gts.append(inject(t, docs, meta, rng, k))
        else:
            types = [dtype]
            gts.append(inject(dtype, docs, meta, rng, 1))
        g, sc = grade_of(gts)
        cases.append({
            "case_id": f"DEFECT-{i:03d}", "label": "defect", "defect_types": types, "difficulty": diff,
            "scenario_note_ko": f"{meta['exp_name']} → {docs['letter_of_credit']['applicant']['name']}, "
                                f"{meta['goods']} USD {docs['letter_of_credit']['amount']:,.0f} · 주입 하자: {', '.join(types)}",
            "presentation_date": meta["presentation_date"],
            "documents": docs, "rendered_files": None,
            "ground_truth": {
                "case_id": f"DEFECT-{i:03d}",
                "documents_checked": ["letter_of_credit", "commercial_invoice", "bill_of_lading"],
                "discrepancies": gts,
                "overall_risk": {"grade": g, "score": sc,
                                 "summary_ko": f"{len(gts)}건의 하자가 주입된 케이스입니다."},
            },
        })

    # 정상 20건 (마지막 4건은 함정 정상)
    for i in range(1, 21):
        docs, meta = base_case(rng, 100 + i)
        note = "완전 정합 케이스"
        if i > 16:
            note = apply_trap(TRAP_KINDS[i - 17], docs, meta, rng)
        cases.append({
            "case_id": f"CLEAN-{i:03d}", "label": "clean", "defect_types": [],
            "difficulty": "hard" if i > 16 else "easy",
            "scenario_note_ko": f"{meta['exp_name']} → {docs['letter_of_credit']['applicant']['name']}, "
                                f"{meta['goods']} · {note}",
            "presentation_date": meta["presentation_date"],
            "documents": docs, "rendered_files": None,
            "ground_truth": {
                "case_id": f"CLEAN-{i:03d}",
                "documents_checked": ["letter_of_credit", "commercial_invoice", "bill_of_lading"],
                "discrepancies": [],
                "overall_risk": {"grade": "A", "score": 100, "summary_ko": "하자 없음 — " + note},
            },
        })

    for c in cases:
        (out / f"{c['case_id']}.json").write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[generate] {len(cases)}건 생성 → {out}/  (하자 20 · 정상 20, 함정 정상 4 포함)")

    if "--render" in args:
        files = sorted(str(p) for p in out.glob("*.json"))
        subprocess.run([sys.executable, str(ROOT / "render" / "render.py"), *files,
                        "--out", str(out / "rendered")], check=True)


if __name__ == "__main__":
    main()
