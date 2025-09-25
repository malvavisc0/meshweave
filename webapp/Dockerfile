# Webapp container for the public FastAPI UI that uses markdownify-crawler library
# Installs Playwright (Chromium) and required OS dependencies.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    MARKDOWNIFY_CACHE_DIR=/tmp/markdownify/cache \
    SQLITE_PATH=/app/app.db \
    PYTHONPATH=/app

WORKDIR /app

# Copy project packaging metadata and crawler library so the webapp can import it
COPY pyproject.toml README.md LICENSE ./
COPY markdownify_crawler ./markdownify_crawler

# Copy webapp source
COPY webapp ./webapp
# Copy Alembic migration config into the image so we can run commands inside the container
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic

# Install python deps:
# - Install local crawler package with renderer extras (playwright)
# - Install FastAPI UI deps (fastapi, uvicorn, jinja2, sqlalchemy)
# - Install auth deps (authlib, cryptography) for Google OIDC
RUN pip install --upgrade pip && \
    pip install --no-cache-dir ".[renderer]" && \
    pip install --no-cache-dir fastapi[standard] uvicorn[standard] jinja2 sqlalchemy psycopg2-binary python-multipart httpx authlib cryptography prometheus-client alembic openai agno redis && \
    pip install --no-cache-dir /app/webapp
# Install curl for container healthchecks and diagnostics
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Install Playwright Chromium and system deps
RUN python -m playwright install --with-deps chromium

# Create cache dir
RUN mkdir -p /tmp/markdownify/cache

EXPOSE 8080

# Run the FastAPI webapp UI
WORKDIR /app
CMD ["markdownify-webapp", "--host", "0.0.0.0", "--port", "8080"]
