FROM rust:1.85-slim-bookworm AS host-builder

WORKDIR /build/rust
RUN apt-get update && apt-get install -y --no-install-recommends \
    pkg-config libssl-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY rust/ ./
COPY frontend/openapi.json /build/frontend/openapi.json
RUN cargo build --release --locked -p lattice-host

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
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

COPY --from=host-builder /build/rust/target/release/lattice-host /usr/local/bin/lattice-host
COPY requirements.txt pyproject.toml MANIFEST.in README.md LICENSE ./
COPY latticeai/ ./latticeai/
COPY lattice_brain/ ./lattice_brain/
COPY static/ ./static/
COPY skills/ ./skills/
COPY plugins/ ./plugins/

RUN pip install --upgrade pip && pip install -r requirements.txt && pip install --no-deps . \
    && chown -R lattice:lattice /app

USER lattice
VOLUME ["/data"]
EXPOSE 4825

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:4825/health || exit 1

# Loopback-first by default (`127.0.0.1:4825`). To publish the container port directly
# (e.g. `docker run -p 4825:4825`), pass `-e LATTICEAI_REQUIRE_AUTH=true` and append
# `lattice-host --bind 0.0.0.0 --port 4825` (non-loopback binds refuse startup without auth).
CMD ["lattice-host", "--port", "4825"]
