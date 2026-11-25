# syntax=docker/dockerfile:1.7

FROM node:22-slim@sha256:be979e5523c76fa4d5ca4ebd3099078f220aa47ec39c48ad5b5df9314debd6eb AS frontend-builder

WORKDIR /build
COPY package.json package-lock.json vite.config.mts postcss.config.cjs tsconfig.json ./
COPY frontend/ ./frontend/
COPY scripts/build_frontend_assets.mjs ./scripts/build_frontend_assets.mjs
COPY static/ ./static/
RUN npm ci --no-audit --no-fund && npm run build:assets

FROM rust:1.100-slim-bookworm@sha256:bb875b0f8ce726053e259034848c471840e96bfe591fd52556178ae9a05e6126 AS host-builder

WORKDIR /build/rust
RUN apt-get update && apt-get install -y --no-install-recommends \
    pkg-config libssl-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY rust/ ./
COPY frontend/openapi.json /build/frontend/openapi.json
RUN cargo build --release --locked -p lattice-host

FROM python:3.11-slim-bookworm@sha256:ef56ae98435cc40d8a27020b0b32a9941515acd5840f0fba34858bb34dd362d1 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LATTICEAI_PORT=4825 \
    LATTICEAI_DATA_DIR=/data \
    LATTICEAI_AGENT_ROOT=/data/agent_workspace

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 lattice \
    && useradd --uid 10001 --gid lattice --create-home --shell /usr/sbin/nologin lattice \
    && mkdir -p /data/agent_workspace \
    && chown -R lattice:lattice /data /app

COPY --from=host-builder --chown=lattice:lattice --chmod=0755 /build/rust/target/release/lattice-host /usr/local/bin/lattice-host
COPY --chown=lattice:lattice pyproject.toml MANIFEST.in README.md LICENSE ./
COPY --chown=lattice:lattice latticeai/ ./latticeai/
COPY --chown=lattice:lattice lattice_brain/ ./lattice_brain/
COPY --chown=lattice:lattice --from=frontend-builder /build/static/ ./static/
COPY --chown=lattice:lattice skills/ ./skills/
COPY --chown=lattice:lattice plugins/ ./plugins/

RUN pip install --no-cache-dir .

USER lattice
VOLUME ["/data"]
EXPOSE 4825

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:4825/health || exit 1

# Loopback-first by default (`127.0.0.1:4825`). To publish the container port directly
# (e.g. `docker run -p 4825:4825`), pass `-e LATTICEAI_REQUIRE_AUTH=true` and append
# `lattice-host --bind 0.0.0.0 --port 4825` (non-loopback binds refuse startup without auth).
CMD ["lattice-host", "--port", "4825"]
