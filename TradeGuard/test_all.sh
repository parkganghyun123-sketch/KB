#!/usr/bin/env bash
# TradeGuard 통합 테스트 — API 비용 0원
#
# 팀 작업(벤치마크 · 파이프라인 · UI)이 서로 맞물려 도는지 확인한다.
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
c=json.load(open('samples/DEMO-001.json'))
r=build_report(c['case_id'],c['documents'])
got={d['type'] for d in r['discrepancies']}
exp={d['type'] for d in c['ground_truth']['discrepancies']}
assert got==exp, f'불일치 got={got} exp={exp}'
assert r['overall_risk']['grade']=='D'\""
run "검출 출력이 discrepancy_report 스키마 준수" "python3 -c \"
import sys,json,jsonschema; sys.path.insert(0,'pipeline')
from detect import build_report
c=json.load(open('samples/DEMO-001.json'))
jsonschema.validate(build_report(c['case_id'],c['documents']), json.load(open('schemas/discrepancy_report.schema.json')))\""

echo "══ 3. A — 벤치마크 회귀 (규칙 정합성) ══"
run "판정이 LLM 키 유무와 무관하게 동일 (결정성)" "python3 -c \"
import sys,json,os; sys.path.insert(0,'pipeline')
from detect import build_report
c=json.load(open('samples/DEMO-001.json'))
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
# 회귀 방지(A-7): 컨테이너 번호가 규격상 체크디짓을 지키지 않으면 선사·포워더 경험이 있는
# 심사위원 눈에 걸린다 — 판정 결과에는 영향 없지만 "서류를 아는 팀인가"의 문제.
run "컨테이너 번호가 ISO 6346 체크디짓을 만족 (A-7)" "python3 -c \"
import sys,json,glob; sys.path.insert(0,'benchmark')
from generate_cases import _iso6346_check_digit
n=0
for f in glob.glob('benchmark/cases/*.json'):
    c=json.load(open(f))
    for cn in c['documents']['bill_of_lading']['container_numbers']:
        owner, check = cn[:10], int(cn[10])
        assert _iso6346_check_digit(owner)==check, f'{f}: {cn}'
        n+=1
assert n>0, '검사 대상 없음'\""
# 회귀 방지(A-9): shipped_on_board.indicated=False인 B/L이 'CLEAN ON BOARD'를 인쇄하면
# 서류 자체가 자기모순이다(정답 라벨은 본선적재 미표기인데 서류는 본선적재를 인쇄) —
# DEFECT-017·018에서 실제로 재현됐던 결함.
run "clean B/L도 본선적재 미표기 시 'CLEAN ON BOARD' 인쇄 안 함 (A-9)" "python3 render/render.py benchmark/cases/DEFECT-017.json benchmark/cases/DEFECT-018.json --out /tmp/tg_render_a9 >/dev/null && ! grep -l 'CLEAN ON BOARD' /tmp/tg_render_a9/DEFECT-017_bl.html /tmp/tg_render_a9/DEFECT-018_bl.html"
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
# 회귀 방지(A-10): 품목이 2개 이상인 송장의 한도초과는 첫 품목 금액으로 총액을 덮어쓰면
# 나머지 품목 금액이 사라진다 — 벤치마크 41건이 전부 단일 품목이라 현재는 드러나지 않는
# 버그였다. 다품목은 자동 제안 대상에서 제외하고 사람 판단으로 넘겨야 한다.
run "다품목 송장의 한도초과는 자동 제안 대상에서 제외 (A-10)" "python3 -c \"
import sys,json,copy; sys.path.insert(0,'pipeline')
from remedy import propose
d=json.load(open('benchmark/cases/DEFECT-019.json'))
docs=copy.deepcopy(d['documents'])
g=docs['commercial_invoice']['goods'][0]
docs['commercial_invoice']['goods'].append(copy.deepcopy(g))
disc={'type':'AMOUNT_EXCEEDS_LC','id':'DISC-001','severity':'high'}
p=propose(docs, disc)
assert p['curable'] is False, f'다품목인데 curable=True: {p}'\""
# 회귀 방지: 데모 대본이 주장하는 등급 전이가 실제로 재현되는지 못박는다.
# 초안 대본은 'D→A'였으나 실측상 D→A는 0건이다(치유 불가 하자가 섞이기 때문).
# DEFECT-019 = 완결 시나리오(제출 가능), DEMO-001 = 정직성 장면(A에 도달하지 않아야 정상).
run "데모 대본 등급 전이 실측 일치 (DEFECT-019 C→A 제출가능 · DEMO-001 D→C 차단)" "python3 -c \"
import sys,json; sys.path.insert(0,'pipeline')
from detect import build_report, d as pd
from remedy import propose_all, apply_edits
def loop(path):
    c=json.load(open(path)); docs=c['documents']; pres=pd(c.get('presentation_date'))
    b=build_report('x',docs,pres)
    fixed,_=apply_edits(docs,[p for p in propose_all(docs,b) if p['curable']])
    a=build_report('x',fixed,pres)
    return b['overall_risk'], a['overall_risk'], [d['type'] for d in a['discrepancies']]
b,a,rest=loop('benchmark/cases/DEFECT-019.json')
assert (b['grade'],b['score'])==('C',65), f'DEFECT-019 수정 전 C/65 아님: {b}'
assert (a['grade'],a['score'])==('A',100) and not rest, f'DEFECT-019 재심사 후 A/100/0건 아님: {a} {rest}'
b,a,rest=loop('samples/DEMO-001.json')
assert (b['grade'],b['score'])==('D',40), f'DEMO-001 수정 전 D/40 아님: {b}'
assert a['grade']=='C' and rest==['LATE_SHIPMENT'], f'DEMO-001은 치유 불가로 C에 머물러야 함: {a} {rest}'
\""
# 회귀 방지: 하자 진단에서 끝나면 반쪽이다. 서류 상태가 곧 어느 KB 창구로 가는지를 정한다.
#   하자 없음 → 정상 매입 / 치유 가능 → 수정 후 매입 / 치유 불가 → 하자 네고·추심 전환
# 지연이 0이면 환노출 구간 자체가 없으므로 환 상품을 권하지 않아야 한다.
run "KB 창구 연결이 서류 상태에 따라 갈림 (정상매입·수정후매입·하자네고)" "python3 -c \"
import sys,json; sys.path.insert(0,'pipeline'); sys.path.insert(0,'server')
from app import analyze
def route(path,cid):
    c=json.load(open(path)); return analyze(c['documents'],cid,c.get('presentation_date'))
r=route('benchmark/cases/CLEAN-017.json','CLEAN-017')['kb']
assert r['route']['status']=='clean', r['route']
cats={i['category_ko'] for i in r['items']}
assert '환위험' not in cats, f'지연 0인데 환 상품 권유: {cats}'
assert any(i['product_ko']=='수출환어음 매입' for i in r['items']), '정상 매입 안내 없음'
r=route('benchmark/cases/DEFECT-019.json','DEFECT-019')['kb']
assert r['route']['status']=='curable', r['route']
assert '환위험' in {i['category_ko'] for i in r['items']}, '지연 있는데 환 상품 없음'
r=route('samples/DEMO-001.json','DEMO-001')['kb']
assert r['route']['status']=='blocked', r['route']
assert '하자 네고' in r['route']['product_ko'], r['route']['product_ko']
\""
# 회귀 방지: 수정으로 하자를 없애도 **재발행에 쓴 시간은 돌아오지 않는다.**
# 지연을 새로 계산하면 0이 되지만, 그 기간의 자금 공백과 환노출은 실제로 발생했다.
# 이걸 놓치면 "서류 고치면 지연도 사라지나요?"라는 질문에 화면이 답을 못 한다.
run "재심사 후에도 이미 발생한 지연이 유지됨 (자금·환위험 카드 소멸 방지)" "python3 -c \"
import sys,json; sys.path.insert(0,'pipeline'); sys.path.insert(0,'server')
from app import analyze, delay_block, apply_edits
from detect import build_report, d as pd
c=json.load(open('benchmark/cases/DEFECT-019.json'))
docs,cid,pres=c['documents'],'DEFECT-019',c.get('presentation_date')
r1=analyze(docs,cid,pres)
inc=delay_block(build_report(cid,docs,pd(pres)))['total_business_days']
assert inc>0, '수정 전 지연이 0이면 이 테스트가 무의미하다'
fixed,_=apply_edits(docs,[x for x in r1['remedies'] if x['curable']])
r2=analyze(fixed,cid,pres,incurred_days=inc)
assert r2['report']['overall_risk']['grade']=='A', '수정 후 A등급이 아니다'
assert r2['delay']['total_business_days']==inc, f'지연이 이월되지 않음: {r2[\\\"delay\\\"]}'
assert r2['delay'].get('incurred') is True, 'incurred 표시 누락'
prods={i['product_ko'] for i in r2['kb']['items']}
for p in ('무역금융','선(현)물환','외화예금'):
    assert p in prods, f'재심사 후 {p} 카드가 사라짐 — 지연은 실제로 발생했다'
txt=json.dumps(r2['kb'],ensure_ascii=False)
assert '이미 소요' in txt, '이미 발생한 지연임을 문구로 밝히지 않음'
r3=analyze(fixed,cid,pres)
assert len(r3['kb']['items'])==1, '이월 없이 재심사하면 카드 1장이어야 한다(대조군)'
\""

# 회귀 방지: 화면에 은행 상품을 적을 때 **브랜드 서비스명·한시 프로그램명**을 쓰면 안 된다.
# 그런 이름은 종료·개편되므로(실제로 한때 안내하던 무역 플랫폼이 지금은 제공되지 않는다),
# 없는 서비스를 화면에 띄우게 되고 실무자 심사위원에게 즉시 걸린다.
# 외국환은행의 고유 업무명만 쓴다 — 이 이름들은 바뀌지 않는다.
# 환변동보험은 한국무역보험공사(K-SURE) 상품이므로 KB 상품으로 표기하면 안 된다.
run "은행 고유 업무명만 사용 (종료 위험 있는 브랜드 서비스명 차단)" "python3 -c \"
import sys,json,glob; sys.path.insert(0,'pipeline'); sys.path.insert(0,'server')
from app import analyze
OK={'수출환어음 매입','하자 네고 / 추심 전환','무역금융','선(현)물환','외화예금'}
seen=set()
for f in sorted(glob.glob('benchmark/cases/*.json'))+['samples/DEMO-001.json']:
    c=json.load(open(f))
    kb=analyze(c['documents'],c['case_id'],c.get('presentation_date'))['kb']
    seen |= {i['product_ko'] for i in kb['items']}
    seen.add(kb['route']['product_ko'])
bad=seen-OK
assert not bad, f'화이트리스트 밖 상품명: {bad}'
# 환변동보험은 한국무역보험공사(K-SURE) 상품이다. KB 상품으로 화면에 내보내면 안 된다.
# (소스 주석에는 이 단어가 설명으로 등장하므로 '실제 출력'만 검사한다)
c=json.load(open('benchmark/cases/DEFECT-019.json'))
res=analyze(c['documents'],c['case_id'],c.get('presentation_date'))
out=json.dumps({'kb':res['kb'],'hedge':res['fx']['hedge_recommendation']},ensure_ascii=False)
assert '환변동보험' not in out, 'K-SURE 상품을 KB 상품으로 표기'
assert 'KB 선물환' not in out, '실재하지 않는 상품명(KB 선물환) 사용'
# 종료·개편 이력이 있거나 한시 운영되는 브랜드명은 화면에 띄우지 않는다
for banned in ('ONE TRADE','ONETRADE','글로벌셀러','특별출연','PAYMENT USANCE'):
    assert banned not in out, f'브랜드·한시 프로그램명 사용: {banned}'
# 한도·요건 수치는 자료마다 다르고 바뀐다. 조건은 상담으로 확정된다고만 안내한다.
for n in ('억달러','만 달러','천만 달러'):
    assert n not in out, f'변동 가능한 요건 수치 기재: {n}'
\""
run "치유 불가 하자를 치유 가능으로 표시하지 않음" "python3 -c \"
import sys,json; sys.path.insert(0,'pipeline')
from detect import build_report
from remedy import propose_all
d=json.load(open('samples/DEMO-001.json'))
docs=d['documents']; r=build_report('x',docs,None)
ps=propose_all(docs,r)
late=[p for p in ps if p['type']=='LATE_SHIPMENT']
assert late and not late[0]['curable'], '선적기일 경과가 치유 가능으로 표시됨'\""

echo "══ 4. C — 렌더링 파이프라인 ══"
run "케이스 JSON → 서류 HTML 3종" "python3 render/render.py samples/DEMO-001.json --out /tmp/tg_render"
run "미치환 템플릿 변수 없음" "! grep -l '{{' /tmp/tg_render/*.html"
run "하자 값이 서류에 반영됨 (AH-702 vs AH-720)" "grep -q 'AH-702' /tmp/tg_render/DEMO-001_invoice.html && grep -q 'AH-720' /tmp/tg_render/DEMO-001_lc.html"
run "UI 4화면 + 런처 존재" "test -f mockups/index.html -a -f mockups/screen1_upload.html -a -f mockups/screen2_live.html -a -f mockups/screen3_live.html -a -f mockups/screen4_fx_simulator.html"
run "구버전 목업이 데모 경로에 없음 (_archive로 격리)" "test ! -f mockups/screen2_extraction.html -a ! -f mockups/screen3_discrepancy_report.html -a -f mockups/_archive/README.md"
run "PDF 저장 버튼이 실제 동작 (window.print)" "grep -q 'window.print()' mockups/screen3_live.html && grep -q 'window.print()' mockups/screen4_fx_simulator.html"
run "인쇄용 CSS 존재 (@media print)" "grep -q '@media print' mockups/screen3_live.html && grep -q '@media print' mockups/screen4_fx_simulator.html"
run "LIVE 화면 재생성 (demo.sh 파이프라인)" "bash demo.sh --no-serve"
run "LIVE 화면이 실제 판정을 반영 (CLEAN-017=A등급 / DEMO-001=D등급)" "grep -q '>A<' mockups/live_CLEAN-017.html && grep -q '>D<' mockups/screen3_live.html"
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
docs=json.load(open('samples/DEMO-001.json'))['documents']
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
    expect = {'CLEAN-017':'A', 'DEFECT-019':'C', 'DEMO-001':'D'}
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
    for path in ('/mockups/', '/mockups/screen3_live.html', '/render/sample_output/DEMO-001_lc.html'):
        assert urllib.request.urlopen('http://127.0.0.1:8899' + path).status == 200, f'정적 서빙 실패: {path}'
finally:
    p.terminate(); p.wait(timeout=10)
PYEOF"

  # 회귀 방지: samples/와 benchmark/cases/에 같은 case_id가 있으면 목록엔 두 장이 뜨는데
  # 클릭 결과는 하나뿐이라, 카드에 적힌 등급과 실제 판정이 어긋난다(데모 중 설명 불가).
  run "샘플 목록에 중복 case_id 없음 · 카드 등급과 실제 판정 일치" "python3 - <<'PYEOF'
import collections, json, subprocess, sys, time, urllib.request
p = subprocess.Popen([sys.executable, 'server/app.py', '--port', '8898'],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(40):
        try:
            urllib.request.urlopen('http://127.0.0.1:8898/api/health', timeout=1); break
        except Exception: time.sleep(0.3)
    else: raise SystemExit('서버 기동 실패')
    s = json.load(urllib.request.urlopen('http://127.0.0.1:8898/api/samples'))['samples']
    dup = [k for k, v in collections.Counter(x['case_id'] for x in s).items() if v > 1]
    assert not dup, f'중복 case_id: {dup}'
    # 화면에 실제로 뜨는 상위 9장은 배지 등급과 판정 등급이 반드시 같아야 한다
    for x in s[:9]:
        if not x['expected_grade']: continue
        req = urllib.request.Request('http://127.0.0.1:8898/api/analyze/sample',
              data=json.dumps({'case_id': x['case_id']}).encode(),
              headers={'Content-Type':'application/json'})
        got = json.load(urllib.request.urlopen(req, timeout=20))['report']['overall_risk']['grade']
        assert got == x['expected_grade'], f\"{x['case_id']}: 카드 {x['expected_grade']} != 판정 {got}\"
finally:
    p.terminate(); p.wait(timeout=10)
PYEOF"

  # 회귀 방지: 업로드 모드가 제시일을 받지 못하면 detect가 '오늘'로 판정한다.
  # 그러면 과거 발행 서류(시연용 벤치마크 이미지 포함)에서 제시기한 경과가 일괄로 잡혀
  # 샘플 모드에서 A등급이던 케이스가 업로드 모드에선 C등급으로 나온다(같은 서류, 다른 결과).
  run "업로드 모드가 제시일을 받아 판정에 반영 (실행일 무관 재현성)" "python3 - <<'PYEOF'
import json, subprocess, sys, time, urllib.error, urllib.request
from datetime import date, timedelta
sys.path.insert(0, 'pipeline'); sys.path.insert(0, 'server')
from app import analyze

docs = json.load(open('benchmark/cases/CLEAN-017.json', encoding='utf-8'))['documents']
# 제시일을 명시하면 오늘이 언제든 정답 등급(A)이 나와야 한다
r = analyze(docs, 'X', '2026-06-30')
assert r['report']['overall_risk']['grade'] == 'A', f\"제시일 지정 시 A여야 함: {r['report']['overall_risk']['grade']}\"
assert r['presentation_date'] == '2026-06-30', '제시일이 응답에 실리지 않음'
# 미지정 시에는 오늘 기준으로 판정된다(설계상 정상) — 화면이 그 사실을 알 수 있어야 한다
assert analyze(docs, 'X', None)['presentation_date'] is None

p = subprocess.Popen([sys.executable, 'server/app.py', '--port', '8896'],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(40):
        try:
            urllib.request.urlopen('http://127.0.0.1:8896/api/health', timeout=1); break
        except Exception: time.sleep(0.3)
    else: raise SystemExit('서버 기동 실패')
    spec = json.load(urllib.request.urlopen('http://127.0.0.1:8896/openapi.json'))
    body = spec['paths']['/api/analyze/upload']['post']['requestBody']
    props = list(body['content'].values())[0]['schema']
    props = props.get('properties') or spec['components']['schemas'][props['\$ref'].split('/')[-1]]['properties']
    assert 'presentation_date' in props, '업로드 API에 presentation_date 파라미터 없음'
finally:
    p.terminate(); p.wait(timeout=10)
PYEOF"

  # 회귀 방지: case_id를 검증 없이 경로에 이어 붙이면 '../../..'로 저장소 밖 JSON을 읽을 수 있다.
  run "케이스 ID 경로 조작 차단 (임의 파일 읽기 불가)" "python3 - <<'PYEOF'
import json, subprocess, sys, time, urllib.error, urllib.request
p = subprocess.Popen([sys.executable, 'server/app.py', '--port', '8897'],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(40):
        try:
            urllib.request.urlopen('http://127.0.0.1:8897/api/health', timeout=1); break
        except Exception: time.sleep(0.3)
    else: raise SystemExit('서버 기동 실패')
    for bad in ('../benchmark/e2e_metrics', '../../README', 'DEMO-001/../../.env', '/etc/passwd'):
        req = urllib.request.Request('http://127.0.0.1:8897/api/analyze/sample',
              data=json.dumps({'case_id': bad}).encode(),
              headers={'Content-Type':'application/json'})
        try:
            urllib.request.urlopen(req, timeout=10)
            raise SystemExit(f'경로 조작이 차단되지 않음: {bad}')
        except urllib.error.HTTPError as e:
            assert e.code in (400, 404), f'{bad}: 예상 400/404, 실제 {e.code}'
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
