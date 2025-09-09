# Build a containerized API service for markdownify-crawler using FastAPI CLI (`fastapi run`)
# Includes Playwright with Chromium and required OS dependencies.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    # Where Playwright stores browsers (kept inside image layer)
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    # Default HTML cache dir for renderer (override with env at runtime if desired)
    MARKDOWNIFY_CACHE_DIR=/tmp/markdownify/cache

WORKDIR /app

# Copy packaging metadata and source
COPY pyproject.toml README.md LICENSE ./ 
COPY markdownify_crawler ./markdownify_crawler

# Install package with extras: renderer (playwright) and server (fastapi[standard], uvicorn)
RUN pip install --upgrade pip && \
    pip install --no-cache-dir ".[renderer,server]"

# Install Chromium and its OS dependencies for Playwright
# This will apt-get the necessary system libraries inside the container.
RUN python -m playwright install --with-deps chromium

# Expose FastAPI default port
EXPOSE 8000

# Optional: default env overrides can be set here
# ENV MARKDOWNIFY_IGNORE_PATHS="^/feed/,^/wp-json/"

# Run the packaged API using FastAPI CLI
# Use the file path so FastAPI can locate the ASGI app variable "app"
CMD ["fastapi", "run", "markdownify_crawler/server.py", "--host", "0.0.0.0", "--port", "8000"]
