FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system radar \
    && useradd --system --gid radar --create-home radar

WORKDIR /app

COPY --chown=radar:radar . /app

RUN mkdir -p /data \
    && chown radar:radar /data

USER radar

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/v1/health', timeout=3).read()" || exit 1

CMD ["python", "bootstrap.py", "serve", "--db", "/data/radar.db", "--host", "0.0.0.0", "--port", "8787"]
