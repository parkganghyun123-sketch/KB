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
  echo "  → http://localhost:$PORT/mockups/   (Ctrl+C로 종료)"
  echo "════════════════════════════════════════"
  python3 -m http.server "$PORT" >/dev/null 2>&1
else
  echo "  서버는 띄우지 않았습니다: python3 -m http.server $PORT"
  echo "════════════════════════════════════════"
fi
