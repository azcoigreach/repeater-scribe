FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LD_LIBRARY_PATH=/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib

WORKDIR /app

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser

RUN apt-get update && apt-get install --yes --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Install the dependency layer before application source so ordinary code changes
# do not trigger another multi-gigabyte CUDA package download.
COPY pyproject.toml README.md /app/
RUN python -m pip install --upgrade pip && \
    mkdir -p src/asl_transcriber && \
    touch src/asl_transcriber/__init__.py && \
    python -m pip install . nvidia-cublas-cu12 nvidia-cudnn-cu12

COPY . /app

RUN python -m pip install --no-deps . && \
    cp src/asl_transcriber/__init__.py \
        /usr/local/lib/python3.12/site-packages/asl_transcriber/__init__.py

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

CMD ["python", "-m", "uvicorn", "asl_transcriber.main:app", "--host", "0.0.0.0", "--port", "8080"]
