#!/bin/sh
# Install the Codex CLI static binary (musl, no Node.js) into /usr/local/bin.
# Used by the root Dockerfile; version pinned by config.json deps.codexcli.
set -eu

VERSION="${1:?usage: install.sh <codex version, e.g. 0.160.0>}"
arch="$(uname -m)"
case "$arch" in
    x86_64)  cx=x86_64 ;;
    aarch64) cx=aarch64 ;;
    *) echo "unsupported arch: $arch" >&2; exit 1 ;;
esac

curl -fsSL "https://github.com/openai/codex/releases/download/rust-v${VERSION}/codex-${cx}-unknown-linux-musl.tar.gz" \
    -o /tmp/codex.tar.gz
# optional supply-chain check: pass the release tarball's sha256 as $2
if [ "${2:-}" != "" ]; then
    echo "$2  /tmp/codex.tar.gz" | sha256sum -c -
fi
tar -xzf /tmp/codex.tar.gz -C /tmp
install -m 0755 "/tmp/codex-${cx}-unknown-linux-musl" /usr/local/bin/codex
rm -f /tmp/codex.tar.gz "/tmp/codex-${cx}-unknown-linux-musl"
codex --version
