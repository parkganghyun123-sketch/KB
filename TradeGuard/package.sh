#!/usr/bin/env bash
# TradeGuard 제출물 패키징 — 시크릿 유출 없이 ZIP을 만들고 스스로 검증한다
#
#   bash TradeGuard/package.sh              # dist/TradeGuard_제출_YYYYMMDD.zip 생성 + 검증
#   bash TradeGuard/package.sh --no-test    # 압축 해제본 테스트 생략 (빠른 확인용)
#
# 왜 `zip -r`을 쓰지 않는가:
#   zip은 .gitignore를 보지 않는다. `zip -r out.zip TradeGuard/`로 만들면
#   API 키가 든 .env, .venv/, __pycache__/, .DS_Store가 그대로 딸려 간다.
#   git archive는 **커밋된 추적 파일만** 담으므로 .gitignore가 자동으로 지켜진다.
#
# 전제: 작업트리가 깨끗해야 한다. git archive는 HEAD를 담으므로
#       미커밋 변경은 제출물에 들어가지 않는다.
set -u
cd "$(dirname "$0")/.."          # 레포 루트(KB/)로 이동
ROOT="$(pwd)"

RUN_TEST=1
[ "${1:-}" = "--no-test" ] && RUN_TEST=0

STAMP="$(date +%Y%m%d)"
OUT_DIR="$ROOT/dist"
OUT="$OUT_DIR/TradeGuard_제출_$STAMP.zip"
mkdir -p "$OUT_DIR"

fail() { echo "  ❌ $1"; exit 1; }
ok()   { echo "  ✅ $1"; }

echo "══ 1/5 작업트리 상태 확인 ══"
if [ -n "$(git status --porcelain)" ]; then
  echo "  ⚠️  미커밋 변경이 있습니다. git archive는 HEAD만 담으므로 아래 내용은 제출물에서 빠집니다:"
  git status --short | sed 's/^/       /'
  echo
  printf "  그래도 계속할까요? [y/N] "
  read -r ans
  [ "$ans" = "y" ] || [ "$ans" = "Y" ] || fail "중단했습니다. 먼저 커밋하세요."
else
  ok "작업트리 깨끗함 (HEAD = $(git rev-parse --short HEAD))"
fi

echo "══ 2/5 ZIP 생성 (git archive — 추적 파일만) ══"
rm -f "$OUT" "$OUT.tmp"
git archive --format=zip --prefix="TradeGuard-submission/" HEAD -o "$OUT.tmp" \
  || fail "git archive 실패"

# 내부 전략·협업 문서는 제출물에서 뺀다.
#
# 심사위원에게 필요한 것은 README·코드·스키마·벤치마크다. 경쟁작 분석이나 우승 전략,
# 팀원 간 요청서는 우리가 일하려고 쓴 것이고, 심사위원이 읽을 이유가 없다.
# 특히 경쟁작 분석은 다른 팀을 언급하므로 제출물에 들어가면 곤란하다.
# 개인 서류는 .gitignore가 이미 막지만, 여기서 한 번 더 막는다.
EXCLUDE=(
  "TradeGuard-submission/TradeGuard/경쟁작_분석_*"
  "TradeGuard-submission/TradeGuard/우승전략_*"
  "TradeGuard-submission/TradeGuard/종합분석보고서_*"
  "TradeGuard-submission/TradeGuard/심사관점_최종검토_*"
  "TradeGuard-submission/TradeGuard/제출_체크리스트_*"
  "TradeGuard-submission/TradeGuard/PPT_프롬프트_*"
  "TradeGuard-submission/TradeGuard/A_요청_*"
  "TradeGuard-submission/TradeGuard/A_회신_*"
  "TradeGuard-submission/TradeGuard/AB_요청_*"
  "TradeGuard-submission/TradeGuard/D1_*"
  "TradeGuard-submission/TradeGuard/D2_*"
  "TradeGuard-submission/TradeGuard/D1-D2_*"
  "TradeGuard-submission/TradeGuard/HANDOFF_*"
  "TradeGuard-submission/TradeGuard/작업요약_*"
  "TradeGuard-submission/TradeGuard/BACKLOG.md"
  # 경쟁팀 실명을 표로 비교한다. 제출물에 들어가면 곤란하다.
  "TradeGuard-submission/TradeGuard/UX_벤치마킹_차별화설계.md"
  "TradeGuard-submission/2026_KB_AI_Challenge_주제전략_분석.md"
  "TradeGuard-submission/*AICHALLENGE*"
  "TradeGuard-submission/*참가신청서*"
  "TradeGuard-submission/*서약서*"
  "TradeGuard-submission/*개인정보*"
)
cp "$OUT.tmp" "$OUT"
zip -dq "$OUT" "${EXCLUDE[@]}" 2>/dev/null
REMOVED=$(( $(unzip -Z1 "$OUT.tmp" | wc -l) - $(unzip -Z1 "$OUT" | wc -l) ))
rm -f "$OUT.tmp"
ok "내부 전략·협업 문서 ${REMOVED}건 제외 (심사위원에게 필요 없는 자료)"
ok "생성: ${OUT#$ROOT/} ($(du -h "$OUT" | cut -f1) · $(unzip -Z1 "$OUT" | wc -l | tr -d ' ')개 파일)"

echo "══ 3/5 시크릿·불필요 파일 검사 ══"
LIST="$(unzip -Z1 "$OUT")"

# 내부 문서가 실제로 빠졌는지 확인한다. 목록만 적어두고 안 지워지면 의미가 없다.
LEAK="$(echo "$LIST" | grep -E '경쟁작_분석|우승전략|종합분석보고서|심사관점|제출_체크리스트|PPT_프롬프트|_요청_|_회신_|HANDOFF|작업요약|주제전략_분석|벤치마킹_차별화|참가신청서|서약서|개인정보' || true)"
[ -z "$LEAK" ] || { echo "$LEAK" | sed 's/^/       /'; fail "내부 문서가 제출물에 남아 있음"; }
ok "내부 전략문서·개인 서류 미포함 확인"

# 남은 문서에 경쟁팀 실명이 들어 있으면 안 된다. 파일명만 거르면 놓친다.
RIVALS="$(cd "$ROOT" && for f in $(echo "$LIST" | grep '\.md$' | sed 's|^TradeGuard-submission/||'); do
  [ -f "$f" ] && grep -lE '환부장|TradePilot|경쟁팀' "$f" 2>/dev/null; done || true)"
[ -z "$RIVALS" ] || { echo "$RIVALS" | sed 's/^/       /'; fail "경쟁팀을 언급한 문서가 제출물에 포함됨"; }
ok "경쟁팀 실명 언급 없음"

# 심사위원이 실제로 읽어야 할 것은 반드시 들어 있어야 한다.
for must in "README.md:README" "TradeGuard/server/app.py:백엔드" \
            "TradeGuard/server/app.html:웹 앱" \
            "TradeGuard/pipeline/detect.py:판정 엔진" "TradeGuard/pipeline/ucp600_kb.json:UCP600 지식베이스" \
            "TradeGuard/test_all.sh:테스트" "TradeGuard/demo.sh:원커맨드 실행" \
            "TradeGuard/DEMO_시나리오.md:실행 안내" "TradeGuard/.env.example:환경설정 템플릿"; do
  f="${must%%:*}"; label="${must##*:}"
  echo "$LIST" | grep -qx "TradeGuard-submission/$f" || fail "필수 파일 누락: $f ($label)"
done
ok "필수 파일 9종 포함 확인 (README · 앱 · 판정 엔진 · 지식베이스 · 테스트 · 실행 안내)"
# .env.example은 값이 비어 있는 템플릿이므로 허용한다. 실제 .env만 차단.
BAD="$(echo "$LIST" | grep -E '(^|/)\.env$|(^|/)\.venv/|__pycache__/|(^|/)\.DS_Store$|\.key$|(^|/)secrets\.json$' || true)"
[ -z "$BAD" ] || { echo "$BAD" | sed 's/^/       /'; fail "제외돼야 할 파일이 포함됨"; }
ok ".env · .venv · __pycache__ · .DS_Store · *.key 없음"

echo "$LIST" | grep -q '\.env\.example$' \
  && ok ".env.example 포함됨 (심사위원이 키 설정 방법을 알 수 있음)" \
  || echo "  ⚠️  .env.example이 없습니다 — 심사위원이 환경 설정을 못 합니다"

echo "══ 4/5 압축 해제 후 시크릿 패턴 스캔 ══"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
unzip -q "$OUT" -d "$TMP" || fail "압축 해제 실패"
SRC="$TMP/TradeGuard-submission"

# 파일 '내용'에 키가 박혀 있는지 검사한다. 파일명 검사만으로는 부족하다.
python3 - "$SRC" <<'PYEOF' || exit 1
import os, re, sys
root = sys.argv[1]
pats = {
    "OpenAI 키":    r"sk-[A-Za-z0-9_\-]{20,}",
    "Anthropic 키": r"sk-ant-[A-Za-z0-9_\-]{20,}",
    "AWS 키":       r"AKIA[0-9A-Z]{16}",
    "공공데이터 키": r"\b[A-Za-z0-9%+/=]{60,}\b",
}
hits = []
for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules")]
    for fn in filenames:
        p = os.path.join(dirpath, fn)
        if fn == ".env.example":
            continue
        try:
            t = open(p, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for name, pat in pats.items():
            if re.search(pat, t):
                hits.append((os.path.relpath(p, root), name))
if hits:
    print("  ❌ 시크릿 패턴 발견 (값은 출력하지 않음):")
    for f, n in hits:
        print(f"       {f} — {n}")
    sys.exit(1)
print("  ✅ 시크릿 패턴 없음 (OpenAI · Anthropic · AWS · 공공데이터)")
PYEOF

echo "══ 5/5 압축 해제본 자체 검증 ══"
if [ "$RUN_TEST" -eq 0 ]; then
  echo "  ⏭  --no-test 지정 — 건너뜀"
elif python3 -c "import jsonschema, jinja2" 2>/dev/null; then
  # 심사위원이 클론 대신 ZIP을 풀어서 돌릴 수도 있다. 그 경로가 실제로 동작하는지 확인한다.
  if (cd "$SRC/TradeGuard" && bash test_all.sh >"$TMP/test.log" 2>&1); then
    ok "압축 해제본에서 test_all.sh 통과 ($(grep -c '✅' "$TMP/test.log")개 항목)"
  else
    tail -20 "$TMP/test.log" | sed 's/^/       /'
    fail "압축 해제본이 테스트를 통과하지 못함 — 누락된 파일이 있을 수 있습니다"
  fi
else
  echo "  ⏭  건너뜀 (pip install -r TradeGuard/requirements.txt 후 재실행)"
fi

echo
echo "════════════════════════════════════════"
echo "  제출물 준비 완료"
echo "  → $OUT"
echo "  커밋: $(git rev-parse --short HEAD) · $(git log -1 --format=%s | cut -c1-50)"
echo "════════════════════════════════════════"
