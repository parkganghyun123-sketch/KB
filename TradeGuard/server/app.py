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
import re
import shutil
import sys
import tempfile
import time
import traceback
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

from fastapi import FastAPI, File, Form, HTTPException, UploadFile  # noqa: E402
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
            "instruments": [
                {"product_type": "forward", "product_name_ko": "KB 선물환 (매도)",
                 "fit_reason_ko": "수취 예정일 만기로 전액 확정 — 원화 강세 손실을 원천 차단"},
                {"product_type": "fx_deposit", "product_name_ko": "KB 외화예금",
                 "fit_reason_ko": "즉시 환전 대신 예치 후 분할 환전 — 유연성 우선 시"},
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


def analyze(docs: dict, case_id: str, presentation_date=None, meta=None) -> dict:
    report = build_report(case_id, docs, parse_date(presentation_date))
    delay = delay_block(report)
    return {"case_id": case_id, "documents": docs, "report": report,
            "delay": delay,
            # 하자마다 '무엇을 어떤 값으로 고쳐야 하는가'를 함께 준다.
            # 제안값은 전부 신용장 기재값에서 결정적으로 도출된다(LLM 미사용).
            "remedies": propose_all(docs, report),
            "fx": fx_block(docs, delay["total_business_days"]),
            "presentation_date": presentation_date,
            "engine_version": engine_version(),
            "meta": meta or {}}


# ---------- API ----------
@app.get("/api/health")
def health():
    c = get_client()
    return {"ok": True,
            "llm": {"available": bool(c), "provider": c.name if c else None},
            "modes": {"sample": True, "upload": bool(c)},
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
                        "source": "저장된 케이스 JSON (추출 단계 생략 · LLM 미사용)"})
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
    res = analyze(fixed_docs, case_id, pres,
                  meta={"mode": "redetect", "cost_usd": 0.0,
                        "elapsed_sec": round(time.time() - t0, 3),
                        "source": "결정적 재심사 (LLM 미호출)"})

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
    return res


@app.post("/api/analyze/upload")
async def analyze_upload(files: list[UploadFile] = File(...),
                         presentation_date: str = Form(None)):
    """presentation_date: 서류를 은행에 제시하는(할) 날짜, YYYY-MM-DD.

    이 값이 UCP600 14(c) 제시기한 판정의 기준이 된다. 서류 어디에도 인쇄돼 있지
    않으므로 추출로는 알 수 없고, 사용자가 지정해야 한다. 미지정 시 오늘로 계산하는데,
    과거에 발행된 서류(예: 시연용 벤치마크 이미지)를 판독하면 제시기한 경과가
    일괄로 잡혀 정상 서류까지 하자로 보인다.
    """
    client = get_client()
    if client is None:
        raise HTTPException(503, "LLM 키가 없습니다. .env를 확인하세요 (샘플 모드는 사용 가능).")
    if not files:
        raise HTTPException(400, "파일이 없습니다")
    if presentation_date:
        try:
            date.fromisoformat(presentation_date)
        except ValueError:
            raise HTTPException(400, f"제시일 형식이 올바르지 않습니다 (YYYY-MM-DD): {presentation_date[:20]}")

    from extract import classify, extract as extract_doc
    t0 = time.time()
    tmp = Path(tempfile.mkdtemp(prefix="tg_"))
    docs, errors = {}, []
    try:
        for f in files:
            dest = tmp / (f.filename or "upload.png")
            with dest.open("wb") as w:
                shutil.copyfileobj(f.file, w)
            if dest.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
                errors.append(f"{dest.name}: 지원하지 않는 형식 (PNG/JPG만)")
                continue
            try:
                blocks = [image_block(dest)]
                doc_type = classify(client, blocks)
                if doc_type == "unknown":
                    errors.append(f"{dest.name}: 서류 종류를 판별하지 못했습니다")
                    continue
                data, retries = extract_doc(client, blocks, doc_type)
                docs[doc_type] = data
            except Exception as ex:
                errors.append(f"{dest.name}: {str(ex)[:150]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if not docs:
        raise HTTPException(422, {"message": "추출된 서류가 없습니다", "errors": errors})

    n = len(docs)
    res = analyze(docs, "UPLOAD", presentation_date,
                  meta={"mode": "upload", "provider": client.name, "docs": n,
                        "cost_usd": round(n * 0.02, 3),  # 대략치
                        "elapsed_sec": round(time.time() - t0, 1),
                        "errors": errors,
                        "presentation_date_source": "user" if presentation_date else "today",
                        "source": f"업로드 이미지 {n}장 · {client.name} 실시간 판독"})
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
