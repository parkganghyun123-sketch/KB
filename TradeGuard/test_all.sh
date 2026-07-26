#!/usr/bin/env bash
# TradeGuard 통합 테스트 — API 비용 0원
#
# 3인 작업(A 벤치마크 · B 파이프라인 · C UI)이 서로 맞물려 도는지 확인한다.
# LLM을 호출하지 않으므로 몇 번을 돌려도 무료다.
#
#   bash test_all.sh
set -u
cd "$(dirname "$0")"
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

echo "══ 4. C — 렌더링 파이프라인 ══"
run "케이스 JSON → 서류 HTML 3종" "python3 render/render.py samples/DEFECT-001.json --out /tmp/tg_render"
run "미치환 템플릿 변수 없음" "! grep -l '{{' /tmp/tg_render/*.html"
run "하자 값이 서류에 반영됨 (AH-702 vs AH-720)" "grep -q 'AH-702' /tmp/tg_render/DEFECT-001_invoice.html && grep -q 'AH-720' /tmp/tg_render/DEFECT-001_lc.html"
run "UI 4화면 + 런처 존재" "test -f mockups/index.html -a -f mockups/screen1_upload.html -a -f mockups/screen2_extraction.html -a -f mockups/screen3_discrepancy_report.html -a -f mockups/screen4_fx_simulator.html"
run "화면 간 링크 유효" "python3 -c \"
import re,os,glob
for f in glob.glob('mockups/*.html'):
    for h in re.findall(r'href=\\\"([^\\\"]+\.html)\\\"', open(f).read()):
        assert os.path.exists(os.path.join('mockups',h)), f'{f} -> {h}'\""

echo "══ 5. 통합 — 파이프라인 임포트 & 시크릿 ══"
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
