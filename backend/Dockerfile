# Backend container for Railway/Render/Fly — FastAPI + Playwright (Chromium).
# Serverless (Vercel functions) can't run a browser; this runs the checkout
# executor and storefront verification headless in a real container.
#
# The Windows SAC stubs (infra/*_stub) are NOT used here: on Linux the real
# bitarray/ckzg/regex wheels install and work normally, so requirements.txt
# installs clean.

# Base image ships Chromium + all its system deps, matched to playwright 1.54.
FROM mcr.microsoft.com/playwright/python:v1.54.0-jammy

WORKDIR /app

# Install Python deps first for layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Browsers are already in the base image, but re-assert chromium in case the
# pinned playwright pulls a newer build.
RUN playwright install chromium

COPY . .

# Railway/Render inject $PORT; default 8000 for local `docker run`.
ENV PORT=8000
EXPOSE 8000

# Shell form so $PORT expands.
CMD uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}
