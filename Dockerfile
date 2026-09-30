# Runway: personal finance, forecasting and investments.

# 1. Install the dependencies (pyproject.toml / poetry.lock) into a virtualenv with Poetry.
FROM python:3.14-slim AS deps
ENV PIP_NO_CACHE_DIR=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1
RUN pip install "poetry==2.5.1"
WORKDIR /app
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root --no-ansi

# 2. Build the web app (frontend/) into runway/static/app. It's plain files, so it's built once on the build machine
#    whatever the image's architecture.
FROM --platform=$BUILDPLATFORM node:26-slim AS web
WORKDIR /web/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# The release build uploads the web app's source maps to Sentry, so its errors show readable stack traces (see
# frontend/vite.config.ts). The token is a build secret: it's never in the image or its layers. Without it (any other
# build) nothing is uploaded and the build is the same as ever.
ARG VERSION=dev
ARG SENTRY_ORG=""
ARG SENTRY_BROWSER_PROJECT=runway-web
RUN --mount=type=secret,id=sentry_auth_token \
    SENTRY_AUTH_TOKEN="$(cat /run/secrets/sentry_auth_token 2>/dev/null || true)" \
    RUNWAY_VERSION="$VERSION" SENTRY_ORG="$SENTRY_ORG" SENTRY_BROWSER_PROJECT="$SENTRY_BROWSER_PROJECT" \
    npm run build

# 3. The image itself: Python, that virtualenv and Runway, without Poetry.
FROM python:3.14-slim

LABEL org.opencontainers.image.source="https://github.com/AnthonyPluth/Runway" \
      org.opencontainers.image.description="Runway: personal finance, forecasting and investments"

# Set by the release workflow (v1.2.3); shown in Settings.
ARG VERSION=dev

ENV RUNWAY_VERSION=$VERSION \
    PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RUNWAY_DATA=/data \
    RUNWAY_HOST=0.0.0.0 \
    RUNWAY_PORT=8765 \
    TZ=America/Chicago

# Run as an ordinary user; your database lives in /data (mount a folder or volume there).
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin runway \
 && mkdir -p /data && chown runway:runway /data

WORKDIR /app
COPY --from=deps /app/.venv ./.venv
COPY --chown=runway:runway run.py alembic.ini ./
COPY --chown=runway:runway runway ./runway
COPY --from=web --chown=runway:runway /web/runway/static/app ./runway/static/app
COPY --chown=runway:runway extension ./extension

USER runway
VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('RUNWAY_PORT', '8765'), timeout=4)" || exit 1

CMD ["python", "run.py"]
