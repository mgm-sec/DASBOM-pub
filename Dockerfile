# python:3.12-slim-bookworm — Debian Bookworm LTS (EOL ~2028), minimal footprint
# SHA256 pinned to multi-arch index (amd64/arm64/arm); Dependabot will open PRs for updates
FROM python:3.14.7-slim-bookworm@sha256:23c59390fc717bf09f9336908199a0ae75d9c4264bf296123f94ad772fea3b52

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash curl git jq nodejs npm gnupg \
    && rm -rf /var/lib/apt/lists/*

# gh CLI — installed via GPG-signed apt repo (signature verified by keyring)
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | gpg --dearmor -o /usr/share/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list && \
    apt-get update && apt-get install -y gh && \
    rm -rf /var/lib/apt/lists/*

# syft v1.44.0 — version-pinned, SHA256-verified per architecture before extraction
RUN set -eux; \
    SYFT_VERSION=v1.44.0; \
    ARCH=$(uname -m); \
    case "$ARCH" in \
        x86_64)  SYFT_ARCH=amd64; SYFT_SHA256=0e91737aee2b5baf1d255b959630194a302335d848ff97bb07921eb6205b5f5a ;; \
        aarch64) SYFT_ARCH=arm64; SYFT_SHA256=6f6cdcdc695721d91ce756e3b5bc3e3416599c464101f5e32e9c3f33054ee6d9 ;; \
        *) echo "Unsupported architecture: $ARCH" && exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/anchore/syft/releases/download/${SYFT_VERSION}/syft_1.44.0_linux_${SYFT_ARCH}.tar.gz" \
         -o /tmp/syft.tar.gz; \
    echo "${SYFT_SHA256}  /tmp/syft.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/syft.tar.gz -C /usr/local/bin syft; \
    rm /tmp/syft.tar.gz

# Non-root user — pipeline writes to /app/output and /app/repos (volume/tmpfs at runtime)
RUN useradd -r -u 1001 -g root appuser

WORKDIR /app
COPY requirements.txt .
RUN pip install --require-hashes -r requirements.txt --no-cache-dir

COPY . .
RUN chmod +x entrypoint.sh refresh_all.sh scripts/*.sh scripts/lib/tools.sh && \
    mkdir -p output/graph output/sbom output/cache repos && \
    chown -R appuser:root /app

USER appuser

EXPOSE 8080
ENTRYPOINT ["/app/entrypoint.sh"]
