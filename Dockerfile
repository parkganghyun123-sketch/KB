# TradeGuard — Hugging Face Spaces 배포용
#
# Spaces는 컨테이너를 **7860 포트**로 열고, 쓰기 가능한 경로가 제한된다.
# 그래서 비루트 사용자(uid 1000)로 실행하고 캐시·감사 로그를 홈 아래에 둔다.
#
# 로컬에서 같은 이미지를 확인하려면:
#   docker build -t tradeguard .
#   docker run -p 7860:7860 -e TG_UPLOAD_CODE=원하는코드 -e OPENAI_API_KEY=... tradeguard
FROM python:3.11-slim

# Playwright(chromium)는 서류 이미지를 **생성**할 때만 쓴다.
# 배포본은 이미 만들어진 이미지를 쓰므로 설치하지 않는다 — 이미지 용량 400MB 이상을 아낀다.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/app \
    PORT=7860

RUN useradd -m -u 1000 app
WORKDIR /app

COPY TradeGuard/requirements.txt ./requirements.txt
# playwright는 배포에 불필요하므로 제외하고 설치한다.
RUN grep -v '^playwright' requirements.txt > requirements.deploy.txt \
    && pip install --no-cache-dir -r requirements.deploy.txt

COPY --chown=app:app README.md /app/README.md
COPY --chown=app:app TradeGuard /app/TradeGuard

# 재심사 감사 이력이 쓰이는 경로. 컨테이너 재시작 시 사라지지만
# 데모 목적에는 충분하고, 서류 내용은 애초에 기록하지 않는다.
RUN mkdir -p /app/TradeGuard/audit && chown -R app:app /app
USER app

EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7860/api/health',timeout=4).status==200 else 1)"

CMD ["python3", "-m", "uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "7860", "--app-dir", "/app/TradeGuard"]
