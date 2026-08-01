# TradeGuard — 수출 서류 하자 진단 AI 에이전트

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![LLM](https://img.shields.io/badge/LLM-OpenAI%20%7C%20Anthropic-412991)](https://platform.openai.com/)
[![Tests](https://img.shields.io/badge/tests-48%2F48%20passing-brightgreen)](#7-테스트)
[![License](https://img.shields.io/badge/license-추가%20필요-lightgrey)](#11-라이선스license)

> **수출 서류를 사진으로 올리면, 은행이 지급을 거절할 하자를 UCP600 조항과 함께 찾아냅니다.**
> 제8회 KB AI Challenge 출품작 (2026)

---

## 🌐 방법 1 — 설치 없이 웹에서 (가장 빠름)

### **https://tradeguard-o9j6.onrender.com**

「샘플 서류로 먼저 둘러보기」는 **별도 절차 없이** 이용하실 수 있습니다.
하자 판정 · 수정 제안 · 재심사 · 창구 연결이 모두 실제 엔진으로 동작합니다.

> 첫 접속 시 서버가 깨어나며 **30초 정도 걸릴 수 있습니다.**
> 서류를 올려 AI 판독까지 보시려면 화면의 접근 코드 칸에 제출 자료에 적힌 코드를 넣어 주십시오.

---

## ⚡ 방법 2 — 터미널에서 직접 실행 (API 키 불필요 · 비용 0원)

```bash
cd TradeGuard
python3 -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python3 -m uvicorn server.app:app --port 8000
```

**http://localhost:8000** → 「샘플 서류로 먼저 둘러보기」

키가 없어도 **하자 판정 · 수정 제안 · 재심사가 모두 동작합니다.** 판독 단계만 건너뜁니다.
로컬 실행에는 **접근 코드도 사용량 제한도 없습니다.**

```bash
bash test_all.sh      # 자동 테스트 50개 항목 (무료)
```

> 📖 **단계별 안내와 정확도 재현 방법은 [`TradeGuard/실행_가이드.md`](TradeGuard/실행_가이드.md)에 있습니다.**
> 아래 4·5절은 GitHub에서 클론하는 경우의 안내입니다.

---

## 📑 목차

1. [프로젝트 개요](#1-프로젝트-개요description)
2. [주요 기능](#2-주요-기능features)
3. [기술 스택](#3-기술-스택tech-stack)
4. [설치 방법](#4-설치-방법installation)
5. [실행 방법](#5-실행-방법usage--getting-started)
6. [프로젝트 구조](#6-프로젝트-구조project-structure)
7. [테스트](#7-테스트)
8. [환경 변수 설정](#8-환경-변수-설정environment-variables)
9. [사용 예제](#9-사용-예제examples)
10. [기여 방법](#10-기여-방법contributing)
11. [라이선스](#11-라이선스license)
12. [작성자 및 연락처](#12-작성자-및-연락처author--contact)
13. [권장 개선 사항](#13-readme-품질을-높이기-위한-권장-사항)

---

## 1. 프로젝트 개요(Description)

### 무엇을 해결하나요?

수출 거래에서 **신용장(L/C)** 을 쓸 때, 서류에 사소한 실수 하나만 있어도 은행이 대금 지급을 거절할 수 있습니다.
예를 들어 신용장에는 모델번호가 `AH-720`인데 송장에 `AH-702`로 한 글자만 잘못 적어도 하자(discrepancy)가 됩니다.

- **대기업**은 전담 외환팀이 서류를 미리 검토합니다.
- **중소 수출기업**은 전담 인력이 없어, 하자를 모른 채 서류를 제시했다가 대금 회수가 지연되거나 거절당합니다.

**TradeGuard는 은행에 서류를 내기 전에 스스로 검사할 수 있게 해주는 도구입니다.**

### 어떻게 동작하나요?

```text
서류 이미지 3종          AI 판독              규칙 기반 판정            결과
┌──────────┐      ┌──────────┐      ┌──────────────┐   ┌─────────┐
│ 신용장     │      │          │      │ UCP600 규칙   │   │ 위험등급  │
│ 상업송장   │ ───▶ │ extract  │ ───▶ │   detect     │──▶│ A~D      │
│ 선하증권   │      │  (LLM)   │      │ (결정적 규칙)  │   │ 조항 인용 │
└──────────┘      └──────────┘      └──────────────┘   └─────────┘
```

### 설계 원칙 — LLM은 "읽기"만, "판정"은 코드가

개발 중 실제로 측정한 결과입니다.

| 작업 | LLM에게 맡겼을 때 | 결정적 규칙으로 바꿨을 때 |
|---|---|---|
| 상품명세 비교 | 정상 케이스 **6/6 전부 오탐** | 오탐 0건 |
| 회사명 비교 | 실제 하자 **2건 미검출** | 전부 검출 |

> LLM은 `CO., LTD.`와 `COMPANY LTD`를 "같은 회사"로 봅니다. 상식적으로는 맞지만,
> 은행 실무(UCP600 18(a)(i))는 신용장에 적힌 명의 그대로를 요구하므로 **하자**입니다.
>
> 그래서 **비정형 이미지에서 필드를 읽는 일만 LLM에 맡기고, 하자 판정은 100% 코드**로 합니다.
> 덕분에 판정 결과가 항상 동일하게 재현되고, 평가·테스트 비용이 0원입니다.

---

## 2. 주요 기능(Features)

| # | 기능 | 설명 | 상태 |
|---|---|---|---|
| 1 | **서류 자동 판독** | 이미지에서 필드를 구조화 JSON으로 추출. 오탈자도 원문 그대로 보존 | ✅ |
| 2 | **하자 검출** | UCP600 기준 교차 대조 → **조항 번호 인용 + 수정 제안** | ✅ |
| 3 | **위험 등급 판정** | A(안전) ~ D(지급거절 확실) 4단계 + 100점 만점 점수 | ✅ |
| 4 | **수정 → 재심사 폐쇄 루프** | 하자마다 신용장 기재값에서 **결정적으로 도출한 수정값** 제시 → 사용자 승인 → 재심사 → Before/After + 감사 이력. LLM 재호출 없음 | ✅ |
| 5 | **환노출 분석** | 서류에서 현금흐름 추출 → 환율 3시나리오 손익 (지연 기간 √t 환산) | ✅ |
| 6 | **KB 창구 연결** | 서류 상태에 따라 정상 매입 · 수정 후 매입 · 하자 네고/추심 전환으로 분기 안내 | ✅ |
| 7 | **합성 서류 생성** | 벤치마크 케이스 JSON → 실제 양식의 서류 이미지 자동 생성 | ✅ |
| 8 | **정확도 평가** | 규칙 검증 + 종단(이미지→판정) 평가 2종 | ✅ |
| 9 | **웹 앱** | 업로드 → 판독 → 하자 검사 → 환노출을 한 화면에서 | ✅ |

### 검출 가능한 하자 유형

| 유형 코드 | 내용 | UCP600 근거 |
|---|---|---|
| `AMOUNT_EXCEEDS_LC` | 송장 금액이 신용장 한도 초과 | 18(b) · 30(b) |
| `CURRENCY_MISMATCH` | 통화 불일치 | 18(a)(iii) |
| `GOODS_DESC_MISMATCH` | 상품명세 불일치 (모델번호 등) | 18(c) |
| `BENEFICIARY_NAME_MISMATCH` | 송장 발행인 ≠ 수익자 | 18(a)(i) |
| `APPLICANT_NAME_MISMATCH` | 송장 수신인 ≠ 개설의뢰인 | 18(a)(ii) |
| `LATE_SHIPMENT` | 선적일이 최종선적기일 초과 | 14조 · 44C |
| `LC_EXPIRED_OR_LATE_PRESENTATION` | 서류 제시기한 경과 | 14(c) · 6(d) |
| `PORT_MISMATCH` | B/L 항구가 신용장과 불일치 | 20(a)(iii) |
| `BL_SIGNATURE_DEFECT` | B/L 서명자 자격 미표시 | 20(a)(i) |
| `BL_NO_ONBOARD_NOTATION` | 본선적재 표기 없음 | 20(a)(ii) |

---

## 3. 기술 스택(Tech Stack)

| 구분 | 기술 | 용도 |
|---|---|---|
| 언어 | Python 3.10+ | 전체 |
| 웹 서버 | FastAPI · Uvicorn | REST API + 정적 서빙 |
| LLM | OpenAI GPT-4o / Anthropic Claude | 서류 이미지 판독 (교체 가능) |
| 데이터 검증 | jsonschema | 스키마 6종 계약 |
| 문서 렌더링 | Jinja2 · Playwright | 합성 서류 HTML → PNG |
| 외부 API | 한국은행 ECOS · 관세청(공공데이터포털) | 환율 |
| 프론트엔드 | Vanilla HTML/CSS/JS | 빌드 도구 없이 즉시 실행 |
| 테스트 | unittest · 자체 통합 스크립트 | 48개 항목 |

> **프레임워크를 최소화한 이유:** 짧은 개발 기간 안에서 빌드 설정·의존성 문제로
> 시간을 잃지 않기 위해 Vanilla JS를 선택했습니다.

---

## 4. 설치 방법(Installation)

### 사전 준비물

- **Python 3.10 이상** ([다운로드](https://www.python.org/downloads/))
- **Git**
- LLM API 키 1개 — [OpenAI](https://platform.openai.com/) 또는 [Anthropic](https://console.anthropic.com/)
  (없어도 **샘플 모드**로 전체 기능을 볼 수 있습니다)

### 설치 단계

```bash
# 1) 저장소 복제
git clone https://github.com/parkganghyun123-sketch/KB.git
cd KB/TradeGuard

# 2) 가상환경 생성 (권장)
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3) 의존성 설치
pip install -r requirements.txt

# 4) 서류 이미지(PNG) 생성이 필요하면 추가 설치
playwright install chromium

# 5) 환경 변수 파일 생성
cp .env.example .env
```

`.env`를 열어 키를 입력합니다. 자세한 내용은 [8번 항목](#8-환경-변수-설정environment-variables) 참고.

### 설치 확인

```bash
python3 pipeline/llm.py --check      # LLM 연결 상태
python3 pipeline/fx_rates.py --check # 환율 API 상태
bash test_all.sh                     # 전체 통합 테스트 (비용 0원)
```

---

## 5. 실행 방법(Usage / Getting Started)

### 가장 빠른 방법 — 명령 하나

```bash
bash demo.sh
```

서류 렌더링 → 하자 리포트 생성 → 판독 화면 생성 → 환율 조회 → 서버 기동까지 자동으로 처리합니다.

| 주소 | 내용 |
|---|---|
| http://localhost:8000/ | **실동작 웹 앱** (업로드 → 분석) |
| http://localhost:8000/mockups/ | 개별 화면 모음 |

### 두 가지 실행 모드

| 모드 | LLM 호출 | 비용 | 소요 | 언제 쓰나 |
|---|---|---|---|---|
| **샘플 케이스** | ✗ | **0원** | ~1초 | 발표 시연, 기능 확인 |
| **서류 업로드** | ✓ (서류 1장당 2회) | 3장 ≈ $0.06 | 10~30초 | 실제 판독 확인 |

> 샘플 모드도 **하자 판정과 환노출 계산은 실제 엔진이 수행**합니다.
> 미리 만든 화면을 보여주는 것이 아니라, 추출 단계만 건너뛰는 방식입니다.

### 개별 실행

```bash
# 서류 이미지 생성 (LLM 불필요)
python3 render/render.py samples/DEMO-001.json --out out --png

# 하자 검출 (LLM 불필요)
python3 pipeline/detect.py samples/DEMO-001.json --out report.json

# 서류 이미지에서 필드 추출 (LLM 필요)
python3 pipeline/extract.py out/DEMO-001_lc.png --out lc.json

# 정확도 평가
cd benchmark
python3 evaluate.py --cases cases                                        # 규칙 검증 (무료)
python3 evaluate_e2e.py --cases cases --images cases/rendered --limit 6   # 종단 (유료)
```

---

## 6. 프로젝트 구조(Project Structure)

```text
KB/
├── README.md                    # 이 문서
└── TradeGuard/
    ├── demo.sh                  # ★ 데모 준비 + 서버 기동 (원커맨드)
    ├── test_all.sh              # ★ 통합 테스트 48항목 (비용 0원)
    ├── requirements.txt
    ├── .env.example             # 환경 변수 템플릿
    │
    ├── schemas/                 # 팀 간 데이터 계약 (JSON Schema 6종)
    │   ├── letter_of_credit.schema.json
    │   ├── commercial_invoice.schema.json
    │   ├── bill_of_lading.schema.json
    │   ├── discrepancy_report.schema.json      # 하자 리포트 형식
    │   ├── fx_exposure.schema.json             # 환노출 형식
    │   └── benchmark_case.schema.json
    │
    ├── pipeline/                # 핵심 엔진
    │   ├── llm.py               # LLM 프로바이더 추상화 (OpenAI/Anthropic)
    │   ├── extract.py           # 이미지 → 구조화 JSON
    │   ├── detect.py            # ★ 하자 판정 (결정적 규칙)
    │   ├── ucp600_kb.json       # UCP600 조항 지식베이스
    │   └── fx_rates.py          # 한국은행 ECOS 환율
    │
    ├── benchmark/               # 정확도 검증
    │   ├── generate_cases.py    # 합성 케이스 40건 생성 (시드 고정)
    │   ├── evaluate.py          # 규칙 정합성 평가
    │   ├── evaluate_e2e.py      # 종단 평가 (이미지부터)
    │   ├── crosscheck_independent.py  # 독립 구현 교차검증
    │   └── cases/               # 케이스 40건 (하자 20 · 정상 20)
    │
    ├── templates/               # 합성 서류 Jinja2 템플릿
    │   ├── lc_mt700.html.j2     # SWIFT MT700 형식
    │   ├── commercial_invoice.html.j2
    │   ├── bill_of_lading.html.j2
    │   └── 서식_실물대조_보고서.md   # 실제 표준과 대조한 결과
    │
    ├── render/                  # 렌더링 도구
    │   ├── render.py            # 케이스 JSON → 서류 HTML/PNG
    │   ├── render_report.py     # 검출 결과 → 하자 리포트 HTML
    │   ├── render_extraction.py # 추출 결과 → 판독 화면 HTML
    │   └── fx_snapshot.py       # 환율 스냅샷
    │
    ├── server/                  # 웹 앱
    │   ├── app.py               # FastAPI 백엔드
    │   └── app.html             # 단일 페이지 프론트엔드
    │
    ├── mockups/                 # 개별 화면 (정적)
    └── tests/                   # unittest
```

---

## 7. 테스트

```bash
bash test_all.sh
```

**LLM을 호출하지 않으므로 몇 번을 실행해도 무료입니다.** 48개 항목을 검사합니다.

| 그룹 | 검사 내용 |
|---|---|
| 스키마 | JSON 유효성 · 케이스 40건 스키마 준수 |
| 판정 엔진 | 샘플 케이스 정검출 · 출력 스키마 준수 · **LLM 유무와 무관한 결정성** |
| 벤치마크 | F1 회귀 · 생성기 재현성 · 독립 교차검증 |
| 렌더링 | 서류 생성 · 템플릿 변수 치환 · UI 링크 무결성 |
| 백엔드 | 서버 기동 → 샘플 분석 3종 → 등급 일치 확인 |
| 보안 | `.env` 미커밋 · 소스에 API 키 하드코딩 없음 |

### 현재 정확도

| 지표 | 값 | 측정 방식 |
|---|---|---|
| 하자 검출 F1 | 1.000 | 합성 40건 규칙 검증 |
| 정상 케이스 오탐률 | 0% | 함정 정상 4건 포함 |
| 필드 추출 정확도 | 93.8% | 종단 평가 (GPT-4o, 40건 / 3,465 필드) |
| 문서 분류 정확도 | 100% | 종단 평가 (120장) |
| 종단 하자 검출 F1 | 0.889 | 이미지부터 시작 (정밀도 87.0% · 재현율 90.9%) |
| 종단 등급 일치율 | 87.5% | 35 / 40 |

> ⚠️ **F1 1.000은 상한 성능입니다.** 케이스 생성 규칙과 검출 규칙이 동일한 UCP600 해석을
> 공유하므로, 이는 "성능"이 아니라 **규칙 구현에 모순이 없다는 검증**으로 읽어야 합니다.
> 실제 성능은 서류 이미지부터 시작하는 **종단 평가 수치(F1 0.889)** 를 보세요.
> 두 숫자를 구분해 제시하는 것이 이 프로젝트의 원칙입니다.

> ⚠️ **재측정 대기 중**: 위 종단 수치는 2026-07-31 오전 렌더본 기준입니다. 같은 날 저녁
> 벤치마크 케이스를 재생성(컨테이너 체크디짓·적재밀도·B/L 자기모순 수정)해 서류 이미지가
> 바뀌었으므로 갱신이 필요합니다. **정답 라벨 40건은 변경되지 않았습니다.**

**이 종단 수치는 API 키 없이 재현할 수 있습니다.** LLM 추출 결과 40건을
`TradeGuard/benchmark/cases/extracted/`에 커밋해 두었습니다.

```bash
cd TradeGuard && python3 - <<'PY'
import sys, json, glob; sys.path.insert(0, 'pipeline')
from detect import build_report, d as pd
tp = fp = fn = g = 0
for f in sorted(glob.glob('benchmark/cases/*.json')):
    c = json.load(open(f))
    ex = json.load(open(f"benchmark/cases/extracted/{c['case_id']}.json"))
    r = build_report(c['case_id'], ex['documents'], pd(ex.get('presentation_date')))
    got = {x['type'] for x in r['discrepancies']}
    exp = {x['type'] for x in c['ground_truth']['discrepancies']}
    tp += len(got & exp); fp += len(got - exp); fn += len(exp - got)
    g += r['overall_risk']['grade'] == c['ground_truth']['overall_risk']['grade']
print(f"TP={tp} FP={fp} FN={fn} · 등급일치 {g}/40")   # → TP=20 FP=3 FN=2 · 등급일치 35/40
PY
```

### 한계 (숨기지 않습니다)

- **벤치마크 40건은 전량 합성 데이터**입니다. 실서류는 기업 기밀이라 확보하지 못했고,
  은행·무역협회 공개 서식을 기준으로 만들었습니다. 실서류 검증은 파일럿 단계 과제입니다.
- 폐쇄 루프 지표(재심사 통과율 100% 등)는 **정답 JSON 위에서** 측정한 값입니다.
  추출값(93.8%) 위에서의 검증은 입력 데이터를 확보한 상태이며 다음 단계입니다.
- 재심사 이력은 `TradeGuard/audit/redetect.jsonl`에 추가 전용으로 저장되지만, **조회 UI와 보존 정책이 없습니다.** 감사 이력 DB는 도입 단계 항목입니다.
- 인증·권한 기능이 없습니다. 로컬 사전점검 프로토타입 범위입니다.

---

## 8. 환경 변수 설정(Environment Variables)

`.env.example`을 `.env`로 복사한 뒤 값을 채웁니다. **`.env`는 절대 커밋하지 마세요** (`.gitignore` 등록됨).

| 변수 | 필수 | 설명 | 발급처 |
|---|---|---|---|
| `LLM_PROVIDER` | ✗ | `auto`(기본) / `openai` / `anthropic` | — |
| `OPENAI_API_KEY` | △ | GPT 사용 시 | [platform.openai.com](https://platform.openai.com/) |
| `ANTHROPIC_API_KEY` | △ | Claude 사용 시 | [console.anthropic.com](https://console.anthropic.com/) |
| `TG_MODEL` | ✗ | 추출 모델. 비우면 기본값 사용 | — |
| `TG_MODEL_CLASSIFY` | ✗ | 문서 분류용 저비용 모델 | — |
| `TG_EXPLAIN_LLM` | ✗ | `1`이면 하자 설명 문장을 LLM이 다듬음 (기본 `0`) | — |
| `ECOS_API_KEY` | ✗ | 환율 조회 (없으면 기본값 사용) | [ecos.bok.or.kr](https://ecos.bok.or.kr/api/) |
| `DATA_GO_KR_KEY` | ✗ | 관세청 관세환율 (보조) | [data.go.kr](https://www.data.go.kr/data/15101230/openapi.do) |

△ = `OPENAI_API_KEY` 또는 `ANTHROPIC_API_KEY` 중 **하나만** 있으면 됩니다. 둘 다 없으면 샘플 모드만 동작합니다.

```dotenv
# .env 예시
LLM_PROVIDER=auto
OPENAI_API_KEY=sk-여기에_실제_키
ANTHROPIC_API_KEY=
TG_EXPLAIN_LLM=0
ECOS_API_KEY=여기에_실제_키
```

> ⚠️ 공공데이터포털 키는 반드시 **디코딩(원본) 키**를 넣으세요.
> 인코딩 키(`%2F` 포함)를 넣으면 재인코딩되어 인증 오류가 납니다.

---

## 9. 사용 예제(Examples)

### 예제 1 — 하자 검출 (Python)

```python
import sys, json
sys.path.insert(0, "pipeline")
from detect import build_report

case = json.load(open("samples/DEMO-001.json"))
report = build_report(case["case_id"], case["documents"])

print(report["overall_risk"]["grade"])   # 'D'
print(report["overall_risk"]["score"])   # 40

for d in report["discrepancies"]:
    print(f"[{d['severity']}] {d['ucp_basis']['article']}")
    print(f"  {d['description_ko']}")
    print(f"  → {d['suggested_fix_ko']}")
```

출력 예시:

```text
D
40
[high] UCP600 18(c)
  송장 상품명세가 신용장 45A와 상응하지 않습니다 — 송장의 'AH-702'가 신용장 45A의 'AH-720'와 불일치
  → 송장을 재발행하여 상품명세를 신용장 45A 원문과 일치시키세요.
[high] UCP600 14조 · L/C 44C
  선적일(2026-07-18)이 신용장 최종선적기일(2026-07-15)을 3일 초과했습니다.
  → 선적기일 경과는 서류 수정으로 치유할 수 없습니다. 개설의뢰인의 하자 수락(waiver)을 요청하세요.
```

### 예제 2 — REST API

서버 실행 후 (`bash demo.sh`):

```bash
# 서버 상태 확인
curl http://localhost:8000/api/health

# 샘플 케이스 분석 (무료)
curl -X POST http://localhost:8000/api/analyze/sample \
     -H "Content-Type: application/json" \
     -d '{"case_id": "DEMO-001"}'

# 서류 이미지 업로드 분석 (LLM 호출 · 유료)
curl -X POST http://localhost:8000/api/analyze/upload \
     -F "files=@lc.png" -F "files=@invoice.png" -F "files=@bl.png"
```

**응답 구조**

```json
{
  "case_id": "DEMO-001",
  "documents": { "letter_of_credit": {}, "commercial_invoice": {}, "bill_of_lading": {} },
  "report": {
    "overall_risk": { "grade": "D", "score": 40, "summary_ko": "..." },
    "discrepancies": [
      {
        "id": "DISC-001",
        "type": "GOODS_DESC_MISMATCH",
        "severity": "high",
        "description_ko": "...",
        "evidence": [
          { "doc": "letter_of_credit", "field": "goods_description", "value": "..." }
        ],
        "ucp_basis": { "article": "UCP600 18(c)", "quote_ko": "..." },
        "suggested_fix_ko": "..."
      }
    ]
  },
  "fx": { "spot_rate": {}, "scenarios": [], "hedge_recommendation": {} },
  "meta": { "mode": "sample", "cost_usd": 0.0, "elapsed_sec": 0.1 }
}
```

### API 목록

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/api/health` | 서버 · LLM 상태 |
| `GET` | `/api/samples` | 샘플 케이스 목록 |
| `POST` | `/api/analyze/sample` | 샘플 분석 (무료) |
| `POST` | `/api/analyze/upload` | 이미지 업로드 분석 |
| `GET` | `/api/fx` | 현재 환율 |

### 예제 3 — 합성 서류 40건 생성

```bash
cd benchmark
python3 generate_cases.py --out cases --render   # 40건 JSON + 120장 PNG
python3 crosscheck_independent.py                # 독립 구현으로 라벨 검증
```

---

## 10. 기여 방법(Contributing)

현재는 **제8회 KB AI Challenge 참가 팀 내부 개발** 중입니다. 아래는 팀원용 규칙입니다.

### 개발 흐름

```bash
git checkout main && git pull
git checkout -b feature/작업명
# ... 작업 ...
bash test_all.sh          # ★ 반드시 통과시킬 것
git commit -m "feat(영역): 요약"
git push origin feature/작업명
```

### 지켜야 할 규칙

| 규칙 | 내용 |
|---|---|
| **스키마 동결** | `schemas/` 변경은 팀 합의 필요. **필드 추가는 허용**, 삭제·이름변경·타입변경은 금지 |
| **테스트 통과** | 커밋 전 `bash test_all.sh` 48/48 |
| **시크릿 금지** | API 키는 `.env`에만. 코드·문서·오류 메시지에 노출 금지 |
| **판정 로직** | 하자 판정에 LLM을 쓰지 않습니다 ([설계 원칙](#설계-원칙--llm은-읽기만-판정은-코드가) 참고) |

### 커밋 메시지 형식

```text
feat(B): 하자 검출 규칙 2종 추가
fix(C): 데모 런처 레이아웃 깨짐 수정
docs: D2 작업지시 추가
test: 백엔드 API 검증 항목 추가
```

역할 태그 — `A` 데이터/벤치마크 · `B` LLM 파이프라인 · `C` UI/통합

---

## 11. 라이선스(License)

**추가 필요**

> 대회 규정상 출품작의 지식재산권 귀속을 확인한 뒤 결정해야 합니다.
> 미정 상태에서는 라이선스 파일을 두지 않는 편이 안전합니다
> (라이선스가 없으면 기본적으로 모든 권리가 저작자에게 유보됩니다).
>
> 확정 후 `LICENSE` 파일을 추가하고 이 항목과 상단 배지를 갱신하세요.

### 서드파티 고지

- UCP600 조항 요지는 학습·검증 목적의 요약본입니다. 정확한 해석은 [국제상업회의소(ICC)](https://iccwbo.org/) 공식 간행물을 따르세요.
- 벤치마크의 회사명·선박명은 모두 **가상**이며 실존 기업과 무관합니다.

---

## 12. 작성자 및 연락처(Author / Contact)

| 항목 | 내용 |
|---|---|
| 팀 구성 | 2인 (기획 · 백엔드 · UI/통합 / LLM 파이프라인 · 평가) |
| 대표 | 강현 (부산대학교) |
| 이메일 | ks922324@pusan.ac.kr |
| 저장소 | https://github.com/parkganghyun123-sketch/KB |
| 대회 | [제8회 KB AI Challenge](https://kb-aichallenge.com/) (2026) |

> 팀원 이름·연락처: **추가 필요**

---

## 13. README 품질을 높이기 위한 권장 사항

현재 README에서 더 보완하면 좋은 항목입니다.

| # | 권장 사항 | 이유 | 우선순위 |
|---|---|---|---|
| 1 | **데모 스크린샷 / GIF 추가** | 하자 리포트 화면 한 장이 설명 열 줄보다 강력합니다 | 높음 |
| 2 | **라이선스 확정** | 현재 미정. 대회 규정 확인 후 `LICENSE` 추가 | 높음 |
| 3 | ~~종단 성능 수치 갱신~~ | ✅ 2026-07-31 완료 — 40건 전량 재측정 반영 | 완료 |
| 4 | **영문 README 병기** | 무역금융 주제 특성상 영문 수요 가능 (`README.en.md`) | 중간 |
| 5 | **CI 배지 연결** | GitHub Actions로 `test_all.sh` 자동 실행 → 배지가 실제로 동작 | 중간 |
| 6 | **아키텍처 다이어그램** | 현재는 ASCII. Mermaid나 이미지로 교체하면 가독성 향상 | 중간 |
| 7 | **트러블슈팅 섹션** | 자주 겪는 오류(포트 충돌, Playwright 미설치, 인코딩 키 문제) 정리 | 중간 |
| 8 | **CHANGELOG.md** | 버전별 변경 이력. 개발 과정을 보여주는 근거 | 낮음 |
| 9 | **Docker 지원** | `docker compose up` 한 줄 실행. 환경 문제 원천 차단 | 낮음 |
| 10 | **팀원 정보 보완** | 현재 대표만 기재 | 낮음 |

### 즉시 반영하면 좋은 것 두 가지

**1) 스크린샷** — `bash demo.sh` 실행 후 하자 리포트 화면을 캡처해 저장하고 제목 아래에 넣으세요.

```markdown
![하자 검사 리포트](docs/images/report.png)
```

**2) GitHub Actions** — `.github/workflows/test.yml`을 추가하면 상단 배지가 실제로 동작합니다.

```yaml
name: tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r TradeGuard/requirements.txt
      - run: cd TradeGuard && bash test_all.sh
```
