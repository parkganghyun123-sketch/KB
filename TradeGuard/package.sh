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
rm -f "$OUT"
git archive --format=zip --prefix="TradeGuard-submission/" HEAD -o "$OUT" \
  || fail "git archive 실패"
ok "생성: ${OUT#$ROOT/} ($(du -h "$OUT" | cut -f1) · $(unzip -l "$OUT" | tail -1 | awk '{print $2}')개 파일)"

echo "══ 3/5 시크릿·불필요 파일 검사 ══"
LIST="$(unzip -Z1 "$OUT")"
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
