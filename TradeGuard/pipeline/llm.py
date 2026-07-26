#!/usr/bin/env python3
"""LLM 프로바이더 추상화 — Anthropic(Claude) / OpenAI(GPT) 어느 쪽이든 동작

왜 필요한가: 팀원마다 보유한 API 키가 다르고, 무료 크레딧 소진 시 즉시 갈아탈 수 있어야 한다.
호출부(extract.py / detect.py)는 이 모듈만 쓰므로 프로바이더 코드를 알 필요가 없다.

.env 설정 예:
    LLM_PROVIDER=anthropic          # 또는 openai / auto(기본: 있는 키를 자동 선택)
    ANTHROPIC_API_KEY=sk-ant-...
    OPENAI_API_KEY=sk-...
    TG_MODEL=claude-sonnet-4-5      # 미지정 시 프로바이더별 기본값 사용
    TG_MODEL_CLASSIFY=claude-haiku-4-5-20251001

설치: pip install anthropic   또는   pip install openai   (쓰는 쪽만 설치하면 됨)

사용:
    from llm import get_client, image_block
    c = get_client()
    if c: text = c.complete(system="...", user="...", images=[image_block(path)], json_only=True)
    python3 llm.py --check    # 사용 가능한 프로바이더 진단
"""
import base64
import json
import os
import re
import sys
from pathlib import Path

MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".webp": "image/webp", ".gif": "image/gif"}
DEFAULTS = {
    "anthropic": {"main": "claude-sonnet-4-5", "classify": "claude-haiku-4-5-20251001"},
    "openai": {"main": "gpt-4o", "classify": "gpt-4o-mini"},
}


def load_env(path=None):
    """의존성 없이 .env를 읽어 os.environ에 반영"""
    p = Path(path or Path(__file__).resolve().parent.parent / ".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = re.split(r"\s+#", v, maxsplit=1)[0]  # 인라인 주석(" # ...") 제거
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def image_block(path):
    """프로바이더 무관 이미지 표현 — 각 클라이언트가 자기 형식으로 변환한다"""
    p = Path(path)
    return {"media_type": MEDIA[p.suffix.lower()],
            "b64": base64.b64encode(p.read_bytes()).decode()}


def parse_json(text):
    """응답에서 JSON 객체만 추출 (코드펜스·설명문 방어)"""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"응답에 JSON 없음: {text[:200]}")
    return json.loads(m.group())


class AnthropicClient:
    name = "anthropic"

    def __init__(self):
        import anthropic
        self._c = anthropic.Anthropic()

    def complete(self, system, user, images=None, model=None, max_tokens=4000, json_only=False):
        content = [{"type": "image",
                    "source": {"type": "base64", "media_type": i["media_type"], "data": i["b64"]}}
                   for i in (images or [])]
        content.append({"type": "text", "text": user})
        if json_only:
            system = (system or "") + "\n\n출력은 JSON 하나만. 설명·마크다운 코드펜스 금지."
        msg = self._c.messages.create(
            model=model or os.environ.get("TG_MODEL") or DEFAULTS["anthropic"]["main"],
            max_tokens=max_tokens, system=system or "",
            messages=[{"role": "user", "content": content}])
        return msg.content[0].text


class OpenAIClient:
    name = "openai"

    def __init__(self):
        from openai import OpenAI
        self._c = OpenAI()

    def complete(self, system, user, images=None, model=None, max_tokens=4000, json_only=False):
        content = [{"type": "image_url",
                    "image_url": {"url": f"data:{i['media_type']};base64,{i['b64']}"}}
                   for i in (images or [])]
        content.append({"type": "text", "text": user})
        messages = ([{"role": "system", "content": system}] if system else []) + \
                   [{"role": "user", "content": content}]
        kwargs = {}
        if json_only:
            kwargs["response_format"] = {"type": "json_object"}
            if "json" not in (user + (system or "")).lower():
                messages[-1]["content"][-1]["text"] += "\n\nJSON으로 출력하시오."
        r = self._c.chat.completions.create(
            model=model or os.environ.get("TG_MODEL") or DEFAULTS["openai"]["main"],
            max_tokens=max_tokens, messages=messages, **kwargs)
        return r.choices[0].message.content


def available():
    """설치·키가 모두 갖춰진 프로바이더 목록"""
    load_env()
    out = []
    for name, env, mod in (("anthropic", "ANTHROPIC_API_KEY", "anthropic"),
                           ("openai", "OPENAI_API_KEY", "openai")):
        has_key = bool(os.environ.get(env))
        try:
            __import__(mod)
            has_sdk = True
        except ImportError:
            has_sdk = False
        if has_key and has_sdk:
            out.append(name)
    return out


def get_client(provider=None):
    """사용 가능한 클라이언트 반환. 없으면 None (호출부는 오프라인 폴백으로 동작)"""
    load_env()
    want = (provider or os.environ.get("LLM_PROVIDER") or "auto").lower()
    avail = available()
    if not avail:
        return None
    if want in ("auto", ""):
        want = avail[0]
    if want not in avail:
        return None
    return AnthropicClient() if want == "anthropic" else OpenAIClient()


def classify_model():
    """분류(저비용) 단계용 모델명"""
    p = (os.environ.get("LLM_PROVIDER") or "auto").lower()
    if p in ("auto", ""):
        a = available()
        p = a[0] if a else "anthropic"
    return os.environ.get("TG_MODEL_CLASSIFY") or DEFAULTS[p]["classify"]


def check():
    load_env()
    print("=== LLM 프로바이더 진단 ===")
    for name, env, mod in (("anthropic", "ANTHROPIC_API_KEY", "anthropic"),
                           ("openai", "OPENAI_API_KEY", "openai")):
        key = os.environ.get(env)
        try:
            __import__(mod)
            sdk = "설치됨"
        except ImportError:
            sdk = f"미설치 (pip install {mod})"
        print(f"  {name:10s} 키 {'✅' if key else '⬜'}  SDK {sdk}")
    avail = available()
    c = get_client()
    print(f"\n  사용 가능: {avail or '없음'}")
    print(f"  선택됨   : {c.name if c else '없음 — detect.py는 오프라인 휴리스틱으로 동작, extract.py는 실행 불가'}")
    if c:
        print(f"  기본 모델: {os.environ.get('TG_MODEL') or DEFAULTS[c.name]['main']} "
              f"/ 분류 {classify_model()}")
        if "--live" in sys.argv:
            try:
                print("  라이브 호출 테스트 …", end=" ")
                out = c.complete(system="한 단어로만 답하시오.", user='"OK"만 출력하시오.', max_tokens=10)
                print(f"✅ 응답: {out.strip()[:20]}")
            except Exception as e:
                print(f"❌ {str(e)[:200]}")
    return 0 if c else 1


if __name__ == "__main__":
    sys.exit(check())
