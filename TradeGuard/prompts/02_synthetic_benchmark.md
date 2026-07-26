# 합성 벤치마크 40건 생성 프롬프트 — 담당: A (데이터)

용도: D2에 정상 20건 + 하자 20건의 서류 세트(L/C + 송장 + B/L)를 생성한다. 이 40건이 ①추출 정확도 측정(D3) ②하자 검출 정밀도/재현율 측정(D4) ③기술설명서의 정량 지표(D7)에 모두 쓰인다.

파이프라인: LLM으로 구조화 JSON 생성 → HTML 템플릿에 주입 → Playwright 스크린샷으로 이미지화. (이미지를 LLM에게 직접 그리게 하지 않는다.)

---

## 하자 유형 배분표 (하자 20건)

| 유형 코드 | 건수 | UCP 근거 | 난이도 |
|---|---|---|---|
| AMOUNT_EXCEEDS_LC | 2 | 30(b) | easy |
| CURRENCY_MISMATCH | 1 | 18(a)(iii) | easy |
| GOODS_DESC_MISMATCH | 3 | 18(c) | 1 easy · 2 hard(오탈자 1~2자) |
| BENEFICIARY_NAME_MISMATCH | 2 | 14(d) | hard(CO., LTD ↔ CO LTD 수준) |
| LATE_SHIPMENT | 2 | 14조·44C | easy |
| LC_EXPIRED_OR_LATE_PRESENTATION | 2 | 14(c)·6(d) | medium |
| PORT_MISMATCH | 2 | 20(a)(iii) | medium |
| BL_SIGNATURE_DEFECT | 2 | 20(a)(i) | medium |
| BL_NO_ONBOARD_NOTATION | 2 | 20(a)(ii) | medium |
| 복합(2개 하자 동시) | 2 | — | hard |

- 정상 20건 중 4건은 "함정 정상": 하자처럼 보이지만 규칙상 정상인 케이스 (예: 금액이 L/C의 98%지만 tolerance 10% 내 / B/L 상품명세가 일반 표현이지만 UCP600 14(e)상 허용). 오탐(false positive) 측정용.
- 산업·국가 다양화: 화학, 기계부품, 식품, 섬유, 전자 × 베트남, 미국, 중국, 인도, UAE 등 조합을 케이스마다 바꾼다.
- 모든 회사명은 가상. 실존 기업명 사용 금지.

## 생성 프롬프트 (케이스 1건 단위 — 코드 루프에서 호출)

```
당신은 무역금융 교육용 모의 서류 제작 전문가입니다. 한국 중소 수출기업의
수출 거래 1건에 대한 서류 세트를 JSON으로 생성하시오.

[케이스 사양]
- case_id: {{case_id}}
- label: {{clean | defect}}
- 주입할 하자: {{defect_types 배열 — clean이면 "없음"}}
- 난이도: {{difficulty}}
- 산업/거래국: {{industry}} / {{country}}
- 거래 규모: USD 30,000 ~ 300,000 사이에서 자연스럽게 설정

[생성 규칙]
1. letter_of_credit, commercial_invoice, bill_of_lading 3종을 각각
   아래 스키마에 맞는 JSON으로 생성한다.
   <schemas>{{3개 스키마 삽입}}</schemas>
2. 하자 주입 시, 지정된 하자 외 다른 필드는 모두 완벽히 정합해야 한다.
   (의도치 않은 제2의 하자를 만들지 말 것 — 생성 후 스스로 교차 검증하시오.)
3. 난이도 hard의 명칭·명세 불일치는 1~2글자 오탈자나 축약 차이 수준으로
   미세하게 만든다. easy는 한눈에 보이는 불일치로 만든다.
4. 날짜는 2026년 5~8월 범위. 선적일·발행일·유효기일 간 순서가 상식적이어야
   한다 (하자로 지정된 경우 제외).
5. 회사명·주소·선박명은 모두 가상으로 창작한다.
6. ground_truth: 주입한 하자를 discrepancy_report 스키마 형식으로 기술한다.
   clean이면 discrepancies=[], grade="A".

[출력]
benchmark_case 스키마에 맞는 JSON 하나만 출력. 설명 금지.
```

## 생성 후 검수 절차 (A — 반드시 수행)

1. 스키마 검증: 40건 전부 jsonschema로 자동 검증.
2. 교차 검증: 하자 케이스 20건은 **생성에 쓴 것과 다른 모델**(또는 별도 프롬프트)로 "이 서류 세트에서 불일치를 찾아라"를 돌려, 의도한 하자 외의 우발 하자가 섞였는지 확인. 우발 하자 발견 시 해당 필드 수정 또는 ground_truth에 추가.
3. 육안 샘플링: 하자 5건 + 정상 5건은 사람이 직접 읽고 확인.
4. 렌더링: HTML 템플릿 3종(C가 D2 오전 제작)에 주입 → Playwright로 PNG 저장 → benchmark_case.rendered_files에 경로 기록.

## 기술설명서용 문구 (미리 합의)

"실서류는 기업 기밀로 확보가 불가하여, 은행·무역협회 공개 서식 기반의 합성 벤치마크 40건(하자 20·정상 20, 하자 유형 10종)을 구축하고 이를 정량 평가 기준으로 사용했다" — 데이터 한계를 먼저 인정하고 벤치마크 설계 자체를 성과로 제시한다.
