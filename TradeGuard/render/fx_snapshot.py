#!/usr/bin/env python3
"""환율 스냅샷 생성 — ECOS 실환율 → mockups/fx_snapshot.json

화면4(환노출 시뮬레이터)가 이 파일을 읽어 기본 환율로 사용한다.
네트워크·키 문제로 실패해도 데모가 멈추지 않도록 폴백 값을 남긴다.

사용법:
  python3 render/fx_snapshot.py                    # ECOS 조회 → mockups/fx_snapshot.json
  python3 render/fx_snapshot.py --out other.json
"""
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

FALLBACK = {"rate": 1385.0, "date": None, "source": "fallback",
            "label": "시연용 기본값 · ECOS 조회 실패"}

# 변동성 실측 실패 시의 보수적 대체값.
# 화면에 "가정치"임을 반드시 표기해 실측값과 구분한다.
VOL_FALLBACK = {"sigma_annual": 0.09, "source": "fallback",
                "method_ko": "실측 실패 — 시연용 가정치 (원/달러 통상 범위)"}


def scrub(msg: str) -> str:
    """오류 메시지에서 API 키를 제거한다.
    ECOS는 키를 URL 경로에 넣으므로 예외 문자열에 키가 그대로 실린다.
    이 파일은 저장소에 커밋되므로 반드시 마스킹해야 한다."""
    msg = str(msg)
    for k in ("ECOS_API_KEY", "DATA_GO_KR_KEY", "DATA_GO_KR_KEY_TRADE",
              "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        v = os.environ.get(k)
        if v and len(v) > 6:
            msg = msg.replace(v, f"<{k}>")
    # 혹시 남은 긴 영숫자 토큰(키 형태)도 제거
    msg = re.sub(r"\b[A-Z0-9]{16,}\b", "<REDACTED>", msg)
    msg = re.sub(r"\bsk-[A-Za-z0-9_-]{10,}\b", "<REDACTED>", msg)
    return msg


def volatility():
    """최근 1년 실측 연율 변동성. 환노출 시뮬레이터가 √t 규칙으로 기간 변동성을 환산할 때 쓴다."""
    try:
        from fx_rates import ecos_volatility
        return ecos_volatility()
    except Exception as ex:
        out = dict(VOL_FALLBACK)
        out["error"] = scrub(ex)[:200]
        return out


def snapshot():
    try:
        from fx_rates import ecos_spot, load_env
        load_env()
        t, v = ecos_spot()
        out = {"rate": v, "date": f"{t[:4]}-{t[4:6]}-{t[6:]}", "source": "ecos",
               "label": "한국은행 ECOS 매매기준율 (통계코드 731Y001)",
               "fetched_at": date.today().isoformat()}
        out["volatility"] = volatility()
        return out
    except Exception as ex:
        out = dict(FALLBACK)
        out["error"] = scrub(ex)[:200]
        out["fetched_at"] = date.today().isoformat()
        out["volatility"] = dict(VOL_FALLBACK)
        return out


def main():
    args = sys.argv[1:]
    out = Path(args[args.index("--out") + 1]) if "--out" in args else ROOT / "mockups" / "fx_snapshot.json"
    data = snapshot()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    vol = data.get("volatility", {})
    vtag = (f"σ {vol.get('sigma_annual', 0):.2%} ({vol.get('source')})" if vol else "σ 없음")
    if data["source"] == "ecos":
        print(f"[fx] ✅ ECOS {data['date']} · {data['rate']:,.2f} KRW/USD · {vtag} → {out}")
    else:
        print(f"[fx] ⚠️  폴백 {data['rate']:,.2f} 사용 · {vtag} "
              f"(사유: {data.get('error', '')[:60]}) → {out}")


if __name__ == "__main__":
    main()
