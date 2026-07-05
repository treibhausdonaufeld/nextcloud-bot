FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# Enable bytecode compilation
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

# install german locale
RUN apt-get update && \
    apt-get install -y \
        locales \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN sed -i -e 's/# de_AT.UTF-8 UTF-8/de_AT.UTF-8 UTF-8/' /etc/locale.gen && \
    sed -i -e 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen && \
    locale-gen

COPY . /app

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN uv sync --frozen --no-dev --no-cache

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"

# SQLite database and avatars live here — mount a volume
RUN mkdir -p /data
ENV DATABASE_URL="sqlite+aiosqlite:////data/nextcloud_bot.db"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Reset the entrypoint, don't invoke `uv`
ENTRYPOINT []

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
