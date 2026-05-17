FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV LATTICEAI_MODE=public
ENV LATTICEAI_HOST=0.0.0.0
ENV LATTICEAI_PORT=4825
ENV LATTICEAI_DATA_DIR=/data
ENV LATTICEAI_BRAIN_DIR=/data/brain
ENV LATTICEAI_ENABLE_TELEGRAM=false
ENV LATTICEAI_ALLOW_LOCAL_MODELS=false
ENV LATTICEAI_AUTOLOAD_MODELS=true
ENV LATTICEAI_PUBLIC_MODEL=openai:gpt-4o-mini

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data /data/brain agent_workspace

EXPOSE 4825

CMD ["python", "server.py"]
