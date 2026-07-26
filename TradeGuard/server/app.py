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
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

from fastapi import FastAPI, File, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from detect import build_report, d as parse_date  # noqa: E402
from llm import get_client, image_block, load_env  # noqa: E402

load_env()
app = FastAPI(title="TradeGuard API", version="0.1")

DOC_ORDER = [("letter_of_credit", "신용장"), ("commercial_invoice", "상업송장"),
             ("bill_of_lading", "선하증권")]
SAMPLE_DIRS = [ROOT / "samples", ROOT / "benchmark" / "cases"]


# ---------- 공통 ----------
def find_case(case_id: str) -> dict:
    for d in SAMPLE_DIRS:
        p = d / f"{case_id}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    raise HTTPException(404, f"케이스를 찾을 수 없습니다: {case_id}")


def fx_block(docs: dict) -> dict:
    """추출된 서류에서 현금흐름을 만들고 환노출을 계산한다 (fx_exposure 스키마 축약형)"""
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

    scenarios = []
    if amount:
        for name, pct in (("krw_strong", -5), ("base", 0), ("krw_weak", 5)):
            r = rate * (1 + pct / 100)
            scenarios.append({"name": name, "rate_change_pct": pct,
                              "assumed_rate": round(r, 2),
                              "pnl_krw": round(amount * (r - rate))})
    return {
        "spot_rate": {"KRW/USD": round(rate, 2)}, "rate_source": rate_src, "rate_date": rate_date,
        "cash_flows": ([{"expected_date": ship, "direction": "inflow", "currency": currency,
                         "amount": amount, "certainty": "estimated",
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
            "rationale_ko": (f"환율이 5% 하락하면 이 거래에서만 약 "
                             f"{abs(round(amount * rate * 0.05)):,}원의 환차손이 발생합니다."
                             if amount else "금액을 판독하지 못해 계산할 수 없습니다."),
        },
    }


def analyze(docs: dict, case_id: str, presentation_date=None, meta=None) -> dict:
    report = build_report(case_id, docs, parse_date(presentation_date))
    return {"case_id": case_id, "documents": docs, "report": report,
            "fx": fx_block(docs), "meta": meta or {}}


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
    for d in SAMPLE_DIRS:
        for p in sorted(d.glob("*.json")):
            try:
                c = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if "documents" not in c:
                continue
            out.append({"case_id": c["case_id"], "label": c.get("label"),
                        "defect_types": c.get("defect_types", []),
                        "note": (c.get("scenario_note_ko") or "")[:90],
                        "expected_grade": (c.get("ground_truth") or {}).get("overall_risk", {}).get("grade")})
    # 데모 추천 순서: A등급 함정 → C등급 → D등급
    prio = {"CLEAN-017": 0, "DEFECT-019": 1, "DEFECT-001": 2}
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


@app.post("/api/analyze/upload")
async def analyze_upload(files: list[UploadFile] = File(...)):
    client = get_client()
    if client is None:
        raise HTTPException(503, "LLM 키가 없습니다. .env를 확인하세요 (샘플 모드는 사용 가능).")
    if not files:
        raise HTTPException(400, "파일이 없습니다")

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
    res = analyze(docs, "UPLOAD", None,
                  meta={"mode": "upload", "provider": client.name, "docs": n,
                        "cost_usd": round(n * 0.02, 3),  # 대략치
                        "elapsed_sec": round(time.time() - t0, 1),
                        "errors": errors,
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


app.mount("/mockups", StaticFiles(directory=str(ROOT / "mockups")), name="mockups")
app.mount("/render", StaticFiles(directory=str(ROOT / "render")), name="render")


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
