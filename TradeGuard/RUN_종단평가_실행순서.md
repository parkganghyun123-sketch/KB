# B-1 · B-2 실행 순서 — 종단 평가로 "진짜 성능" 만들기
> 이 문서의 결과물이 기술설명서의 정량 지표가 됩니다. 최우선 작업입니다.

## 0. 준비 (1회)

```bash
cd ~/KB/TradeGuard
git checkout main
pip install openai jsonschema requests jinja2 playwright
playwright install chromium          # 서류 PNG 렌더링용, 약 200MB
python3 pipeline/llm.py --check --live   # ✅ openai / gpt-4o 확인 완료
```

## 1. 서류 이미지 렌더링 (A-3)

```bash
python3 render/render.py benchmark/cases/*.json --out benchmark/cases/rendered --png
```

40건 × 3장 = **120장 PNG** 생성. LLM 호출 없으니 **비용 0원**입니다.
몇 장 열어보고 서류처럼 보이는지 눈으로 확인하세요 (A-1 육안 검수와 겸함).

## 2. 종단 평가 — 소규모 먼저 (B-1)

```bash
cd benchmark
python3 evaluate_e2e.py --cases cases --images cases/rendered --limit 6
```

**6건(18장) ≈ $0.36.** 시작 전 예상 비용을 출력하고 3초 대기하니, 아니다 싶으면 Ctrl+C.

여기서 보는 것:
- 필드 추출 정확도가 **80% 이상**인가 → 미만이면 프롬프트부터 고칩니다
- "추출 오류가 잦은 필드 TOP 8" → 이게 프롬프트 개선의 우선순위 목록입니다
- 문서 분류가 100%인가 → 아니면 분류 프롬프트 손봐야 합니다

## 3. 프롬프트 개선 루프 (B-1)

`pipeline/extract.py`의 `SYSTEM_EXTRACT`를 고치고 2번을 다시 돌립니다.
자주 나오는 문제와 대응:

| 증상 | 대응 |
|---|---|
| 날짜가 원문 형식 그대로 나옴 | 규칙 3에 예시 추가: `"15TH MAY 2026" → "2026-05-15"` |
| 오탈자를 교정해버림 (하자가 사라짐) | 규칙 1을 프롬프트 **맨 앞**으로 이동 + 예시 강화 |
| 금액에 콤마·통화기호 포함 | "금액은 숫자만, 콤마·통화기호 제외" 명시 |
| 없는 필드를 지어냄 | 규칙 2 강조 + `unreadable_fields` 예시 제공 |

**3회 이상 반복해도 80%를 못 넘으면** 모델을 바꿔보세요 (`.env`의 `TG_MODEL`).

## 4. 전량 평가 (B-2)

```bash
python3 evaluate_e2e.py --cases cases --images cases/rendered \
        --out e2e_metrics.json --md e2e_report.md
```

**40건(120장) ≈ $2.4.** 여기서 나온 `e2e_report.md`의 표를 기술설명서에 그대로 넣습니다.

## 5. 두 지표를 어떻게 쓰는가 (중요)

| 파일 | 측정 대상 | 발표 시 표현 |
|---|---|---|
| `accuracy_report.md` | 규칙 정합성 (정답 JSON 직접 투입) | "규칙 구현 검증 — 상한 성능" |
| `e2e_report.md` | **실제 서비스 성능** (이미지부터) | "종단 성능 — 실제 수치" |

발표에서는 **종단 수치를 주로 말하고**, 규칙 검증은 "구현 무결성을 별도 검증했다"는 보조 근거로 씁니다.
자세한 대사는 `D1_회의_결정브리프.md` 안건 2 참고.

## 비용 관리

| 작업 | 장수 | gpt-4o 기준 |
|---|---|---|
| 렌더링 | 120 | **$0** |
| 스모크(--limit 6) | 18 | ~$0.36 |
| 개선 루프 3회 | 54 | ~$1.1 |
| 전량 평가 | 120 | ~$2.4 |
| **합계** | | **약 $4** |

`gpt-4o-mini`로 추출까지 돌리면 1/10 수준이지만 정확도가 떨어질 수 있습니다.
**둘 다 돌려 비교하면 그 자체가 기술설명서 소재**가 됩니다 (모델 선택에 근거가 있다는 인상).

## 문제 해결

- `이미지 없음` → 1번 렌더링을 먼저 실행하세요
- `LLM 키 없음` → `python3 pipeline/llm.py --check`
- 느림 → 정상입니다. 120장이면 10~20분 걸립니다. `--limit`로 나눠 돌리세요
- 중간에 끊김 → 이어붙이기 기능은 없습니다. `--limit`로 작게 여러 번 돌리는 게 안전합니다
