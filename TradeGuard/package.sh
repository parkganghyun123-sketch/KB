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

# 제출물에는 **읽는 사람에게 필요한 것만** 담는다.
#
# 코드·스키마·벤치마크·실행 안내가 그 대상이다. 팀원 간 요청서와 회신, 일정 브리프,
# 진행 상황 메모는 우리가 개발하려고 주고받은 기록이라 결과물의 일부가 아니다.
# 개인정보가 담긴 제출 서류는 .gitignore가 이미 막지만, 여기서 한 번 더 막는다.
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
  # 배포 절차 메모. 접근 코드 설정법이 들어 있어 제출물에 넣지 않는다.
  "TradeGuard-submission/TradeGuard/배포_가이드*"
  "TradeGuard-submission/TradeGuard/BACKLOG.md"
  # 개발 중 참고한 시장 조사 메모. 결과물이 아니다.
  "TradeGuard-submission/TradeGuard/UX_벤치마킹_차별화설계.md"
  # 팀 내부 시연 준비 메모. 실행 안내는 실행_가이드.md 로 따로 제공한다.
  "TradeGuard-submission/TradeGuard/DEMO_시나리오.md"
  # UI 참고용 내부 조사 메모.
  "TradeGuard-submission/TradeGuard/KB_디지털_UI_리서치.md"
  "TradeGuard-submission/2026_KB_AI_Challenge_주제전략_분석.md"
  # 본문 차단어 목록. 목록 자체에 타 출품작 이름이 있어 제출물에 넣지 않는다.
  "TradeGuard-submission/TradeGuard/.submission-blocklist"
  # 이 스크립트 자체. 제출물을 **만드는** 도구지 제출물의 일부가 아니다.
  # git 저장소 밖에서는 동작하지도 않는다(git archive에 의존한다).
  "TradeGuard-submission/TradeGuard/package.sh"
  # 최종 제출 폴더가 실수로 커밋되면 ZIP이 **자기 자신을 담는다**.
  # 완성된 ZIP·PDF와 개인정보 서류가 통째로 들어가므로 반드시 막는다.
  "TradeGuard-submission/TradeGuard — 수출*"
  "TradeGuard-submission/TradeGuard — 수출*/*"
  "TradeGuard-submission/*.zip"
  "TradeGuard-submission/*/*.zip"
  "TradeGuard-submission/*AICHALLENGE*"
  "TradeGuard-submission/*참가신청서*"
  "TradeGuard-submission/*서약서*"
  "TradeGuard-submission/*개인정보*"
)
# zip -d 로 지우지 않고 **새로 쓴다.**
# macOS의 zip은 항목을 지운 뒤 아카이브에 잔여 바이트를 남기고, 그러면 unzip이
# 경고와 함께 종료코드 1을 돌려준다. 압축은 정상인데 스크립트는 실패로 읽는다.
# 파이썬 zipfile로 다시 쓰면 그 문제가 없고 유니코드 경로도 안전하다.
python3 - "$OUT.tmp" "$OUT" "${EXCLUDE[@]}" <<'PYEOF' || exit 1
import sys, zipfile, fnmatch, unicodedata
src, dst, pats = sys.argv[1], sys.argv[2], sys.argv[3:]
pats = [unicodedata.normalize("NFC", p) for p in pats]
zin = zipfile.ZipFile(src)
kept = dropped = 0
with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
    for info in zin.infolist():
        name = unicodedata.normalize("NFC", info.filename)
        if any(fnmatch.fnmatch(name, p) for p in pats):
            dropped += 1
            continue
        zout.writestr(info, zin.read(info.filename))
        kept += 1
print(f"  ✅ 내부 협업 문서 {dropped}건 제외")
print(f"  ✅ 남은 파일 {kept}개")
PYEOF
rm -f "$OUT.tmp"
ok "생성: ${OUT#$ROOT/} ($(du -h "$OUT" | cut -f1))"

echo "══ 3/5 시크릿·불필요 파일 검사 ══"
LIST="$(python3 -c "import sys,zipfile;print('\n'.join(zipfile.ZipFile(sys.argv[1]).namelist()))" "$OUT")"

# 제외 목록이 실제로 적용됐는지, 남은 문서에 타 출품작 언급이 없는지 확인한다.
# 목록만 적어두고 안 지워지면 의미가 없고, 파일명만 걸러도 본문은 놓친다.
# 여기서도 셸 grep 대신 Python을 쓴다 — 한글 경로 정규화 차이로 '못 찾음'이
# '없음'으로 오독되면 검사가 통과한 것처럼 보이면서 실제로는 새어 나간다.
python3 - "$OUT" "$ROOT/TradeGuard/.submission-blocklist" <<'PYEOF' || exit 1
import os, sys, zipfile, unicodedata
BANNED_NAME = ["경쟁작_분석", "우승전략", "종합분석보고서", "심사관점", "제출_체크리스트",
               "PPT_프롬프트", "_요청_", "_회신_", "HANDOFF", "작업요약", "주제전략_분석",
               "벤치마킹_차별화", "참가신청서", "서약서", "개인정보", "BACKLOG"]
# 본문 차단어는 별도 파일에서 읽는다. 목록에 타 출품작 이름이 들어 있어
# 이 스크립트에 그대로 적으면 검사의 목적과 정반대가 된다(그 파일은 ZIP에서 제외된다).
blockfile = sys.argv[2]
BANNED_BODY = []
if os.path.exists(blockfile):
    BANNED_BODY = [l.strip() for l in open(blockfile, encoding="utf-8")
                   if l.strip() and not l.startswith("#")]
z = zipfile.ZipFile(sys.argv[1])
names = [(n, unicodedata.normalize("NFC", n)) for n in z.namelist()]

leak = [n for n, k in names if any(b in k for b in BANNED_NAME)]
if leak:
    for n in leak: print(f"       {n}")
    print("  ❌ 내부 협업 문서가 제출물에 남아 있음"); sys.exit(1)
print("  ✅ 내부 협업 문서·개인 서류 미포함 확인")

# 자기 자신(ZIP)이나 완성된 PDF가 안에 들어가면 안 된다.
# 제출 폴더를 커밋하면 그대로 벌어진다 — 용량이 10배가 되고 심사위원이 혼란스럽다.
nested = [n for n, k in names if k.lower().endswith((".zip", ".pdf", ".pptx"))]
if nested:
    for n in nested: print(f"       {n}")
    print("  ❌ 제출물 안에 완성 산출물(ZIP·PDF·PPTX)이 들어 있음"); sys.exit(1)
print("  ✅ 자기 중첩 없음 (ZIP·PDF·PPTX 미포함)")

rivals = []
for n, k in names:
    if not k.lower().endswith((".md", ".txt", ".html", ".json")): continue
    try: t = z.read(n).decode("utf-8", "ignore")
    except Exception: continue
    hit = [b for b in BANNED_BODY if b in t]
    if hit: rivals.append((n, ", ".join(hit)))
if rivals:
    for n, h in rivals: print(f"       {n} — {h}")
    print("  ❌ 타 출품작을 언급한 문서가 제출물에 포함됨"); sys.exit(1)
print(f"  ✅ 타 출품작 언급 없음 (본문 {sum(1 for _, k in names if k.lower().endswith(('.md','.txt','.html','.json')))}개 파일 검사)")
PYEOF

# 실행에 반드시 필요한 파일이 빠지지 않았는지 확인한다.
# 한글 파일명은 셸 grep으로 맞추면 안 된다. macOS는 파일명을 NFD로,
# git·리눅스는 NFC로 다루기 때문에 같은 이름이 바이트 단위로 달라진다.
# 실제로 ZIP에 들어 있는 파일을 "없다"고 오판한다. 정규화까지 하는 Python으로 검사한다.
python3 - "$OUT" <<'PYEOF' || exit 1
import sys, zipfile, unicodedata
must = [("README.md", "README"),
        ("TradeGuard/server/app.py", "백엔드"),
        ("TradeGuard/server/app.html", "웹 앱"),
        ("TradeGuard/pipeline/detect.py", "판정 엔진"),
        ("TradeGuard/pipeline/ucp600_kb.json", "UCP600 지식베이스"),
        ("TradeGuard/test_all.sh", "테스트"),
        ("TradeGuard/demo.sh", "원커맨드 실행"),
        ("TradeGuard/실행_가이드.md", "실행 안내"),
        ("TradeGuard/.env.example", "환경설정 템플릿")]
nfc = {unicodedata.normalize("NFC", n) for n in zipfile.ZipFile(sys.argv[1]).namelist()}
missing = [(f, l) for f, l in must
           if unicodedata.normalize("NFC", "TradeGuard-submission/" + f) not in nfc]
if missing:
    for f, l in missing:
        print(f"  ❌ 필수 파일 누락: {f} ({l})")
    sys.exit(1)
print(f"  ✅ 필수 파일 {len(must)}종 포함 확인 (README · 앱 · 판정 엔진 · 지식베이스 · 테스트 · 실행 안내)")
PYEOF
# .env.example은 값이 비어 있는 템플릿이므로 허용한다. 실제 .env만 차단.
BAD="$(echo "$LIST" | grep -E '(^|/)\.env$|(^|/)\.venv/|__pycache__/|(^|/)\.DS_Store$|\.key$|(^|/)secrets\.json$' || true)"
[ -z "$BAD" ] || { echo "$BAD" | sed 's/^/       /'; fail "제외돼야 할 파일이 포함됨"; }
ok ".env · .venv · __pycache__ · .DS_Store · *.key 없음"

echo "$LIST" | grep -q '\.env\.example$' \
  && ok ".env.example 포함됨 (키 설정 방법 안내)" \
  || echo "  ⚠️  .env.example이 없습니다 — 환경 설정 안내가 빠집니다"

echo "══ 4/5 압축 해제 후 시크릿 패턴 스캔 ══"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
# unzip 대신 파이썬으로 푼다. macOS의 unzip은 한글 경로나 아카이브 잔여 바이트에
# 대해 경고와 함께 종료코드 1을 내는데, 압축은 멀쩡한데도 실패로 읽히기 때문이다.
python3 -c "import sys,zipfile;zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" \
  "$OUT" "$TMP" || fail "압축 해제 실패"
SRC="$TMP/TradeGuard-submission"
[ -d "$SRC" ] || fail "압축 해제본에 TradeGuard-submission 폴더가 없습니다"

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
# 의존 패키지가 없으면 **건너뛴다.** 여기서 실패로 처리하면
# "패키징이 잘못됐다"와 "이 셸에 패키지가 없다"를 구분할 수 없다.
# 실제로 가상환경 밖에서 돌리면 fastapi가 없어 9건이 무더기로 실패한다 —
# 제출물에는 아무 문제가 없는데도.
MISSING="$(python3 - <<'PYEOF'
import importlib.util
need = [("jsonschema", "jsonschema"), ("jinja2", "jinja2"), ("fastapi", "fastapi"),
        ("uvicorn", "uvicorn"), ("multipart", "python-multipart")]
print(" ".join(pkg for mod, pkg in need if importlib.util.find_spec(mod) is None))
PYEOF
)"
if [ "$RUN_TEST" -eq 0 ]; then
  echo "  ⏭  --no-test 지정 — 건너뜀"
elif [ -n "$MISSING" ]; then
  echo "  ⏭  건너뜀 — 현재 파이썬에 없는 패키지: $MISSING"
  echo "       가상환경을 켜고 다시 실행하면 이 단계까지 확인됩니다:"
  echo "       source TradeGuard/.venv/bin/activate && bash TradeGuard/package.sh"
else
  # ZIP을 풀어서 돌리는 경로가 실제로 동작하는지 확인한다.
  if (cd "$SRC/TradeGuard" && bash test_all.sh >"$TMP/test.log" 2>&1); then
    ok "압축 해제본에서 test_all.sh 통과 ($(grep -c '✅' "$TMP/test.log")개 항목)"
  else
    tail -25 "$TMP/test.log" | sed 's/^/       /'
    fail "압축 해제본이 테스트를 통과하지 못함 — 누락된 파일이 있을 수 있습니다"
  fi
fi

echo
echo "════════════════════════════════════════"
echo "  제출물 준비 완료"
echo "  → $OUT"
echo "  커밋: $(git rev-parse --short HEAD) · $(git log -1 --format=%s | cut -c1-50)"
echo "════════════════════════════════════════"
