#!/usr/bin/env python3
"""TradeGuard 멀티모달 추출 파이프라인 — 서류 이미지 → 스키마 준수 JSON

2단계 구조 (prompts/01_extraction_v1.md 구현):
  1단계 분류: 저비용 모델로 doc_type 판별
  2단계 추출: 해당 doc_type 스키마만 주입하여 필드 추출 → jsonschema 검증 → 실패 시 1회 재시도

프로바이더 무관: Claude(Anthropic) / GPT(OpenAI) 어느 키든 동작 — llm.py 참고.
  .env에 ANTHROPIC_API_KEY 또는 OPENAI_API_KEY 중 하나만 있으면 됩니다.

사용법:
  python3 extract.py <이미지1> [이미지2 ...] --out extracted.json
  (여러 이미지는 한 문서의 여러 페이지로 취급 — L/C 2~3페이지 대응)
"""
import json
import sys
from pathlib import Path

import jsonschema

from llm import classify_model, get_client, image_block, load_env, parse_json

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"
SCHEMAS = {
    "letter_of_credit": "letter_of_credit.schema.json",
    "commercial_invoice": "commercial_invoice.schema.json",
    "bill_of_lading": "bill_of_lading.schema.json",
}

SYSTEM_EXTRACT = """당신은 은행 외환사업부의 수출입 서류 심사 전문가입니다. 무역 서류 이미지에서 필드를 추출하여 JSON으로 구조화합니다.

절대 규칙:
1. 원문 보존: 모든 텍스트 값은 서류에 적힌 그대로 추출한다. 오탈자·대소문자·축약형을 절대 교정하지 않는다. 오탈자 자체가 하자 검출의 입력값이다.
2. 추측 금지: 읽을 수 없거나 없는 필드는 null로 두고 unreadable_fields에 필드명을 추가한다.
3. 날짜만 예외적으로 ISO 8601(YYYY-MM-DD)로 변환한다. 불확실하면 null + unreadable_fields.
4. 숫자·문자 금액 불일치 시 숫자를 취하고 field_confidence를 0.5 이하로 낮춘다.
5. 각 필드에 field_confidence(0.0~1.0)를 기록한다.
6. 출력은 JSON 하나만. 설명·코드펜스 금지."""


def classify(client, images):
    text = client.complete(
        system='무역 서류 분류기. JSON만 출력: {"doc_type": "letter_of_credit|commercial_invoice|bill_of_lading|unknown"}',
        user="이 서류의 종류는?", images=images, model=classify_model(),
        max_tokens=100, json_only=True)
    return parse_json(text).get("doc_type", "unknown")


def extract(client, images, doc_type):
    schema = json.loads((SCHEMA_DIR / SCHEMAS[doc_type]).read_text(encoding="utf-8"))
    base = (f"첨부 이미지는 {doc_type} 서류입니다. 아래 JSON 스키마에 따라 모든 필드를 추출하시오.\n"
            f"<schema>\n{json.dumps(schema, ensure_ascii=False)}\n</schema>\nJSON:")
    feedback = ""
    data = None
    for attempt in range(2):  # 최초 1회 + 검증 실패 시 재시도 1회
        text = client.complete(system=SYSTEM_EXTRACT, user=base + feedback, images=images,
                               max_tokens=4000, json_only=True)
        data = parse_json(text)
        try:
            jsonschema.validate(data, schema)
            return data, attempt
        except jsonschema.ValidationError as e:
            feedback = f"\n\n직전 출력이 스키마 검증에 실패했다. 오류를 고쳐 다시 출력하라: {e.message[:300]}"
    print("  [warn] 스키마 검증 2회 실패 — 원본 그대로 반환 (벤치마크 실패 케이스로 집계)", file=sys.stderr)
    return data, 2


def run(paths, out_path=None):
    client = get_client()
    if client is None:
        sys.exit("사용 가능한 LLM 키가 없습니다. .env에 ANTHROPIC_API_KEY 또는 OPENAI_API_KEY를 설정하세요.\n"
                 "진단: python3 llm.py --check")
    images = [image_block(p) for p in paths]
    print(f"[extract] 프로바이더: {client.name}")

    doc_type = classify(client, images)
    print(f"[extract] 1단계 분류: {doc_type}")
    if doc_type == "unknown":
        result = {"doc_type": "unknown", "reason": "분류 실패"}
    else:
        result, retries = extract(client, images, doc_type)
        print(f"[extract] 2단계 추출 완료 (재시도 {retries}회)")
        low = [k for k, v in (result.get("field_confidence") or {}).items() if v < 0.8]
        if low:
            print(f"[extract] 신뢰도 0.8 미만 필드: {low}")
    if out_path:
        Path(out_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[extract] 저장: {out_path}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main():
    load_env()
    args = sys.argv[1:]
    out_path = None
    if "--out" in args:
        i = args.index("--out")
        out_path = args[i + 1]
        args = args[:i] + args[i + 2:]
    paths = [Path(a) for a in args if not a.startswith("--")]
    if not paths:
        sys.exit("사용법: python3 extract.py <이미지...> [--out extracted.json]")
    run(paths, out_path)


if __name__ == "__main__":
    main()
