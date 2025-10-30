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

# RUN --mount=type=cache,target=/root/.cache/uv \
#     uv sync --frozen --no-dev

RUN uv sync --frozen --no-dev

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8501

# Reset the entrypoint, don't invoke `uv`
ENTRYPOINT []

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
