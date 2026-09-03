FROM python:3.14.7-slim-trixie@sha256:656d12e70054d5fda18a045e2494c96701e9792dd1445f95b3d038df954f57e9

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
COPY pyproject.toml /app/
RUN python -m pip install --upgrade pip && \
    touch README.md && \
    mkdir -p src/asl_transcriber && \
    touch src/asl_transcriber/__init__.py && \
    python -m pip install . nvidia-cublas-cu12 nvidia-cudnn-cu12

# Give the CUDA libraries a stable runtime path independent of the Python minor
# version used by the pinned base image.
RUN ln -s "$(python -c 'import site; print(site.getsitepackages()[0])')/nvidia" /opt/nvidia
ENV LD_LIBRARY_PATH=/opt/nvidia/cublas/lib:/opt/nvidia/cudnn/lib

COPY --chown=root:root . /app

RUN python -m pip install --no-deps . && \
    cp src/asl_transcriber/__init__.py \
        "$(python -c 'import site; print(site.getsitepackages()[0])')/asl_transcriber/__init__.py" && \
    chmod -R a-w /app

USER appuser

EXPOSE 8080

CMD ["sh", "-c", "alembic upgrade head && exec python -m uvicorn asl_transcriber.main:app --host 0.0.0.0 --port 8080 --no-server-header"]
