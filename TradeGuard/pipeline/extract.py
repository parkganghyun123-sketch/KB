#!/usr/bin/env python3
"""TradeGuard 멀티모달 추출 파이프라인 — 서류 이미지 → 스키마 준수 JSON

2단계 구조 (prompts/01_extraction_v1.md 구현):
  1단계 분류: 저비용 모델로 doc_type 판별
  2단계 추출: 해당 doc_type 스키마만 주입하여 필드 추출 → jsonschema 검증 → 실패 시 1회 재시도

필요 환경변수: ANTHROPIC_API_KEY
사용법:
  python3 extract.py <이미지1> [이미지2 ...] --out extracted.json
  (여러 이미지는 한 문서의 여러 페이지로 취급 — L/C 2~3페이지 대응)
"""
import base64
import json
import os
import re
import sys
from pathlib import Path

import anthropic
import jsonschema

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"
SCHEMAS = {
    "letter_of_credit": "letter_of_credit.schema.json",
    "commercial_invoice": "commercial_invoice.schema.json",
    "bill_of_lading": "bill_of_lading.schema.json",
}
MODEL_CLASSIFY = os.environ.get("TG_MODEL_CLASSIFY", "claude-haiku-4-5-20251001")
MODEL_EXTRACT = os.environ.get("TG_MODEL", "claude-sonnet-4-5")
MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}

SYSTEM_EXTRACT = """당신은 은행 외환사업부의 수출입 서류 심사 전문가입니다. 무역 서류 이미지에서 필드를 추출하여 JSON으로 구조화합니다.

절대 규칙:
1. 원문 보존: 모든 텍스트 값은 서류에 적힌 그대로 추출한다. 오탈자·대소문자·축약형을 절대 교정하지 않는다. 오탈자 자체가 하자 검출의 입력값이다.
2. 추측 금지: 읽을 수 없거나 없는 필드는 null로 두고 unreadable_fields에 필드명을 추가한다.
3. 날짜만 예외적으로 ISO 8601(YYYY-MM-DD)로 변환한다. 불확실하면 null + unreadable_fields.
4. 숫자·문자 금액 불일치 시 숫자를 취하고 field_confidence를 0.5 이하로 낮춘다.
5. 각 필드에 field_confidence(0.0~1.0)를 기록한다.
6. 출력은 JSON 하나만. 설명·코드펜스 금지."""


def img_block(path: Path):
    return {"type": "image", "source": {"type": "base64", "media_type": MEDIA[path.suffix.lower()],
                                        "data": base64.b64encode(path.read_bytes()).decode()}}


def parse_json(text: str):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("JSON 없음")
    return json.loads(m.group())


def classify(client, images):
    msg = client.messages.create(
        model=MODEL_CLASSIFY, max_tokens=100,
        system='무역 서류 분류기. JSON만 출력: {"doc_type": "letter_of_credit|commercial_invoice|bill_of_lading|unknown"}',
        messages=[{"role": "user", "content": images + [{"type": "text", "text": "이 서류의 종류는?"}]}],
    )
    return parse_json(msg.content[0].text).get("doc_type", "unknown")


def extract(client, images, doc_type):
    schema = json.loads((SCHEMA_DIR / SCHEMAS[doc_type]).read_text(encoding="utf-8"))
    user = (f"첨부 이미지는 {doc_type} 서류입니다. 아래 JSON 스키마에 따라 모든 필드를 추출하시오.\n"
            f"<schema>\n{json.dumps(schema, ensure_ascii=False)}\n</schema>\nJSON:")
    feedback = ""
    for attempt in range(2):  # 최초 1회 + 검증 실패 재시도 1회
        msg = client.messages.create(
            model=MODEL_EXTRACT, max_tokens=4000, system=SYSTEM_EXTRACT,
            messages=[{"role": "user", "content": images + [{"type": "text", "text": user + feedback}]}],
        )
        data = parse_json(msg.content[0].text)
        try:
            jsonschema.validate(data, schema)
            return data, attempt
        except jsonschema.ValidationError as e:
            feedback = f"\n\n직전 출력이 스키마 검증에 실패했다. 오류를 고쳐 다시 출력하라: {e.message[:300]}"
    print(f"  [warn] 스키마 검증 2회 실패 — 원본 그대로 반환 (벤치마크 실패 케이스로 집계)", file=sys.stderr)
    return data, 2


def main():
    args = sys.argv[1:]
    out_path = None
    if "--out" in args:
        i = args.index("--out")
        out_path = Path(args[i + 1])
        args = args[:i] + args[i + 2:]
    paths = [Path(a) for a in args]
    if not paths:
        sys.exit("사용법: python3 extract.py <이미지...> [--out extracted.json]")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY 환경변수가 필요합니다.")

    client = anthropic.Anthropic()
    images = [img_block(p) for p in paths]

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
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[extract] 저장: {out_path}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
