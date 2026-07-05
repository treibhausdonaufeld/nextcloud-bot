# syntax=docker/dockerfile:1

########################################
# Builder — resolve deps into a venv
########################################
FROM python:3.13-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

# Pinned uv (reproducible builds — avoid :latest drift)
COPY --from=ghcr.io/astral-sh/uv:0.11.17 /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first (cached layer, independent of app source changes)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Now add the application source and install the project itself
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

########################################
# Runtime — minimal, non-root
########################################
FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    DATABASE_URL="sqlite+aiosqlite:////data/nextcloud_bot.db"

# Runtime-only OS deps: curl for the healthcheck, locales for German formatting
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        locales \
        curl \
    && sed -i -e 's/# de_AT.UTF-8 UTF-8/de_AT.UTF-8 UTF-8/' /etc/locale.gen && \
    sed -i -e 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen && \
    locale-gen && \
    apt-get purge -y --auto-remove && \
    rm -rf /var/lib/apt/lists/*

# Unprivileged user with a fixed uid/gid (so host bind-mounts can be chowned to match)
RUN groupadd --gid 1000 app && \
    useradd --uid 1000 --gid 1000 --no-create-home --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app

# Copy the built venv and application source, owned by the unprivileged user
COPY --from=builder --chown=1000:1000 /app /app

# Writable data dir for the SQLite database and avatars — owned by the app user
RUN mkdir -p /data && chown 1000:1000 /data
VOLUME ["/data"]

USER 1000:1000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Reset the entrypoint, don't invoke `uv`
ENTRYPOINT []

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
