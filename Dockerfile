# Этап 1: зависимости ставятся в отдельное виртуальное окружение
FROM python:3.11.18-slim-bookworm AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install -r requirements.txt


# Этап 2: в образ попадают только окружение и код, без pip-кэша и временных файлов сборки
FROM python:3.11.18-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# приложение работает не от root; данные пишет только в /app/data (том из docker-compose.yml)
RUN useradd --create-home --uid 1000 pulse \
    && mkdir -p /app/data \
    && chown pulse:pulse /app/data

WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY backend backend
COPY frontend frontend

USER pulse
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)"
# за HTTPS-прокси (nginx, Caddy) схема и адрес берутся из X-Forwarded-*: иначе вход отклоняется по Origin
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
