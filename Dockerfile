# The elusion image: the CLI and its flows, for the Prefect flow-run Jobs (phase 6).
#
# Two stages. The builder has uv and resolves the locked dependencies into /app/.venv;
# the runtime stage copies only that virtualenv and the criteria, so neither uv nor the
# source tree ships. Build for both architectures (the dev Mac is arm64, AKS is amd64):
#   docker buildx build --platform linux/arm64 -t elusion:dev --load .
#   docker buildx build --platform linux/amd64 -t elusion:dev-amd64 --load .
# Then: infra/check-image.sh elusion:dev

FROM ghcr.io/astral-sh/uv:0.10.9 AS uv

FROM python:3.12.13-slim AS builder
COPY --from=uv /uv /bin/uv
# Compile .pyc once at build time; copy files rather than hard-link (the cache is a mount);
# use the base image's Python, never download one.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12
WORKDIR /app

# Dependencies first, from the lockfile only: this layer is rebuilt when uv.lock changes,
# not on every source edit.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --no-editable

# Then the package itself, installed as a wheel (not editable), so the prompts under
# src/elusion/review/prompts/ land in site-packages and src/ is not needed at runtime.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.12.13-slim
# A fixed non-root uid, so a Kubernetes securityContext can name it. The home directory
# holds Prefect's and Hugging Face's caches.
RUN useradd --create-home --uid 10001 elusion
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
# `evaluate` and the CLI read criteria/<name>.md relative to the working directory.
COPY criteria ./criteria
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER 10001
ENTRYPOINT ["elusion"]
