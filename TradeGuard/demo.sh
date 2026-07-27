#!/usr/bin/env bash
# TradeGuard 데모 준비 — 명령 하나로 실데이터 화면을 전부 재생성하고 서버를 띄운다
#
#   bash demo.sh              # 재생성 + 서버 기동 (http://localhost:8000/mockups/)
#   bash demo.sh --no-serve   # 재생성만
#   bash demo.sh --port 8080
#
# LLM 호출 없음 = 비용 0원. 발표 직전에 돌려 화면을 최신 상태로 맞추세요.
set -e
cd "$(dirname "$0")"

# Windows의 앱 실행 별칭(python3.exe)이 실제 Python이 아닌 경우가 있어
# 동작하는 python 명령으로 자동 폴백한다.
if ! python3 -c "import sys" >/dev/null 2>&1; then
  if python -c "import sys" >/dev/null 2>&1; then
    python3() { python "$@"; }
  else
    echo "Python 3 실행기를 찾을 수 없습니다."
    exit 1
  fi
fi
export PYTHONUTF8=1

PORT=8000
SERVE=1
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2;;
    --no-serve) SERVE=0; shift;;
    *) shift;;
  esac
done

echo "══ 1/4 서류 렌더링 (샘플 케이스) ══"
python3 render/render.py samples/DEFECT-001.json --out render/sample_output
for c in CLEAN-017 DEFECT-019 DEFECT-011; do
  python3 render/render.py "benchmark/cases/$c.json" --out render/sample_output >/dev/null
done
echo "  ✅ 서류 HTML 생성 완료 (render/sample_output/)"

echo "══ 2/4 하자 리포트 — detect.py 실제 판정 ══"
python3 render/render_report.py samples/DEFECT-001.json --out mockups/screen3_live.html
for c in CLEAN-017 DEFECT-019 DEFECT-011; do
  python3 render/render_report.py "benchmark/cases/$c.json" --out "mockups/live_$c.html"
done

echo "══ 3/4 판독 화면 — 추출 결과 렌더 ══"
python3 render/render_extraction.py --from-case samples/DEFECT-001.json \
        --out mockups/screen2_live.html --docs ../render/sample_output

echo "══ 4/4 환율 스냅샷 — 한국은행 ECOS ══"
python3 render/fx_snapshot.py

echo
echo "════════════════════════════════════════"
echo "  데모 준비 완료"
echo "  시나리오: DEMO_시나리오.md"
if [ "$SERVE" -eq 1 ]; then
  if python3 -c "import fastapi, uvicorn" 2>/dev/null; then
    echo "  → http://localhost:$PORT/            ★ 실동작 앱 (업로드·분석)"
    echo "  → http://localhost:$PORT/mockups/    정적 화면 모음"
    echo "════════════════════════════════════════"
    python3 server/app.py --port "$PORT"
  else
    echo "  ⚠️  FastAPI 미설치 — 정적 화면만 제공합니다."
    echo "     실동작 앱을 쓰려면: pip install fastapi \"uvicorn[standard]\" python-multipart"
    echo "  → http://localhost:$PORT/mockups/   (Ctrl+C로 종료)"
    echo "════════════════════════════════════════"
    python3 -m http.server "$PORT" >/dev/null 2>&1
  fi
else
  echo "  서버는 띄우지 않았습니다: python3 server/app.py --port $PORT"
  echo "════════════════════════════════════════"
fi
