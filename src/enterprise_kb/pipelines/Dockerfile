# Cloud Run job image for the A2 corpus freshness refresh (SPEC §2).
#
# Build from the repository root. CI may attach a temporary tag while publishing, but Terraform
# accepts only the resulting immutable regional Artifact Registry digest.
#
#   docker build -f src/enterprise_kb/pipelines/Dockerfile \
#     -t REGION-docker.pkg.dev/PROJECT/enterprise-knowledge-base/refresh-job:BUILD_ID .

FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git \
    && rm -rf /var/lib/apt/lists/* \
    && python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml README.md requirements-gcp.lock ./
COPY src/ ./src/
COPY config/ ./config/

# The committed lock is authoritative. Installing the package with --no-deps prevents an
# image build from silently resolving a different managed dependency graph.
RUN pip install -r requirements-gcp.lock && pip install --no-deps .

FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    KB_PROFILE=gcp \
    CLOUDSDK_CORE_DISABLE_PROMPTS=1

WORKDIR /app

RUN useradd --create-home --uid 1001 refresher
COPY --from=builder /opt/venv /opt/venv
COPY src/ ./src/
COPY config/ ./config/

USER refresher

ENTRYPOINT ["python", "-m", "enterprise_kb.pipelines.refresh_job"]
