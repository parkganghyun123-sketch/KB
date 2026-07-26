# TradeGuard 파이프라인 (B 담당)

```
이미지 → [extract.py] → documents JSON → [detect.py] → discrepancy_report JSON → UI(화면3)
                                          └ ucp600_kb.json (조항 인용)
```

## 설치

```bash
pip install anthropic jsonschema
export ANTHROPIC_API_KEY=sk-ant-...   # git에 커밋 금지
export ECOS_API_KEY=...               # 환노출 모듈(D5)용 — 역시 커밋 금지
```

## 사용

```bash
# 1) 추출: 서류 이미지 → 스키마 JSON (API 키 필요)
python3 extract.py ../render/out/DEFECT-001_lc.png --out lc.json

# 2) 하자 검출: 케이스/문서 JSON → 하자 리포트
python3 detect.py ../samples/DEFECT-001.json --out report.json
#    ANTHROPIC_API_KEY 있으면 의미 비교(상품명세·명칭)에 LLM 사용,
#    없으면 토큰 휴리스틱 폴백으로 동작 (오프라인 테스트 가능)
```

`detect.py`는 입력에 `ground_truth`가 있으면 TP/FN/FP를 자동 출력한다 — 벤치마크 40건 평가의 최소 단위.

## 설계 결정

- **하이브리드 판정**: 날짜·금액·통화·항구·서명은 코드로(환각 0%), 상품명세 상응·명칭 동일성만 LLM으로. 기술설명서에서 "AI 필연성" 방어 포인트 — LLM이 필요한 지점과 필요 없는 지점을 구분해 설계했다는 근거.
- **조항 인용**: 모든 하자에 `ucp600_kb.json`의 조항 요지를 부착. ⚠️ 현재 요지는 요약본 — D1 도메인 스프린트에서 공식 국문 번역과 대조 후 확정할 것.
- **등급 산식**: 100 − (high 25 · medium 10 · low 3). A=무하자 / B=경미 / C=지급거절 가능 / D=지급거절 확실. ground_truth의 score와 엔진 score는 산식이 달라도 무방 — 평가는 하자 유형 집합과 등급으로 한다.

## 남은 작업 (D3~D4)

- [ ] 실제 API 키로 extract.py 스모크 테스트 (렌더링 PNG 5건)
- [ ] 벤치마크 40건 일괄 평가 스크립트 (A의 evaluate.py와 연결)
- [ ] ucp600_kb.json 인용문 공식 원문 대조
- [ ] detect.py에 presentation_date 인자 추가 여부 결정 (현재는 오늘 날짜 기준 경고)
