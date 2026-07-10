FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV LATTICEAI_MODE=public
ENV LATTICEAI_HOST=0.0.0.0
ENV LATTICEAI_PORT=4825
ENV LATTICEAI_DATA_DIR=/data
ENV LATTICEAI_BRAIN_DIR=/data/brain
ENV LATTICEAI_AGENT_ROOT=/data/agent_workspace
ENV LATTICEAI_ENABLE_TELEGRAM=false
ENV LATTICEAI_ALLOW_LOCAL_MODELS=false
ENV LATTICEAI_AUTOLOAD_MODELS=true
ENV LATTICEAI_PUBLIC_MODEL=openai:gpt-4o-mini

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN pip install --no-cache-dir "."

RUN groupadd --gid 10001 lattice \
    && useradd --uid 10001 --gid lattice --create-home --shell /usr/sbin/nologin lattice \
    && mkdir -p /data/brain /data/agent_workspace \
    && chown -R lattice:lattice /data

USER lattice

EXPOSE 4825

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail --silent --show-error http://127.0.0.1:4825/health || exit 1

STOPSIGNAL SIGTERM

CMD ["python", "server.py"]
