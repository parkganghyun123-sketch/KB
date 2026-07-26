# 백엔드 (C 담당 · D6 통합)

```
브라우저(app.html)
   │ POST /api/analyze/sample   ← 저장된 케이스 (LLM 미사용, 비용 0)
   │ POST /api/analyze/upload   ← 이미지 업로드 (extract.py → GPT/Claude)
   ▼
app.py ──▶ extract.py(추출) ──▶ detect.py(판정) ──▶ fx_rates.py(환율)
                                     └ ucp600_kb.json
```

## 실행

```bash
pip install -r ../requirements.txt
python3 app.py                # http://localhost:8000
python3 app.py --port 8080
# 또는 프로젝트 루트에서:  bash demo.sh
```

## 두 가지 모드

| 모드 | 입력 | LLM | 비용 | 소요 | 용도 |
|---|---|---|---|---|---|
| **샘플** | 저장된 케이스 JSON | ✗ | **0원** | ~0.1초 | **발표 시연 (권장)** |
| 업로드 | 서류 이미지 | ✓ | 3장 ≈ $0.06 | 10~30초 | 실제 판독 증명 |

샘플 모드는 **추출 단계만 건너뛰고** 하자 판정·환노출은 실제 엔진이 계산합니다.
즉 "미리 만든 화면을 보여주는 것"이 아니라 판정 로직은 진짜로 돌아갑니다.

## API

| 엔드포인트 | 설명 |
|---|---|
| `GET /api/health` | 서버 상태 · LLM 프로바이더 · 사용 가능 모드 |
| `GET /api/samples` | 샘플 케이스 목록 (등급 순 정렬, 데모 권장 3건이 앞) |
| `POST /api/analyze/sample` | `{case_id}` → 판정 + 환노출 (무료) |
| `POST /api/analyze/upload` | multipart `files` → 추출 + 판정 + 환노출 |
| `GET /api/fx` | 한국은행 ECOS 현재 환율 |

응답 형태:
```json
{ "case_id": "...", "documents": {...}, "report": {discrepancy_report 스키마},
  "fx": {fx_exposure 축약형}, "meta": {"mode","cost_usd","elapsed_sec","source"} }
```

## 설계 메모

- **LLM 없이도 서버가 뜬다.** 키가 없으면 업로드 모드만 비활성화되고 샘플 모드는 그대로 동작한다. 발표장에서 키 문제로 데모 전체가 멈추는 상황을 막기 위함.
- **판정은 항상 `detect.py`** — 모드와 무관하게 결정적 규칙으로 계산한다.
- **환율 실패 시 폴백** — ECOS 조회가 안 되면 1,385원으로 계산하고 화면에 "시연용 기본값"으로 표시한다.
- 업로드 파일은 임시 디렉터리에 저장 후 즉시 삭제한다 (서버에 잔존하지 않음).

## 남은 작업

- [ ] 업로드 모드 실측 — 렌더링 PNG 3장으로 종단 확인 (약 $0.06)
- [ ] 동시 요청 처리 (현재는 단일 사용자 데모 전제)
- [ ] 대용량 이미지 리사이즈 (현재는 원본 그대로 전송 → 토큰 비용 증가)
