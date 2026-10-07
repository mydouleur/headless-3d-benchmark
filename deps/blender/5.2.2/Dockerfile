# Headless Blender + mcp-for-blender over Streamable HTTP.
#
# Versioned by directory: config.json deps.blender picks deps/blender/<version>/.
#   docker compose build blender        (versions come from config.json via run.py build)
#
# Blender is the official tarball from download.blender.org (5.2.2 LTS), not the
# Debian package — the LTS pin matters for reproducible judging renders.
FROM debian:trixie-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# libegl1 + mesa's software rasteriser give Blender a GL context in background
# mode, which the Workbench snapshot renderer needs (without libEGL Blender
# aborts instead of raising). xz-utils unpacks the official tarball.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv \
        libegl1 libgl1-mesa-dri libgles2 libxi6 libxrender1 libxfixes3 \
        ca-certificates curl xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Blender 5.2.2 LTS official tarball (amd64 + arm64).
ARG BLENDER_VERSION=5.2.2
ARG BLENDERMCP_VERSION=2.1.3
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in amd64) ba=x64 ;; arm64) ba=arm64 ;; *) echo "unsupported arch: $arch" >&2; exit 1 ;; esac; \
    series="$(echo "$BLENDER_VERSION" | cut -d. -f1-2)"; \
    curl -fsSL "https://download.blender.org/release/Blender${series}/blender-${BLENDER_VERSION}-linux-${ba}.tar.xz" \
        -o /tmp/blender.tar.xz; \
    tar -xJf /tmp/blender.tar.xz -C /opt; \
    mv "/opt/blender-${BLENDER_VERSION}-linux-${ba}" /opt/blender; \
    ln -s /opt/blender/blender /usr/local/bin/blender; \
    rm /tmp/blender.tar.xz; \
    blender --version

# Optional package index mirror for registry-constrained networks.
ARG PIP_INDEX_URL=
ENV PIP_INDEX_URL=${PIP_INDEX_URL}

# The bundled python needs requests for the MCP addon.
RUN /opt/blender/python/bin/python3 -m ensurepip \
    && /opt/blender/python/bin/python3 -m pip install --no-cache-dir requests

# MCP server half. The version pin lives in deps/blendermcp/<version>/ so
# config.json deps.blendermcp selects it.
COPY deps/blendermcp/${BLENDERMCP_VERSION}/requirements.txt /tmp/blendermcp-requirements.txt
RUN python3 -m venv /opt/mcp \
    && /opt/mcp/bin/pip install -r /tmp/blendermcp-requirements.txt \
    && mkdir -p /opt/blender-mcp \
    && cp /opt/mcp/lib/python3*/site-packages/blender_mcp/bundled/addon.py /opt/blender-mcp/addon.py

COPY deps/blender/${BLENDER_VERSION}/headless_server.py \
     deps/blender/${BLENDER_VERSION}/mcp_http.py \
     deps/blender/${BLENDER_VERSION}/entrypoint.sh /opt/blender-mcp/
RUN chmod +x /opt/blender-mcp/entrypoint.sh

WORKDIR /app
# Shared mounts: agent workspaces and judge renders.
VOLUME ["/app/outputs"]

ENV BLENDER_HOST=127.0.0.1 \
    BLENDER_PORT=9876 \
    BLENDER_BIND_HOST=127.0.0.1 \
    MCP_HTTP_HOST=0.0.0.0 \
    MCP_HTTP_PORT=8000 \
    BLENDER_MCP_DISABLE_TELEMETRY=1

EXPOSE 8000
ENTRYPOINT ["/opt/blender-mcp/entrypoint.sh"]
