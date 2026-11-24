FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/opt/hf

WORKDIR /app

# CPU-only torch keeps the image ~1 GB instead of ~5 GB.
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu

# Dependencies first (cached layer), read straight from pyproject.toml.
COPY pyproject.toml ./
RUN python -c "import tomllib, subprocess, sys; \
deps = tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']; \
subprocess.check_call([sys.executable, '-m', 'pip', 'install', *deps, 'pytest', 'ruff'])"

# Download the embedding model and cross-encoder at build time so the container
# never needs Hugging Face at runtime.
RUN python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; \
SentenceTransformer('BAAI/bge-small-en-v1.5'); \
CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

ARG APP_UID=1000
RUN useradd --uid ${APP_UID} --create-home app \
    && mkdir -p /app/data /app/eval/results \
    && chown -R app:app /app

COPY --chown=app:app app ./app
COPY --chown=app:app eval ./eval
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app tests ./tests
COPY --chown=app:app data/urls.txt ./seed/urls.txt

ENV URLS_FILE=/app/seed/urls.txt DATA_DIR=/app/data
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=4)"
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
