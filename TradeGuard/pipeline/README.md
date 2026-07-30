# TradeGuard 파이프라인 (B 담당)

```
이미지 → [extract.py] → documents JSON → [detect.py] → discrepancy_report JSON → UI(화면3)
                                          └ ucp600_kb.json (조항 인용)
```

## 설치

```bash
cd TradeGuard
pip install -r requirements.txt
cp .env.example .env         # 키는 .env에만 — git 커밋 금지
python3 pipeline/llm.py --check       # 프로바이더 진단 (--live 추가 시 실호출 테스트)
python3 pipeline/fx_rates.py --check  # 환율 API 키 진단
```

**LLM 프로바이더는 교체 가능합니다.** `llm.py`가 Anthropic/OpenAI를 추상화하므로
`extract.py`·`detect.py`는 어느 키든 그대로 동작합니다. `.env`의 `LLM_PROVIDER`로
고정하거나 `auto`(기본)로 두면 있는 키를 자동 선택합니다.

## 사용

```bash
# 1) 추출: 서류 이미지 → 스키마 JSON (API 키 필요)
python3 extract.py ../render/sample_output/DEMO-001_lc.png --out lc.json

# 2) 하자 검출: 케이스/문서 JSON → 하자 리포트
python3 detect.py ../samples/DEMO-001.json --out report.json
#    ANTHROPIC_API_KEY 있으면 의미 비교(상품명세·명칭)에 LLM 사용,
#    없으면 토큰 휴리스틱 폴백으로 동작 (오프라인 테스트 가능)
```

`detect.py`는 입력에 `ground_truth`가 있으면 TP/FN/FP를 자동 출력한다 — 벤치마크 40건 평가의 최소 단위.

## 설계 결정

- **역할 분리 — 판정은 코드, 이해는 LLM.**
  - **LLM이 필요한 곳**: 서류 *이미지*에서 비정형 필드를 구조화 추출 (`extract.py`). 룰베이스로 불가능한 영역이며 여기가 AI 필연성의 근거다.
  - **LLM을 쓰면 안 되는 곳**: 하자 *판정* (`detect.py`). 실측으로 확인된 실패 사례:
    - 상품명세: 송장이 수량·조건을 생략한 정상 케이스를 6/6 전부 하자로 과탐 (UCP600 18(c)의 '상응'을 '동일'로 오해)
    - 회사명: `CO., LTD.` vs `COMPANY LTD`를 "같은 법인"으로 보아 실제 하자 2건을 놓침 (은행 실무는 신용장 기재 명의 그대로를 요구)
  - 결론: **판정은 100% 결정적 규칙**, LLM은 설명 문장 생성에만 선택적으로 사용(`TG_EXPLAIN_LLM=1`, 기본 OFF).
  - 부수 효과: 판정이 재현 가능해지고, 평가·테스트 비용이 0이 된다.
- **조항 인용**: 모든 하자에 `ucp600_kb.json`의 조항 요지를 부착. ⚠️ 현재 요지는 요약본 — D1 도메인 스프린트에서 공식 국문 번역과 대조 후 확정할 것.
- **등급 산식**: 100 − (high 25 · medium 10 · low 3). A=무하자 / B=경미 / C=지급거절 가능 / D=지급거절 확실. ground_truth의 score와 엔진 score는 산식이 달라도 무방 — 평가는 하자 유형 집합과 등급으로 한다.

## 남은 작업 (D3~D4)

- [ ] 실제 API 키로 extract.py 스모크 테스트 (렌더링 PNG 5건)
- [x] 벤치마크 40건 일괄 평가 스크립트 (A의 evaluate.py와 연결)
- [x] PNG 엔드투엔드 평가 및 필드 단위 추출 정확도 측정기
- [ ] ucp600_kb.json 인용문 공식 원문 대조
- [ ] detect.py에 presentation_date 인자 추가 여부 결정 (현재는 오늘 날짜 기준 경고)

## PNG 엔드투엔드 평가

`benchmark/evaluate.py`의 JSON 평가는 검출기 상한선이다. 발표용 실제 수치는
PNG에서 다시 추출하는 아래 명령의 `field_accuracy`와 `defect_f1`을 사용한다.

```bash
python3 benchmark/evaluate_e2e.py \
  --cases benchmark/cases \
  --rendered-dir benchmark/cases/rendered \
  --limit 5 \
  --out benchmark/metrics-e2e-smoke.json
```

`--limit`을 제거하면 전체 케이스를 평가한다. 실패한 API 호출이나 누락된 PNG는
`failures`에 기록하며 성공 케이스의 지표와 섞지 않는다.
