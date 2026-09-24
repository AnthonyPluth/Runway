# Runway: personal finance, forecasting and investments. Standard-library Python only, so the image stays small.
FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/AnthonyPluth/Runway" \
      org.opencontainers.image.description="Runway: personal finance, forecasting and investments"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RUNWAY_DATA=/data \
    RUNWAY_HOST=0.0.0.0 \
    RUNWAY_PORT=8765 \
    TZ=America/Chicago

# The Postgres driver, used only when DATABASE_URL is set (otherwise Runway uses its built-in SQLite database).
RUN pip install --no-cache-dir "psycopg[binary]>=3.1,<4"

# Run as an ordinary user; your database lives in /data (mount a folder or volume there).
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin runway \
 && mkdir -p /data && chown runway:runway /data

WORKDIR /app
COPY --chown=runway:runway run.py ./
COPY --chown=runway:runway runway ./runway

USER runway
VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=4)" || exit 1

CMD ["python", "run.py"]
