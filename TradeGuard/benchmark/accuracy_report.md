# TradeGuard 하자 검출 정확도 리포트

- 벤치마크: 합성 케이스 40건 (하자 20 · 정상 20, 함정 정상 4 포함)
- 판정 모드: 오프라인 휴리스틱 폴백

## 종합 지표

| 지표 | 값 |
|---|---|
| 하자 검출 정밀도 | 100.0% |
| 하자 검출 재현율 | 100.0% |
| F1 | 1.000 |
| 케이스 판정 정확도 | 100.0% |
| 등급 일치율 | 100.0% |
| 정상 케이스 오탐률 | 0.0% |

## 하자 유형별

| 유형 | TP | FP | FN | 정밀도 | 재현율 |
|---|---|---|---|---|---|
| AMOUNT_EXCEEDS_LC | 3 | 0 | 0 | 100% | 100% |
| BENEFICIARY_NAME_MISMATCH | 2 | 0 | 0 | 100% | 100% |
| BL_NO_ONBOARD_NOTATION | 2 | 0 | 0 | 100% | 100% |
| BL_SIGNATURE_DEFECT | 4 | 0 | 0 | 100% | 100% |
| CURRENCY_MISMATCH | 1 | 0 | 0 | 100% | 100% |
| GOODS_DESC_MISMATCH | 3 | 0 | 0 | 100% | 100% |
| LATE_SHIPMENT | 3 | 0 | 0 | 100% | 100% |
| LC_EXPIRED_OR_LATE_PRESENTATION | 2 | 0 | 0 | 100% | 100% |
| PORT_MISMATCH | 2 | 0 | 0 | 100% | 100% |
