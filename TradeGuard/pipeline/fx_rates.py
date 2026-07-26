#!/usr/bin/env python3
"""TradeGuard 환율 연동 — 한국은행 ECOS(주) + 관세청 관세환율(보조)

데이터원 역할 구분:
  · ECOS 731Y001  : 일별 매매기준율. **화면4 환노출 계산의 기준값** (주 데이터원)
  · 관세청 관세환율: 주간 고시환율. 관세 산출 기준이며 실시간성이 낮음 → **보조·교차검증용**

키는 코드에 넣지 말고 .env에 둘 것 (.env.example 참고):
  ECOS_API_KEY=...
  DATA_GO_KR_KEY=...        # 디코딩(원본) 키를 넣을 것. params로 넘기므로 인코딩 키는 오류남

사용법:
  python3 fx_rates.py --check              # 두 API 키 동작 검증 (오늘 할 일)
  python3 fx_rates.py --spot               # 최신 원/달러 매매기준율
  python3 fx_rates.py --spot --date 20260724
"""
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

ECOS_BASE = "https://ecos.bok.or.kr/api/StatisticSearch"
CUSTOMS_FX = "https://apis.data.go.kr/1220000/RetrieveTrifFxrtInfo/getRetrieveTrifFxrtInfo"
CUSTOMS_TRADE = "https://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList"  # 품목별 국가별 수출입실적
ECOS_STAT, ECOS_ITEM_USD = "731Y001", "0000001"  # 주요국 통화의 대원화환율 / 원-미국달러(매매기준율)


def load_env(path=None):
    """의존성 없이 .env를 읽어 os.environ에 반영 (python-dotenv 불필요)"""
    p = Path(path or Path(__file__).resolve().parent.parent / ".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ---------- 한국은행 ECOS (주 데이터원) ----------
def ecos_rates(start: str, end: str, item=ECOS_ITEM_USD, key=None):
    """기간 내 일별 환율. 반환: [(YYYYMMDD, float), ...] 오름차순"""
    key = key or os.environ.get("ECOS_API_KEY")
    if not key:
        raise RuntimeError("ECOS_API_KEY 없음 — .env를 확인하세요")
    url = f"{ECOS_BASE}/{key}/json/kr/1/100/{ECOS_STAT}/D/{start}/{end}/{item}"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    body = r.json()
    if "StatisticSearch" not in body:
        raise RuntimeError(f"ECOS 오류 응답: {body}")
    return [(x["TIME"], float(x["DATA_VALUE"])) for x in body["StatisticSearch"]["row"]]


def ecos_spot(on: date = None, lookback=10, key=None):
    """지정일(기본 오늘) 기준 가장 최근 영업일 환율. 반환: (YYYYMMDD, rate)"""
    on = on or date.today()
    rows = ecos_rates((on - timedelta(days=lookback)).strftime("%Y%m%d"),
                      on.strftime("%Y%m%d"), key=key)
    if not rows:
        raise RuntimeError("ECOS 데이터 없음 — 조회 기간을 늘려보세요")
    return rows[-1]


# ---------- 관세청 관세환율 (보조) ----------
def customs_fx(apply_date: str, imex="2", week_tp="2", key=None, timeout=15):
    """관세청 주간 관세환율. imex: 1=수출 2=수입 / week_tp: 관세청 정의 주간환율구분코드
    반환: [{country, currency, rate, ...}] · 실패 시 예외
    ⚠️ 파라미터명은 관세청 문서 개정에 따라 달라질 수 있으니 --check로 먼저 검증할 것"""
    key = key or os.environ.get("DATA_GO_KR_KEY")
    if not key:
        raise RuntimeError("DATA_GO_KR_KEY 없음 — .env를 확인하세요")
    params = {"serviceKey": key, "aplyBgnDt": apply_date, "imexTp": imex, "weekFxrtTpcd": week_tp}
    r = requests.get(CUSTOMS_FX, params=params, timeout=timeout)
    r.raise_for_status()
    text = r.text
    if "SERVICE_KEY_IS_NOT_REGISTERED" in text or "SERVICE ERROR" in text:
        raise RuntimeError(f"관세청 API 키 오류(승인 대기 또는 인코딩 키 사용 의심): {text[:300]}")
    # 응답이 XML — 표준 라이브러리로 파싱
    import xml.etree.ElementTree as ET
    root = ET.fromstring(text)
    items = []
    for it in root.iter("item"):
        items.append({c.tag: (c.text or "").strip() for c in it})
    if not items:
        raise RuntimeError(f"관세청 응답에 item 없음 (파라미터 확인 필요): {text[:300]}")
    return items


def customs_trade(hs_sgn: str, start_ym: str, end_ym: str, cntry_cd=None, key=None, timeout=15):
    """관세청 품목별·국가별 수출입실적. hs_sgn: HS코드(2/4/6/10자리), start_ym/end_ym: YYYYMM
    거래 규모 맥락 제공용(보조). 키는 DATA_GO_KR_KEY_TRADE > DATA_GO_KR_KEY 순으로 사용"""
    key = key or os.environ.get("DATA_GO_KR_KEY_TRADE") or os.environ.get("DATA_GO_KR_KEY")
    if not key:
        raise RuntimeError("DATA_GO_KR_KEY(_TRADE) 없음 — .env를 확인하세요")
    params = {"serviceKey": key, "strtYymm": start_ym, "endYymm": end_ym, "hsSgn": hs_sgn}
    if cntry_cd:
        params["cntyCd"] = cntry_cd
    r = requests.get(CUSTOMS_TRADE, params=params, timeout=timeout)
    r.raise_for_status()
    import xml.etree.ElementTree as ET
    root = ET.fromstring(r.text)
    items = [{c.tag: (c.text or "").strip() for c in it} for it in root.iter("item")]
    if not items:
        raise RuntimeError(f"응답에 item 없음 (파라미터·승인상태 확인): {r.text[:300]}")
    return items


# ---------- 검증 CLI ----------
def _diagnose(e):
    """네트워크 차단과 키 오류를 구분해 안내"""
    s = str(e)
    if "ProxyError" in s or "Tunnel connection failed" in s or "Max retries" in s:
        return "네트워크 차단(외부 접속 불가 환경) — 키 문제가 아닙니다. 개인 PC에서 다시 실행하세요."
    return s[:300]



def check():
    load_env()
    keys = {"ECOS_API_KEY": os.environ.get("ECOS_API_KEY"),
            "DATA_GO_KR_KEY": os.environ.get("DATA_GO_KR_KEY"),
            "DATA_GO_KR_KEY_TRADE": os.environ.get("DATA_GO_KR_KEY_TRADE"),
            "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY")}
    print("=== 0) .env 로딩 상태 ===")
    for k, v in keys.items():
        print(f"  {'✅' if v else '⬜'} {k:22s} {(v[:6] + '…' + v[-4:]) if v else '(비어 있음)'}")

    ok = True
    print("\n=== 1) 한국은행 ECOS (주 데이터원) ===")
    try:
        t, v = ecos_spot()
        print(f"  ✅ 정상 — 최근 매매기준율 {t}: {v:,.2f} KRW/USD")
    except Exception as e:
        ok = False
        print(f"  ❌ 실패: {_diagnose(e)}")

    print("\n=== 2) 관세청 관세환율 (보조) ===")
    try:
        today = date.today()
        monday = today - timedelta(days=today.weekday())  # 주간환율 적용 개시일
        items = customs_fx(monday.strftime("%Y%m%d"))
        print(f"  ✅ 정상 — {len(items)}개 통화 수신 (적용개시 {monday})")
        print(f"     샘플: {items[0]}")
    except Exception as e:
        ok = False
        print(f"  ⚠️  실패: {_diagnose(e)}")
        print("     확인 순서: ① 마이페이지에서 '승인' 상태인지 ② 디코딩(원본) 키인지"
              " ③ 상세페이지 요청변수가 aplyBgnDt/imexTp/weekFxrtTpcd가 맞는지")

    print("\n=== 3) 관세청 품목별·국가별 수출입실적 (보조) ===")
    try:
        items = customs_trade("7616", "202601", "202606")  # 알루미늄 제품 예시
        print(f"  ✅ 정상 — {len(items)}건 수신")
        print(f"     샘플: {items[0]}")
    except Exception as e:
        ok = False
        print(f"  ⚠️  실패: {_diagnose(e)}")
        print("     확인 순서: ① 별도 승인 필요 ② 요청변수(strtYymm/endYymm/hsSgn) 상세페이지와 대조")

    print("\n" + ("모든 데이터원 정상 — D5 환노출 모듈 진행 가능" if ok else
                  "⚠️ 일부 실패 — ECOS만 정상이면 D5는 진행 가능합니다(관세청은 보조 데이터원)"))
    return 0 if ok else 1


def main():
    args = sys.argv[1:]
    if "--check" in args or not args:
        sys.exit(check())
    load_env()
    if "--spot" in args:
        on = None
        if "--date" in args:
            s = args[args.index("--date") + 1]
            on = date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        t, v = ecos_spot(on)
        print(f"{t}\t{v}")


if __name__ == "__main__":
    main()
