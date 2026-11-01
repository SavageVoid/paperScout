# Day 3 embedder. Kept separate from the Airflow image on purpose:
#   airflow image  ~1.2 GB  -> adding torch makes it ~3.5 GB
#   this image     ~1.1 GB  -> only used by one-off `make embed`
# With 8.2 GB free on /var, that separation is the difference between working and
# a failed docker build halfway through.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/models/hf \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TOKENIZERS_PARALLELISM=false \
    PYTHONPATH=/opt/docpulse/src

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# CPU-only torch first, from the PyTorch CPU index, so pip resolves
# sentence-transformers against it instead of pulling the CUDA build (~2.5 GB).
COPY requirements-embed.txt /tmp/requirements-embed.txt
RUN python -m pip install --upgrade pip \
 && pip install --index-url https://download.pytorch.org/whl/cpu "torch==2.15.0" \
 && pip install -r /tmp/requirements-embed.txt

# Non-root, fixed uid so the mounted model cache keeps sane ownership.
RUN mkdir -p /models/hf /opt/docpulse \
 && useradd --uid 50100 --create-home --shell /bin/bash docpulse \
 && chown -R 50100:50100 /models/hf

WORKDIR /opt/docpulse
USER 50100

# Idle by default: one-off runs use `docker compose run --rm embedder ...`
CMD ["sleep", "infinity"]
