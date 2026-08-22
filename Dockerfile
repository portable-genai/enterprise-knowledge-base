# A2 Enterprise Knowledge Base : API service image.
#
# Builds the FastAPI service with the managed-stack extra ([gcp]) installed, so the
# deployed container talks to Gemini / Model Armor / regional DLP / private AlloyDB /
# regional redacted GCS / Cloud Logging. The image is region-agnostic at build time;
# residency is enforced at runtime via config/settings.yaml (region pinned) and the deploy
# environment.

# --------------------------------------------------------------------------- #
# Builder : install dependencies into a venv we can copy into a slim runtime.
# --------------------------------------------------------------------------- #
FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Build tooling for any wheels that need compilation (e.g. pg8000 deps).
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential git \
 && rm -rf /var/lib/apt/lists/*

# Create an isolated venv.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy only what the build needs first, for better layer caching. The committed lockfile
# pins every transitive dep; install from it and add the package itself with --no-deps so
# the lock stays authoritative (matches CI, pip-audit and the container image).
COPY pyproject.toml README.md ./
COPY requirements-gcp.lock ./
COPY src ./src
COPY config ./config

# Install the managed-stack dependencies from the lockfile, then the package (no re-resolve).
RUN pip install -r requirements-gcp.lock && pip install --no-deps .

# --------------------------------------------------------------------------- #
# Runtime : slim, non-root, venv copied from builder.
# --------------------------------------------------------------------------- #
FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    KB_PROFILE=gcp \
    KB_SETTINGS=/app/config/settings.yaml \
    PORT=8082

WORKDIR /app

# Non-root runtime user.
RUN useradd --create-home --uid 10001 appuser

COPY --from=builder /opt/venv /opt/venv
COPY src ./src
COPY config ./config

USER appuser
EXPOSE 8082

# The API exposes /healthz for liveness/readiness.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8082')+'/healthz')" || exit 1

# Run the managed-readiness preflight before Uvicorn; shell form expands $PORT at container start.
CMD python -m enterprise_kb.managed_preflight && exec uvicorn enterprise_kb.api.app:app --host 0.0.0.0 --port ${PORT}
