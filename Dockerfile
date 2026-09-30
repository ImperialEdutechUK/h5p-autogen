FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt ./requirements.txt
COPY requirements.browser.txt ./requirements.browser.txt
RUN pip install --no-cache-dir -r requirements.txt -r requirements.browser.txt \
    && playwright install --with-deps chromium

COPY . .

CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${PORT:-8080}"]
