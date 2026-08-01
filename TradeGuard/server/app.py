#!/usr/bin/env python3
"""TradeGuard 백엔드 — 업로드 → 추출 → 하자검출 → 환노출을 한 번에

이 서버가 붙으면 화면1(업로드)이 실제로 동작한다. 4화면 전부 실물이 된다.

실행:
  pip install fastapi "uvicorn[standard]" python-multipart
  python3 server/app.py            # http://localhost:8000
  python3 server/app.py --port 8080

두 가지 모드:
  · 데모 모드  — 샘플 케이스 선택. LLM 호출 없음 = **비용 0원**. 발표 실패 보험.
  · 실제 모드  — 서류 이미지 업로드. extract.py가 GPT/Claude로 판독 (서류 3장 ≈ $0.06)

API:
  GET  /api/health              서버·프로바이더 상태
  GET  /api/samples             데모용 샘플 케이스 목록
  POST /api/analyze/sample      {case_id} → 전체 분석 (무료)
  POST /api/analyze/upload      multipart 이미지 → 전체 분석 (유료)
  GET  /api/fx                  현재 환율 (ECOS)
"""
import json
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
from datetime import date, timedelta
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from detect import build_report, d as parse_date  # noqa: E402
from remedy import propose_all, apply_edits  # noqa: E402
from llm import get_client, image_block, load_env  # noqa: E402

load_env()
app = FastAPI(title="TradeGuard API", version="0.1")

DOC_ORDER = [("letter_of_credit", "신용장"), ("commercial_invoice", "상업송장"),
             ("bill_of_lading", "선하증권")]
SAMPLE_DIRS = [ROOT / "samples", ROOT / "benchmark" / "cases"]


# 케이스 ID 허용 문자 — 영문 대문자·숫자·하이픈만.
# 경로 구분자(/ \)와 상위 이동(..)을 문법적으로 배제해 임의 파일 읽기를 막는다.
# 검증 없이 f"{case_id}.json"을 경로에 이어 붙이면 "../../.."로 저장소 밖 JSON을
# 읽을 수 있다(경로 조작). 프로토타입이라도 파일 경로에 사용자 입력을 붙일 때는 막는다.
CASE_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,31}$")


# ---------- 공통 ----------
def find_case(case_id: str) -> dict:
    if not isinstance(case_id, str) or not CASE_ID_RE.match(case_id):
        raise HTTPException(400, f"허용되지 않는 케이스 ID 형식입니다: {str(case_id)[:40]}")
    for d in SAMPLE_DIRS:
        p = (d / f"{case_id}.json").resolve()
        # 정규화 후에도 지정 디렉터리 안에 있는지 이중 확인 (심볼릭 링크 대비)
        if p.parent != d.resolve():
            continue
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    raise HTTPException(404, f"케이스를 찾을 수 없습니다: {case_id}")


# 하자 심각도별 예상 처리 지연(영업일) — 실무 관행 기반 추정치
# high  : 서류 재발행·재제시 또는 개설의뢰인 waiver 협의
# medium: 운송사·발행처 정정 요청
DELAY_DAYS = {"high": 5, "medium": 2, "low": 0}


def delay_block(report: dict) -> dict:
    """하자 → 예상 지연 일수. **경쟁 서비스에 없는 연결 고리.**

    서류 하자는 단순한 '오류'가 아니라 대금 수취를 미루는 원인이다.
    지연된 기간만큼 외화가 환율에 노출되므로, 하자와 환노출은 하나의 인과로 이어진다."""
    items = []
    total = 0
    for d in report.get("discrepancies", []):
        days = DELAY_DAYS.get(d["severity"], 0)
        if days:
            total += days
            items.append({"id": d["id"], "type": d["type"], "severity": d["severity"],
                          "days": days, "reason_ko": d["description_ko"][:60]})
    return {"total_business_days": total, "items": items,
            "basis_ko": "HIGH 하자 1건당 5영업일(재발행·waiver 협의), MEDIUM 1건당 2영업일(정정 요청) 가정"}


def fx_block(docs: dict, delay_days: int = 0) -> dict:
    """추출된 서류에서 현금흐름을 만들고 환노출을 계산한다 (fx_exposure 스키마 축약형)

    delay_days: 하자로 인한 예상 지연. 수취 예정일을 그만큼 밀어 노출 기간을 늘린다."""
    inv = docs.get("commercial_invoice") or {}
    bl = docs.get("bill_of_lading") or {}
    amount = inv.get("total_amount")
    currency = inv.get("currency", "USD")
    ship = (bl.get("shipped_on_board") or {}).get("date") or bl.get("issue_date")

    rate, rate_src, rate_date = 1385.0, "fallback", None
    try:
        from fx_rates import ecos_spot
        t, v = ecos_spot()
        rate, rate_src, rate_date = v, "ecos", f"{t[:4]}-{t[4:6]}-{t[6:]}"
    except Exception:
        pass

    # 연율 변동성 — 가정치가 아니라 ECOS 시계열 실측값을 우선 사용한다.
    sigma_annual, vol_src = 0.09, "fallback"
    vol_meta = {"method_ko": "실측 실패 — 시연용 가정치"}
    try:
        from fx_rates import ecos_volatility
        vm = ecos_volatility()
        sigma_annual, vol_src = vm["sigma_annual"], vm["source"]
        vol_meta = {k: vm[k] for k in ("n_observations", "start", "end", "method_ko") if k in vm}
    except Exception:
        pass

    # √t 규칙 — 지연 기간이 길수록 노출 변동폭이 커진다.
    # 이 연결이 없으면 "하자 → 지연 → 환노출" 인과가 화면에서 성립하지 않는다.
    from fx_rates import period_sigma
    sigma_period = period_sigma(sigma_annual, delay_days)
    Z_95 = 1.645  # 정규분포 단측 95%
    band_pct = round(sigma_period * Z_95 * 100, 3)

    scenarios = []
    if amount:
        for name, mult in (("krw_strong", -Z_95), ("base", 0.0), ("krw_weak", Z_95)):
            pct = sigma_period * mult * 100
            r = rate * (1 + pct / 100)
            scenarios.append({"name": name, "rate_change_pct": round(pct, 3),
                              "assumed_rate": round(r, 2),
                              "pnl_krw": round(amount * (r - rate))})
    # 수취 예정일 = 선적일 + 결제기간(가정 25일) + 하자로 인한 지연
    expected, delayed = None, None
    if ship:
        try:
            base = date.fromisoformat(ship) + timedelta(days=25)
            expected = base.isoformat()
            delayed = (base + timedelta(days=round(delay_days * 7 / 5))).isoformat()  # 영업일→달력일
        except Exception:
            pass

    return {
        "spot_rate": {"KRW/USD": round(rate, 2)}, "rate_source": rate_src, "rate_date": rate_date,
        "rate_label_ko": "한국은행 매매기준율 (통계코드 731Y001)",
        "expected_date": expected, "delayed_date": delayed, "delay_days": delay_days,
        "volatility": {"sigma_annual": round(sigma_annual, 6), "source": vol_src,
                       "sigma_period": round(sigma_period, 6),
                       "confidence_z": Z_95, "band_pct": band_pct,
                       "basis_ko": "σ(기간) = σ(연율) × √(지연영업일/252) · 정규분포 95% 구간",
                       **vol_meta},
        "exposure_krw": (round(amount * rate * sigma_period * Z_95) if amount else 0),
        "cash_flows": ([{"expected_date": delayed or expected or ship, "direction": "inflow",
                         "currency": currency, "amount": amount, "certainty": "estimated",
                         "source": {"doc": "commercial_invoice", "field": "total_amount"}}]
                       if amount else []),
        "scenarios": scenarios,
        "hedge_recommendation": {
            "needed": bool(amount),
            "notional": amount, "currency": currency,
            # KB국민은행이 공개한 분류명을 쓴다. 'KB 선물환'은 개별 약관상 상품명이 아니라
            # 기업뱅킹 FX/파생상품의 '선(현)물환' 카테고리이며, 그 안에 기본 선물환·
            # 통화옵션·합성선물환이 있다. 없는 이름을 지어내면 실무자에게 바로 걸린다.
            "instruments": [
                {"product_type": "forward", "product_name_ko": "선(현)물환 — 매도",
                 "fit_reason_ko": "수취 예정일 만기로 환율을 확정해 원화 강세 손실을 차단합니다. "
                                  "통화옵션·합성선물환도 선택할 수 있습니다."},
                {"product_type": "fx_deposit", "product_name_ko": "외화예금",
                 "fit_reason_ko": "즉시 환전하지 않고 예치한 뒤 나눠 환전합니다 — 유연성 우선 시."},
            ],
            "rationale_ko": ((f"하자로 {delay_days}영업일이 지연되는 동안 연율 변동성 "
                              f"{sigma_annual:.1%} 기준 원화 수령액이 최대 "
                              f"{abs(round(amount * rate * sigma_period * Z_95)):,}원 달라질 수 있습니다."
                              if delay_days > 0 else
                              "지연 요인이 없어 이 구간의 환율 변동에 노출되지 않습니다.")
                             if amount else "금액을 판독하지 못해 계산할 수 없습니다."),
        },
    }


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="seconds")


def engine_version() -> str:
    """판정 엔진 버전 = 현재 커밋 해시. 감사 추적에서 '무엇이 판정했는가'를 특정한다."""
    try:
        import subprocess
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT),
                              capture_output=True, text=True, timeout=3).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


# ---------- KB 창구 연결 ----------
# 서류 하자를 찾아주는 데서 끝나면 반쪽이다. 신용장 서류를 은행에 내는 행위 자체가
# **수출환어음 매입(네고)** 이고, 하자 유무가 곧 어느 창구로 가는지를 가른다.
#   하자 없음 → 정상 매입 (즉시 자금화)
#   치유 가능 → 수정 후 매입
#   치유 불가 → 하자 네고(개설은행 조회·지급확약 후 매입) 또는 추심 전환
#
# ⚠️ 여기에는 **은행의 고유 업무명만** 쓴다. 브랜드 서비스명·한시 프로그램명은 쓰지 않는다.
#
#    이유: 브랜드 플랫폼과 우대 프로그램은 종료·개편된다(예: 한때 안내하던 무역 플랫폼이
#    현재는 제공되지 않는다). 반면 수출환어음 매입·무역금융·선물환·외화예금은 외국환은행의
#    고유 업무라 이름이 바뀌지 않는다. 화면에 없는 서비스를 띄우면 실무자 심사위원에게
#    바로 걸리고, 신뢰가 한 번에 무너진다.
#
#    같은 이유로 **한도·금리·대상 요건 수치는 적지 않는다.** (포괄금융 대상 수출실적 기준만
#    해도 자료마다 다르게 나온다.) 조건은 영업점 상담으로 확정된다고만 안내한다.
#    환변동보험은 한국무역보험공사(K-SURE) 상품이라 여기 넣지 않는다.
DOC_KO = {"letter_of_credit": "신용장", "commercial_invoice": "상업송장",
          "bill_of_lading": "선하증권"}

CHANNEL_BRANCH = "영업점 외환 담당"
CHANNEL_ONLINE = "기업인터넷뱅킹 · 영업점"


def kb_products(report: dict, remedies: list, delay: dict, docs: dict) -> dict:
    """서류 상태 → 지금 이용할 수 있는 KB 창구·상품."""
    discs = report.get("discrepancies", [])
    incurable = [r for r in remedies if not r.get("curable")]
    delay_days = delay.get("total_business_days", 0)
    inv = docs.get("commercial_invoice") or {}
    amount, currency = inv.get("total_amount"), inv.get("currency", "USD")

    items = []
    if not discs and delay.get("incurred"):
        # 고쳐서 하자는 없앴지만 그 과정에서 시간을 썼다. 둘 다 말해 준다.
        route = {"status": "clean", "product_ko": "수출환어음 매입",
                 "headline_ko": "제출 가능한 상태입니다",
                 "detail_ko": f"수정으로 하자가 모두 해소돼 정상 매입 신청이 가능합니다. "
                              f"다만 재발행에 약 {delay_days}영업일이 소요돼, "
                              f"그 기간의 자금 공백과 환노출은 그대로 남아 있습니다."}
        items.append({"category_ko": "결제·자금화", "product_ko": "수출환어음 매입",
                      "channel_ko": CHANNEL_ONLINE,
                      "fit_reason_ko": "하자가 해소돼 매입 신청이 가능합니다."})
    elif not discs:
        route = {"status": "clean", "product_ko": "수출환어음 매입",
                 "headline_ko": "정상 매입(네고) 진행이 가능한 상태입니다",
                 "detail_ko": "신용장 조건과 서류가 일치하므로 개설은행의 하자 통보 없이 "
                              "매입이 진행됩니다. 선적 후 곧바로 대금을 자금화할 수 있습니다."}
        items.append({"category_ko": "결제·자금화", "product_ko": "수출환어음 매입",
                      "channel_ko": CHANNEL_ONLINE,
                      "fit_reason_ko": "하자 0건 — 서류를 그대로 제시해 매입 신청하면 됩니다. "
                                       "개설은행의 하자 통보 없이 선적 대금을 앞당겨 받을 수 있습니다."})
    elif incurable:
        blocked = ", ".join(sorted({r["type"] for r in incurable}))
        route = {"status": "blocked", "product_ko": "하자 네고 / 추심 전환",
                 "headline_ko": "서류 수정으로 치유되지 않는 하자가 있습니다",
                 "detail_ko": f"{blocked} 은(는) 이미 발생한 사실이라 서류를 다시 써도 해소되지 "
                              "않습니다. 개설의뢰인의 하자 수락(waiver) 또는 신용장 조건변경을 "
                              "먼저 협의하고, 그 결과에 따라 매입 방식을 정하게 됩니다."}
        items.append({"category_ko": "결제·자금화", "product_ko": "하자 네고 / 추심 전환",
                      "channel_ko": CHANNEL_BRANCH,
                      "fit_reason_ko": "개설은행에 하자를 통보·조회해 지급확약을 받은 뒤 매입하거나, "
                                       "매입 없이 서류를 보내 추심 후 대금을 받는 방식을 검토합니다."})
    else:
        route = {"status": "curable", "product_ko": "수출환어음 매입",
                 "headline_ko": "수정하면 정상 매입이 가능한 상태입니다",
                 "detail_ko": "발견된 하자는 모두 서류 재발행·정정으로 해소됩니다. "
                              "수정 후 제시하면 정상 매입 절차를 그대로 밟을 수 있습니다."}
        items.append({"category_ko": "결제·자금화", "product_ko": "수출환어음 매입",
                      "channel_ko": CHANNEL_ONLINE,
                      "fit_reason_ko": "위 수정 제안을 반영해 재제시하면 하자 없이 매입 신청이 가능합니다."})

    # 대금 수취가 밀리는 만큼 운전자금 공백이 생긴다.
    # 수정을 마친 뒤에도 이 카드는 남는다 — 재발행에 쓴 시간은 되돌아오지 않는다.
    if delay_days:
        incurred = delay.get("incurred")
        when = (f"하자를 고치는 데 약 {delay_days}영업일이 이미 소요됐습니다. "
                if incurred else
                f"하자 처리로 약 {delay_days}영업일 수취가 지연됩니다. ")
        items.append({"category_ko": "자금 공백", "product_ko": "무역금융",
                      "channel_ko": CHANNEL_BRANCH,
                      "fit_reason_ko": when + "신용장기준·실적기준·포괄금융으로 그 기간의 "
                                              "운전자금을 메울 수 있는지 상담해 보십시오."})
        # 지연이 있어야 노출 구간이 생긴다. 지연 0이면 환 상품을 권하지 않는다.
        items.append({"category_ko": "환위험", "product_ko": "선(현)물환",
                      "channel_ko": CHANNEL_ONLINE,
                      "fit_reason_ko": ("밀린 수취 예정일을 만기로 환율을 확정해 남은 구간의 변동을 차단합니다. "
                                        if incurred else
                                        "수취 예정일 만기로 환율을 확정해 지연 구간의 변동을 차단합니다. ")
                                       + "기본 선물환 외에 통화옵션·합성선물환도 선택할 수 있습니다."})
        items.append({"category_ko": "환위험", "product_ko": "외화예금",
                      "channel_ko": CHANNEL_ONLINE,
                      "fit_reason_ko": "수취 즉시 환전하지 않고 예치한 뒤 나눠 환전합니다. "
                                       "환율 확정보다 유연성이 필요할 때 씁니다(외화보통예금·외화정기예금)."})

    return {
        "route": route, "items": items,
        "exposure": {"amount": amount, "currency": currency, "delay_business_days": delay_days},
        "note_ko": "외국환은행의 고유 업무 기준으로 안내합니다. 실제 이용 가능 여부와 "
                   "한도·금리·수수료는 영업점 상담으로 확정되며, 본 화면은 은행의 심사 결과가 아닙니다.",
    }


def analyze(docs: dict, case_id: str, presentation_date=None, meta=None,
            incurred_days: int = 0) -> dict:
    """incurred_days: 재심사 전 하자 처리에 이미 소요된 영업일.

    수정하면 하자가 사라지므로 새로 계산한 지연은 0이 된다. 그런데 서류를
    재발행하는 데 걸린 시간은 되돌아오지 않는다. 그 기간의 자금 공백과 환노출은
    수정 여부와 무관하게 실제로 발생한다 — 오히려 이제는 추정이 아니라 확정이다.
    그래서 재심사에서는 앞서 계산한 지연을 이월해 창구 안내에 반영한다.
    """
    report = build_report(case_id, docs, parse_date(presentation_date))
    delay = delay_block(report)
    if incurred_days > delay["total_business_days"]:
        delay = dict(delay, total_business_days=incurred_days, incurred=True,
                     basis_ko="수정 전 하자 처리에 소요된 기간입니다. "
                              "서류를 고쳐도 이미 지난 시간은 돌아오지 않습니다.")
    # 하자마다 '무엇을 어떤 값으로 고쳐야 하는가'를 함께 준다.
    # 제안값은 전부 신용장 기재값에서 결정적으로 도출된다(LLM 미사용).
    remedies = propose_all(docs, report)
    return {"case_id": case_id, "documents": docs, "report": report,
            "delay": delay,
            "remedies": remedies,
            "fx": fx_block(docs, delay["total_business_days"]),
            # 진단에서 끝내지 않고 '그래서 어느 창구로 가야 하는가'까지 연결한다.
            "kb": kb_products(report, remedies, delay, docs),
            "presentation_date": presentation_date,
            "engine_version": engine_version(),
            "meta": meta or {}}


# ---------- 공개 배포 보호 ----------
#
# 공개 URL에 키를 넣어 두면 판독 1회가 곧 비용이다. 누구든 호출할 수 있으므로
# 아무 장치가 없으면 하루 만에 크레딧이 빈다. 세 겹으로 막는다.
#
#   1) 접근 코드 — TG_UPLOAD_CODE가 설정돼 있으면 업로드 계열에 코드를 요구한다.
#                  로컬 실행에서는 값이 없으므로 아무 제약이 없다.
#   2) IP별 상한 — 한 사람이 반복 호출하는 것을 막는다.
#   3) 전체 상한 — 코드가 유출돼도 하루 총량이 넘지 않는다.
#
# 상한에 걸려도 **샘플 모드는 그대로 동작한다.** 판정·수정 제안·재심사는
# LLM을 쓰지 않으므로 막을 이유가 없고, 그래야 심사가 끊기지 않는다.
UPLOAD_CODE = os.environ.get("TG_UPLOAD_CODE", "").strip()
LIMIT_PER_IP = int(os.environ.get("TG_LIMIT_PER_IP", "5"))
LIMIT_TOTAL = int(os.environ.get("TG_LIMIT_TOTAL", "60"))

_usage = {"day": None, "total": 0, "by_ip": {}}


def _today() -> str:
    return date.today().isoformat()


def _usage_reset_if_needed():
    if _usage["day"] != _today():
        _usage.update(day=_today(), total=0, by_ip={})


def check_upload_quota(request, code: str | None, n_docs: int = 1):
    """업로드 계열 호출 전에 코드와 사용량을 확인한다. 초과하면 HTTPException."""
    _usage_reset_if_needed()
    if UPLOAD_CODE:
        if not code or code.strip() != UPLOAD_CODE:
            raise HTTPException(403, "접근 코드가 필요합니다. 샘플 모드는 코드 없이 이용하실 수 있습니다.")
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "unknown"))
    if _usage["total"] + n_docs > LIMIT_TOTAL:
        raise HTTPException(429, "오늘 판독 한도를 모두 사용했습니다. "
                                 "샘플 모드는 계속 이용하실 수 있으며, 코드를 내려받아 "
                                 "로컬에서 직접 실행하시면 제한 없이 확인하실 수 있습니다.")
    if _usage["by_ip"].get(ip, 0) + n_docs > LIMIT_PER_IP:
        raise HTTPException(429, f"판독은 하루 {LIMIT_PER_IP}장까지 이용하실 수 있습니다. "
                                 "샘플 모드는 계속 이용하실 수 있습니다.")
    return ip


def record_upload_usage(ip: str, n: int):
    _usage_reset_if_needed()
    _usage["total"] += n
    _usage["by_ip"][ip] = _usage["by_ip"].get(ip, 0) + n


# ---------- API ----------
@app.get("/api/health")
def health():
    c = get_client()
    _usage_reset_if_needed()
    return {"ok": True,
            "llm": {"available": bool(c), "provider": c.name if c else None},
            "modes": {"sample": True, "upload": bool(c)},
            "upload_gate": {"code_required": bool(UPLOAD_CODE),
                            "per_ip": LIMIT_PER_IP,
                            "remaining_today": max(0, LIMIT_TOTAL - _usage["total"])},
            "note": "샘플 분석은 LLM 없이 동작합니다 (비용 0원)."}


@app.get("/api/samples")
def samples():
    out = []
    # find_case()가 SAMPLE_DIRS를 앞에서부터 훑어 첫 일치를 반환하므로,
    # 같은 case_id가 두 디렉터리에 있으면 목록에는 두 장이 뜨는데 클릭 결과는 하나뿐이다.
    # 카드에 적힌 등급과 실제 판정이 어긋나므로, 목록도 같은 우선순위로 중복을 제거한다.
    seen = set()
    for d in SAMPLE_DIRS:
        for p in sorted(d.glob("*.json")):
            try:
                c = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if "documents" not in c or c.get("case_id") in seen:
                continue
            seen.add(c["case_id"])
            out.append({"case_id": c["case_id"], "label": c.get("label"),
                        "defect_types": c.get("defect_types", []),
                        "note": (c.get("scenario_note_ko") or "")[:90],
                        "expected_grade": (c.get("ground_truth") or {}).get("overall_risk", {}).get("grade")})
    # 데모 추천 순서: A등급 함정 → C등급 → D등급
    prio = {"CLEAN-017": 0, "DEFECT-019": 1, "DEMO-001": 2}
    out.sort(key=lambda x: (prio.get(x["case_id"], 9), x["case_id"]))
    return {"count": len(out), "samples": out}


@app.post("/api/analyze/sample")
def analyze_sample(body: dict):
    case_id = (body or {}).get("case_id")
    if not case_id:
        raise HTTPException(400, "case_id가 필요합니다")
    t0 = time.time()
    case = find_case(case_id)
    res = analyze(case["documents"], case_id, case.get("presentation_date"),
                  meta={"mode": "sample", "cost_usd": 0.0,
                        "elapsed_sec": round(time.time() - t0, 2),
                        "source": "샘플 서류 — 판독을 마친 데이터로 심사만 수행"})
    res["ground_truth"] = case.get("ground_truth")
    return res


@app.post("/api/redetect")
def redetect(body: dict):
    """수정 반영 후 재심사 — 폐쇄 루프의 핵심.

    입력: {documents, edits[], presentation_date?, case_id?, before?}
      edits = /api/analyze가 준 remedies 중 사용자가 **승인한** 항목.
              after 값을 사용자가 바꿨다면 그 값이 그대로 반영된다(최종 결정권은 사람).
    출력: 재심사 리포트 + Before/After 비교 + 감사 이력

    LLM을 호출하지 않는다. 판정도 제안도 결정적이므로 비용 0원·재현 가능하며,
    같은 입력에 항상 같은 결과가 나온다.
    """
    body = body or {}
    docs = body.get("documents")
    if not docs:
        raise HTTPException(400, "documents가 필요합니다")

    case_id = body.get("case_id") or "REDETECT"
    pres = body.get("presentation_date")
    t0 = time.time()

    before = body.get("before") or build_report(case_id, docs, parse_date(pres))
    fixed_docs, applied = apply_edits(docs, body.get("edits") or [])
    # 수정 전 하자로 발생한 지연을 이월한다 — 고쳤다고 쓴 시간이 돌아오지 않는다.
    incurred = delay_block(before)["total_business_days"]
    res = analyze(fixed_docs, case_id, pres, incurred_days=incurred,
                  meta={"mode": "redetect", "cost_usd": 0.0,
                        "elapsed_sec": round(time.time() - t0, 3),
                        "source": "직전 심사 서류에 승인한 수정을 반영해 재심사"})

    before_types = {x["type"] for x in before.get("discrepancies", [])}
    after_types = {x["type"] for x in res["report"]["discrepancies"]}
    remaining = res["report"]["discrepancies"]
    incurable = [r for r in res["remedies"] if not r["curable"]]

    res["comparison"] = {
        "before": {"grade": before["overall_risk"]["grade"],
                   "score": before["overall_risk"]["score"],
                   "count": len(before.get("discrepancies", []))},
        "after": {"grade": res["report"]["overall_risk"]["grade"],
                  "score": res["report"]["overall_risk"]["score"],
                  "count": len(remaining)},
        "resolved": sorted(before_types - after_types),
        "new_defects": sorted(after_types - before_types),
        # 제출 가능 = 잔여 하자 0건. 치유 불가 하자가 남아 있으면 여기서 막힌다.
        "submittable": len(remaining) == 0,
        "blocked_by_ko": ([r["type"] for r in incurable] if incurable else []),
    }
    # 감사 추적 — 누가 무엇을 어떤 엔진으로 바꿨는지 리포트에 남긴다.
    res["audit"] = {
        "timestamp": _now_iso(),
        "engine_version": res["engine_version"],
        "edits": [{"doc": a["doc"], "field": a["field"],
                   "before": a["before"], "after": a["after"],
                   "type": a["type"], "basis_ko": a.get("basis_ko")} for a in applied],
        "note_ko": "판정·제안 모두 결정적 규칙 기반이며 LLM을 호출하지 않았습니다.",
    }
    res["audit"]["persisted"] = append_audit(case_id, res)
    return res


AUDIT_LOG = ROOT / "audit" / "redetect.jsonl"


def append_audit(case_id: str, res: dict) -> bool:
    """재심사 감사 이력을 파일에 덧붙인다 (JSON Lines, 추가 전용).

    은행 업무에서는 "왜 이 값으로 바뀌었는가"를 나중에 되짚을 수 있어야 한다.
    화면에만 남기면 새로고침과 함께 사라진다.

    **서류 내용은 저장하지 않는다.** 무엇을 어떤 값으로 바꿨는지, 어느 엔진이
    판정했는지, 등급이 어떻게 변했는지만 남긴다. 서류 원본이나 이미지는 기록 대상이
    아니다 — 사후 검증에 필요한 최소한만 남기는 것이 개인정보 관점에서도 맞다.

    쓰기에 실패해도 심사 결과는 그대로 돌려준다. 이력은 부가 기능이지 심사의
    전제가 아니다. 성공 여부만 응답에 실어 화면이 사실대로 표시하게 한다.
    """
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": res["audit"]["timestamp"], "case_id": case_id,
               "engine_version": res["engine_version"],
               "grade_before": res["comparison"]["before"]["grade"],
               "grade_after": res["comparison"]["after"]["grade"],
               "resolved": res["comparison"]["resolved"],
               "new_defects": res["comparison"]["new_defects"],
               "submittable": res["comparison"]["submittable"],
               "edits": res["audit"]["edits"],
               "llm_calls": 0}
        with AUDIT_LOG.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


MAX_FILES = 10                    # 신용장·송장·선하증권 3종 + 여유분
MAX_BYTES = 12 * 1024 * 1024      # 서류 스캔본 1장 기준 넉넉한 상한
ALLOWED_EXT = (".png", ".jpg", ".jpeg", ".webp")


def receive_uploads(files, tmp: Path):
    """업로드 파일을 임시 디렉터리에 안전하게 저장한다.

    파일명은 **클라이언트가 정하는 값이므로 신뢰하지 않는다.**
    `tmp / f.filename` 처럼 그대로 결합하면 "../../etc/x.png"이나 "/tmp/x.png"이
    임시 디렉터리를 벗어난다. 케이스 ID는 화이트리스트로 막아 두었는데(CASE_ID_RE)
    업로드 경로만 열려 있으면 같은 종류의 구멍이 남는다.

    확장자만 원본에서 취하고 **경로는 서버가 짓는다.** 원래 이름은 화면 표시용으로만
    돌려준다. 크기·개수 상한도 여기서 함께 건다 — 상한이 없으면 이미지 전량이
    메모리에 base64로 올라가고 LLM 비용도 그만큼 나간다.

    반환: [(표시용 원본 파일명, 저장 경로 or None, 오류 메시지 or None)]
    """
    out = []
    for i, f in enumerate(files[:MAX_FILES]):
        shown = PurePosixPath(f.filename or "upload.png").name or "upload.png"
        ext = Path(shown).suffix.lower()
        if ext not in ALLOWED_EXT:
            out.append((shown, None, f"{shown}: 지원하지 않는 형식 (PNG/JPG만)"))
            continue
        dest = tmp / f"{i:02d}{ext}"          # 경로는 서버가 정한다
        size = 0
        with dest.open("wb") as w:
            while chunk := f.file.read(1 << 20):
                size += len(chunk)
                if size > MAX_BYTES:
                    w.close(); dest.unlink(missing_ok=True)
                    out.append((shown, None,
                                f"{shown}: 파일이 너무 큽니다 ({MAX_BYTES // (1024*1024)}MB 이하)"))
                    break
                w.write(chunk)
            else:
                out.append((shown, dest, None))
    if len(files) > MAX_FILES:
        out.append((None, None, f"한 번에 {MAX_FILES}장까지 처리합니다. "
                                f"나머지 {len(files) - MAX_FILES}장은 제외했습니다"))
    return out


# 서류 종류만 알려주는 가벼운 단계. 필드 추출은 하지 않는다.
#
# 왜 따로 두나 — 사용자는 파일명만 보고 무엇을 올렸는지 확신하지 못한다.
# 잘못 올린 걸 추출까지 끝난 뒤에 알면 시간과 비용을 이미 쓴 뒤다.
# 분류는 저비용 모델(classify_model)을 쓰므로 추출 대비 값이 훨씬 싸다.
# 실패해도 분석 자체는 그대로 진행된다 — 이 단계는 편의 기능이다.
@app.post("/api/classify")
async def classify_upload(request: Request,
                          files: list[UploadFile] = File(...),
                          access_code: str = Form(None)):
    # 입력 검증(400)을 서비스 가용성 검사(503)보다 먼저 한다.
    # 순서가 반대면 날짜를 잘못 넣은 사용자가 "LLM 키가 없습니다"를 보게 된다.
    if not files:
        raise HTTPException(400, "파일이 없습니다")
    client = get_client()
    if client is None:
        raise HTTPException(503, "LLM 키가 없습니다")
    ip = check_upload_quota(request, access_code, len(files))

    from extract import classify
    tmp = Path(tempfile.mkdtemp(prefix="tgc_"))
    out = []
    try:
        for shown, dest, err in receive_uploads(files, tmp):
            if shown is None:               # 개수 초과 안내
                continue
            if err:
                out.append({"filename": shown, "doc_type": None,
                            "doc_type_ko": "확인 불가"})
                continue
            try:
                dt = classify(client, [image_block(dest)]); record_upload_usage(ip, 1)
            except Exception:
                dt = "unknown"          # 조용히 넘어간다. 분석은 이것과 무관하게 돌아간다.
            out.append({"filename": shown,
                        "doc_type": dt if dt != "unknown" else None,
                        "doc_type_ko": DOC_KO.get(dt, "판별 못 함")})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"results": out}


@app.post("/api/analyze/upload")
async def analyze_upload(request: Request,
                         files: list[UploadFile] = File(...),
                         presentation_date: str = Form(None),
                         access_code: str = Form(None)):
    """presentation_date: 서류를 은행에 제시하는(할) 날짜, YYYY-MM-DD.

    이 값이 UCP600 14(c) 제시기한 판정의 기준이 된다. 서류 어디에도 인쇄돼 있지
    않으므로 추출로는 알 수 없고, 사용자가 지정해야 한다. 미지정 시 오늘로 계산하는데,
    과거에 발행된 서류(예: 시연용 벤치마크 이미지)를 판독하면 제시기한 경과가
    일괄로 잡혀 정상 서류까지 하자로 보인다.
    """
    # 입력 검증(400)이 먼저다. 키가 없다고 503을 던지면
    # 날짜를 잘못 넣은 사용자가 엉뚱한 메시지를 받는다.
    if not files:
        raise HTTPException(400, "파일이 없습니다")
    if presentation_date:
        try:
            date.fromisoformat(presentation_date)
        except ValueError:
            raise HTTPException(400, f"제시일 형식이 올바르지 않습니다 (YYYY-MM-DD): {presentation_date[:20]}")
    client = get_client()
    if client is None:
        raise HTTPException(503, "LLM 키가 없습니다. .env를 확인하세요 (샘플 모드는 사용 가능).")
    ip = check_upload_quota(request, access_code, len(files))

    from extract import classify, extract as extract_doc
    t0 = time.time()
    tmp = Path(tempfile.mkdtemp(prefix="tg_"))
    docs, errors, classified = {}, [], []
    llm_calls = 0
    try:
        for shown, dest, err in receive_uploads(files, tmp):
            if err:
                errors.append(err)
                continue
            try:
                blocks = [image_block(dest)]
                doc_type = classify(client, blocks)
                llm_calls += 1; record_upload_usage(ip, 1)
                if doc_type == "unknown":
                    errors.append(f"{shown}: 서류 종류를 판별하지 못했습니다")
                    continue
                # 같은 종류가 두 장 오면 조용히 덮어쓰지 않는다.
                # 사용자는 두 장을 올렸는데 한 장만 심사되면 판정 결과를 신뢰할 수 없다.
                if doc_type in docs:
                    errors.append(f"{shown}: {DOC_KO.get(doc_type, doc_type)}이(가) 이미 있어 "
                                  f"이 파일은 심사에서 제외했습니다")
                    continue
                data, retries = extract_doc(client, blocks, doc_type)
                llm_calls += 1; record_upload_usage(ip, 1)
                docs[doc_type] = data
                classified.append({"filename": shown, "doc_type": doc_type,
                                   "doc_type_ko": DOC_KO.get(doc_type, doc_type)})
            except Exception as ex:
                errors.append(f"{shown}: {str(ex)[:150]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if not docs:
        raise HTTPException(422, {"message": "추출된 서류가 없습니다", "errors": errors})

    n = len(docs)
    res = analyze(docs, "UPLOAD", presentation_date,
                  meta={"mode": "upload", "provider": client.name, "docs": n,
                        # 서류 1장당 종류 판별 1회 + 필드 추출 1회. 실제 호출 수를 센다.
                        # 판정·수정 제안·재심사는 이 뒤로 전부 코드이므로 0회다.
                        "llm_calls": llm_calls,
                        "cost_usd": round(llm_calls * 0.01, 3),  # 대략치
                        "elapsed_sec": round(time.time() - t0, 1),
                        "errors": errors, "classified": classified,
                        "presentation_date_source": "user" if presentation_date else "today",
                        "source": f"업로드하신 서류 이미지 {n}장을 판독"})
    return res


@app.get("/api/fx")
def fx():
    try:
        from fx_rates import ecos_spot
        t, v = ecos_spot()
        return {"source": "ecos", "date": f"{t[:4]}-{t[4:6]}-{t[6:]}", "rate": v}
    except Exception as ex:
        return {"source": "fallback", "rate": 1385.0, "error": str(ex)[:120]}


@app.exception_handler(Exception)
async def on_error(request, exc):
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"message": str(exc)[:300]})


# ---------- 정적 파일 ----------
@app.get("/")
def index():
    return FileResponse(ROOT / "server" / "app.html")


# html=True → 디렉터리 접근 시 index.html을 자동으로 서빙 (없으면 404가 난다)
app.mount("/mockups", StaticFiles(directory=str(ROOT / "mockups"), html=True), name="mockups")
app.mount("/render", StaticFiles(directory=str(ROOT / "render"), html=True), name="render")

# 시연용 서류 이미지. 공개 URL로 접속한 사람에게는 **올릴 파일 자체가 없다.**
# 저장소를 받지 않고도 업로드 판독을 확인할 수 있도록 내려받을 경로를 연다.
_DEMO_IMG = ROOT / "benchmark" / "cases" / "rendered"
if _DEMO_IMG.is_dir():
    app.mount("/demo-docs", StaticFiles(directory=str(_DEMO_IMG)), name="demo-docs")


@app.get("/api/demo-docs")
def demo_docs():
    """시연에 쓸 수 있는 서류 이미지 목록 (저장소에 포함된 것만)."""
    out = []
    for case, pres, note in (("CLEAN-017", "2026-06-30", "정상 — 금액이 한도를 넘지만 39A 과부족 허용으로 수리 가능"),
                             ("DEFECT-019", "2026-07-21", "하자 2건 — 금액 초과 · B/L 서명자 자격 미표시")):
        files = [f"{case}_{k}.png" for k in ("lc", "invoice", "bl")]
        if all((_DEMO_IMG / f).exists() for f in files):
            out.append({"case_id": case, "presentation_date": pres, "note_ko": note,
                        "files": [f"/demo-docs/{f}" for f in files]})
    return {"cases": out}


def main():
    import uvicorn
    args = sys.argv[1:]
    port = int(args[args.index("--port") + 1]) if "--port" in args else 8000
    c = get_client()
    print(f"[server] LLM: {c.name if c else '없음 (샘플 모드만 가능)'}")
    print(f"[server] → http://localhost:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
