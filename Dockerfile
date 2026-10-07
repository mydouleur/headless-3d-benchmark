# headless-3d-bench controller image: python 3.12.10 + uv-managed judge envs +
# Codex CLI. Linux only (on Windows use WSL2 + Docker).
#
#   python run.py build                      # docker compose build with config.json versions
#   docker compose run --rm controller       # run the controller test suite
#   docker compose run --rm controller python run.py run
FROM python:3.12.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# uv (static binary) manages the two judge environments.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Codex CLI (static musl binary; version pinned by config.json deps.codexcli).
ARG CODEX_VERSION=0.160.0
COPY deps/codexcli/${CODEX_VERSION}/install.sh /tmp/codex-install.sh
RUN sh /tmp/codex-install.sh "${CODEX_VERSION}" && rm /tmp/codex-install.sh

# Judge environments: .venv/py312 (3.12.10) and .venv/py314 (3.14.8), same
# commands envinstall.py runs on a host checkout.
COPY requirements-3.12.txt requirements-3.14.txt ./
RUN uv python install 3.12.10 3.14.8 \
    && uv venv .venv/py312 --python 3.12.10 \
    && uv venv .venv/py314 --python 3.14.8 \
    && uv pip install --python .venv/py312/bin/python -r requirements-3.12.txt \
    && uv pip install --python .venv/py314/bin/python -r requirements-3.14.txt

COPY . .

# outputs/ is bind-mounted from the host (shared with the blender container).
VOLUME ["/app/outputs"]

CMD [".venv/py312/bin/python", "-m", "pytest", "tests", "-q"]
