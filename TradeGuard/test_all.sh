#!/usr/bin/env bash
# TradeGuard 통합 테스트 — API 비용 0원
#
# 3인 작업(A 벤치마크 · B 파이프라인 · C UI)이 서로 맞물려 도는지 확인한다.
# LLM을 호출하지 않으므로 몇 번을 돌려도 무료다.
#
#   bash test_all.sh
set -u
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

PASS=0; FAIL=0
ok()  { echo "  ✅ $1"; PASS=$((PASS+1)); }
ng()  { echo "  ❌ $1"; FAIL=$((FAIL+1)); }
run() { if eval "$2" >/tmp/tg_test.log 2>&1; then ok "$1"; else ng "$1"; sed 's/^/       /' /tmp/tg_test.log | tail -5; fi }

echo "══ 1. 스키마 (6종 · 팀 간 계약) ══"
run "JSON 파싱" "python3 -c \"import json,glob; [json.load(open(f)) for f in glob.glob('schemas/*.json')]\""
run "벤치마크 40건이 스키마를 만족" "python3 -c \"
import json,glob,jsonschema
bs=json.load(open('schemas/benchmark_case.schema.json'))
docs={k:json.load(open(f'schemas/{k}.schema.json')) for k in ['letter_of_credit','commercial_invoice','bill_of_lading']}
n=0
for f in glob.glob('benchmark/cases/*.json'):
    c=json.load(open(f)); jsonschema.validate(c,bs)
    for k,s in docs.items(): jsonschema.validate(c['documents'][k],s)
    n+=1
assert n==40, n\""

echo "══ 2. B — 하자 검출 엔진 ══"
run "샘플 케이스 정검출 (하자 3건)" "python3 -c \"
import sys,json; sys.path.insert(0,'pipeline')
from detect import build_report
c=json.load(open('samples/DEFECT-001.json'))
r=build_report(c['case_id'],c['documents'])
got={d['type'] for d in r['discrepancies']}
exp={d['type'] for d in c['ground_truth']['discrepancies']}
assert got==exp, f'불일치 got={got} exp={exp}'
assert r['overall_risk']['grade']=='D'\""
run "검출 출력이 discrepancy_report 스키마 준수" "python3 -c \"
import sys,json,jsonschema; sys.path.insert(0,'pipeline')
from detect import build_report
c=json.load(open('samples/DEFECT-001.json'))
jsonschema.validate(build_report(c['case_id'],c['documents']), json.load(open('schemas/discrepancy_report.schema.json')))\""

echo "══ 3. A — 벤치마크 회귀 (규칙 정합성) ══"
run "판정이 LLM 키 유무와 무관하게 동일 (결정성)" "python3 -c \"
import sys,json,os; sys.path.insert(0,'pipeline')
from detect import build_report
c=json.load(open('samples/DEFECT-001.json'))
a=[d['type'] for d in build_report(c['case_id'],c['documents'])['discrepancies']]
os.environ['TG_EXPLAIN_LLM']='0'
b=[d['type'] for d in build_report(c['case_id'],c['documents'])['discrepancies']]
assert a==b, f'{a} != {b}'\""
run "40건 평가 F1 1.000 · 오탐 0" "python3 -c \"
import sys,json,glob; sys.path.insert(0,'pipeline')
from detect import build_report, d as pd
tp=fp=fn=0
for f in sorted(glob.glob('benchmark/cases/*.json')):
    c=json.load(open(f))
    r=build_report(c['case_id'],c['documents'],pd(c.get('presentation_date')))
    got={x['type'] for x in r['discrepancies']}; exp={x['type'] for x in c['ground_truth']['discrepancies']}
    tp+=len(got&exp); fp+=len(got-exp); fn+=len(exp-got)
print(f'TP={tp} FP={fp} FN={fn}')
assert fp==0 and fn==0, f'회귀 실패 FP={fp} FN={fn}'\""
run "생성기 재현성 (시드 고정)" "python3 benchmark/generate_cases.py --out /tmp/tg_regen && python3 -c \"
import json,glob,os
for f in glob.glob('/tmp/tg_regen/*.json'):
    a=json.load(open(f)); b=json.load(open('benchmark/cases/'+os.path.basename(f)))
    assert a==b, os.path.basename(f)\""
run "독립 교차검증 — 우발 하자 0건 (A-2)" "(cd benchmark && python3 crosscheck_independent.py | grep -q '우발 하자 0건, 미검출 0건')"
# 회귀 방지: 라벨에 없고 엔진도 안 잡지만 사람 눈에는 보이는 '우발 하자'를 서류 단계에서 차단한다.
run "서류 물리 정합성 — 부가 이상치 0건 (포장·산술·적재)" "(cd benchmark && python3 crosscheck_independent.py | grep -q '이상치 없음')"
run "폐쇄 루프 — 재심사 통과율 100% · 신규 하자 0건" "(cd benchmark && python3 evaluate_closedloop.py | grep -q '재심사 통과율      100.0%') && (cd benchmark && python3 evaluate_closedloop.py | grep -q '신규 하자          0건')"
run "수정 제안이 신용장 기준값에서 도출 (LLM 미사용)" "python3 -c \"
import sys,json; sys.path.insert(0,'pipeline')
from detect import build_report
from remedy import propose_all
d=json.load(open('benchmark/cases/DEFECT-019.json'))
docs=d['documents']; r=build_report('x',docs,None)
ps=[p for p in propose_all(docs,r) if p['curable']]
assert ps, '제안 없음'
amt=[p for p in ps if p['type']=='AMOUNT_EXCEEDS_LC']
assert amt and amt[0]['after']==docs['letter_of_credit']['amount'], '제안값이 L/C 금액과 불일치'\""
run "치유 불가 하자를 치유 가능으로 표시하지 않음" "python3 -c \"
import sys,json; sys.path.insert(0,'pipeline')
from detect import build_report
from remedy import propose_all
d=json.load(open('samples/DEFECT-001.json'))
docs=d['documents']; r=build_report('x',docs,None)
ps=propose_all(docs,r)
late=[p for p in ps if p['type']=='LATE_SHIPMENT']
assert late and not late[0]['curable'], '선적기일 경과가 치유 가능으로 표시됨'\""

echo "══ 4. C — 렌더링 파이프라인 ══"
run "케이스 JSON → 서류 HTML 3종" "python3 render/render.py samples/DEFECT-001.json --out /tmp/tg_render"
run "미치환 템플릿 변수 없음" "! grep -l '{{' /tmp/tg_render/*.html"
run "하자 값이 서류에 반영됨 (AH-702 vs AH-720)" "grep -q 'AH-702' /tmp/tg_render/DEFECT-001_invoice.html && grep -q 'AH-720' /tmp/tg_render/DEFECT-001_lc.html"
run "UI 4화면 + 런처 존재" "test -f mockups/index.html -a -f mockups/screen1_upload.html -a -f mockups/screen2_live.html -a -f mockups/screen3_live.html -a -f mockups/screen4_fx_simulator.html"
run "구버전 목업이 데모 경로에 없음 (_archive로 격리)" "test ! -f mockups/screen2_extraction.html -a ! -f mockups/screen3_discrepancy_report.html -a -f mockups/_archive/README.md"
run "PDF 저장 버튼이 실제 동작 (window.print)" "grep -q 'window.print()' mockups/screen3_live.html && grep -q 'window.print()' mockups/screen4_fx_simulator.html"
run "인쇄용 CSS 존재 (@media print)" "grep -q '@media print' mockups/screen3_live.html && grep -q '@media print' mockups/screen4_fx_simulator.html"
run "LIVE 화면 재생성 (demo.sh 파이프라인)" "bash demo.sh --no-serve"
run "LIVE 화면이 실제 판정을 반영 (CLEAN-017=A등급 / DEFECT-001=D등급)" "grep -q '>A<' mockups/live_CLEAN-017.html && grep -q '>D<' mockups/screen3_live.html"
run "생성 HTML에 중첩 <a> 없음" "python3 -c \"
import re,glob
for f in glob.glob('mockups/*.html'):
    d=0
    for t in re.findall(r'</?a\\b', open(f).read()):
        d += 1 if t=='<a' else -1
        assert d<=1, f
    assert d==0, f\""
run "환율 스냅샷에 API 키 노출 없음" "python3 -c \"
import json,re
d=open('mockups/fx_snapshot.json').read()
assert not re.search(r'[A-Z0-9]{16,}', d), '키 노출'\""
# 회귀 방지: 과거 산식이 delay를 무시해 '하자→지연→환노출' 인과가 화면에서 성립하지 않았다.
run "환노출이 지연 일수에 반응 (√t 규칙)" "python3 -c \"
import sys; sys.path.insert(0,'pipeline'); sys.path.insert(0,'server')
from app import fx_block
import json
docs=json.load(open('samples/DEFECT-001.json'))['documents']
vals=[fx_block(docs,d)['exposure_krw'] for d in (0,3,7,14,30)]
assert vals[0]==0, f'지연 0일이면 노출 0이어야 함: {vals[0]}'
assert all(a<b for a,b in zip(vals,vals[1:])), f'지연이 늘면 노출도 늘어야 함: {vals}'
import math
from fx_rates import period_sigma
assert abs(period_sigma(0.09,252)-0.09)<1e-9, '연율화 일관성 실패'\""
run "환노출 산식이 3개 구현체에서 동일 (백엔드·SPA·screen4)" "python3 -c \"
import re
srcs={'app.html':'server/app.html','screen4':'mockups/screen4_fx_simulator.html'}
for n,p in srcs.items():
    t=open(p,encoding='utf-8').read()
    assert 'TRADING_DAYS = 252' in t, n+': 연율 상수 불일치'
    assert 'Z95 = 1.645' in t, n+': 신뢰수준 상수 불일치'
    assert 'Math.sqrt(d' in t or 'Math.sqrt(days' in t, n+': √t 규칙 없음'
t=open('pipeline/fx_rates.py',encoding='utf-8').read()
assert 'TRADING_DAYS = 252' in t, 'fx_rates: 연율 상수 불일치'\""
run "KB 브랜드 자산 미사용 (오인 방지)" "python3 -c \"
import glob,re
pat = re.compile(r'(Star-b|kbfg\.com/.*\.(jpg|png|zip)|KB_SymbolMark|KB_Logotype|KB_Signature)')
bad = [f for f in glob.glob('mockups/*.html')+glob.glob('server/*.html') if pat.search(open(f).read())]
assert not bad, f'KB 브랜드 자산 참조: {bad}'\""
run "출품작 고지 + 면책 문구 상시 노출" "python3 -c \"
import glob
need = 'KB AI Challenge 출품작'
for f in glob.glob('mockups/*.html')+['server/app.html']:
    assert need in open(f).read(), f
for f in glob.glob('mockups/live_*.html')+['mockups/screen3_live.html','server/app.html']:
    s=open(f).read()
    assert '프로토타입' in s and '최종 심사 결과' in s, f'면책 누락: {f}'\""
run "접근성 — 스킵 링크·포커스 표시·색상 외 상태 표현" "python3 -c \"
s=open('server/app.html').read()
assert 'class=\\\"skip\\\"' in s, '스킵 링크 없음'
assert 'focus-visible' in s, '포커스 표시 없음'
assert '.sev.high::before' in s, '색상 외 상태 표현 없음'
assert '--sub:#5b6068' in s, '대비 상향 미적용'\""
run "반응형 — 태블릿·모바일 분기 존재" "grep -q 'max-width:1000px' server/app.html && grep -q 'max-width:640px' server/app.html"
run "화면 간 링크 유효" "python3 -c \"
import re,os,glob
for f in glob.glob('mockups/*.html'):
    for h in re.findall(r'href=\\\"([^\\\"]+\.html)\\\"', open(f).read()):
        assert os.path.exists(os.path.join('mockups',h)), f'{f} -> {h}'\""

echo "══ 5. 백엔드 API (서버 기동 → 샘플 분석 → 종료) ══"
if python3 -c "import fastapi, uvicorn" 2>/dev/null; then
  run "서버 기동 · /api/health · 샘플 분석 3종 (무료)" "python3 - <<'PYEOF'
import json, subprocess, sys, time, urllib.request
p = subprocess.Popen([sys.executable, 'server/app.py', '--port', '8899'],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(40):
        try:
            urllib.request.urlopen('http://127.0.0.1:8899/api/health', timeout=1); break
        except Exception: time.sleep(0.3)
    else: raise SystemExit('서버 기동 실패')
    h = json.load(urllib.request.urlopen('http://127.0.0.1:8899/api/health'))
    assert h['modes']['sample'], '샘플 모드 비활성'
    expect = {'CLEAN-017':'A', 'DEFECT-019':'C', 'DEFECT-001':'D'}
    for cid, want in expect.items():
        req = urllib.request.Request('http://127.0.0.1:8899/api/analyze/sample',
              data=json.dumps({'case_id':cid}).encode(),
              headers={'Content-Type':'application/json'})
        d = json.load(urllib.request.urlopen(req, timeout=20))
        got = d['report']['overall_risk']['grade']
        assert got == want, f'{cid}: {got} != {want}'
        assert d['meta']['cost_usd'] == 0.0, '샘플 모드인데 비용 발생'
        assert len(d['fx']['scenarios']) == 3, '환노출 시나리오 누락'
    assert urllib.request.urlopen('http://127.0.0.1:8899/').status == 200, '프론트 서빙 실패'
    for path in ('/mockups/', '/mockups/screen3_live.html', '/render/sample_output/DEFECT-001_lc.html'):
        assert urllib.request.urlopen('http://127.0.0.1:8899' + path).status == 200, f'정적 서빙 실패: {path}'
finally:
    p.terminate(); p.wait(timeout=10)
PYEOF"
else
  echo "  ⏭  건너뜀 (pip install fastapi \"uvicorn[standard]\" python-multipart)"
fi

echo "══ 6. 통합 — 파이프라인 임포트 & 시크릿 ══"
run "unittest 스위트 (tests/)" "python3 -m unittest discover -s tests"
run "모든 모듈 임포트 가능" "python3 -c \"
import sys; sys.path.insert(0,'pipeline')
import llm, detect, extract, fx_rates\""
run ".env가 git에 커밋되지 않음" "test -z \"\$(git ls-files | grep -x 'TradeGuard/.env')\" -a -z \"\$(git ls-files | grep -x '.env')\""
# 자기 자신이 패턴을 포함하지 않도록 조각으로 조립한다
run "소스에 API 키 하드코딩 없음" "! grep -rIl --exclude-dir=.git --exclude='test_all.sh' --exclude='.env*' \
  --include='*.py' --include='*.js' --include='*.html' --include='*.json' --include='*.j2' \
  -E \"(sk\\-[A-Za-z0-9_-]{20,}|[A-Z0-9]{20}\$|\$(echo c669d21f)[0-9a-f]{20,})\" ."

echo
echo "════════════════════════════════"
echo "  통과 $PASS · 실패 $FAIL"
[ "$FAIL" -eq 0 ] && echo "  🎉 전체 통과 — 통합 정상" || echo "  ⚠️  실패 항목을 확인하세요"
exit "$FAIL"
