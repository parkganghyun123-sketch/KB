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


# ---------- 검증 CLI ----------
def check():
    load_env()
    ok = True
    print("=== 1) 한국은행 ECOS (주 데이터원) ===")
    try:
        t, v = ecos_spot()
        print(f"  ✅ 정상 — 최근 매매기준율 {t}: {v:,.2f} KRW/USD")
    except Exception as e:
        ok = False
        print(f"  ❌ 실패: {e}")

    print("\n=== 2) 관세청 관세환율 (보조) ===")
    try:
        # 최근 월요일(주간환율 적용 개시일) 기준으로 시도
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        items = customs_fx(monday.strftime("%Y%m%d"))
        usd = [x for x in items if "US" in str(x.values()) or "달러" in str(x.values())][:1]
        print(f"  ✅ 정상 — {len(items)}개 통화 수신 (적용개시 {monday})")
        if usd:
            print(f"     샘플: {usd[0]}")
    except Exception as e:
        ok = False
        print(f"  ⚠️  실패: {e}")
        print("     확인 순서: ① 마이페이지에서 '승인' 상태인지 ② 디코딩(원본) 키를 썼는지"
              " ③ 상세페이지의 파라미터명이 aplyBgnDt/imexTp/weekFxrtTpcd가 맞는지")

    print("\n" + ("모든 키 정상 — D5 환노출 모듈 진행 가능" if ok else
                  "일부 실패 — ECOS만 정상이면 D5는 진행 가능합니다(관세청은 보조)"))
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
