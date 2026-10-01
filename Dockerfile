# 차봄 조회·검증 API (Cloud Run). 화면(web/)은 Firebase Hosting이 따로 내준다.
# Playwright가 공식 지원하는 Debian 12(bookworm) 기반으로 고정한다.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app
COPY requirements.txt .
# headless shell만 설치해 이미지를 줄인다(전체 Chromium보다 작다). 시스템 라이브러리도 함께 설치한다.
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/* /root/.cache

COPY . .

# Cloud Run이 PORT를 넘겨준다.
CMD ["sh", "-c", "exec uvicorn server:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --no-access-log"]
