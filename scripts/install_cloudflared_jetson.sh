#!/usr/bin/env bash
set -euo pipefail

ARCH="$(dpkg --print-architecture)"
case "$ARCH" in
  arm64|amd64)
    ;;
  *)
    echo "Unsupported architecture: $ARCH" >&2
    exit 1
    ;;
esac

TMP_DEB="/tmp/cloudflared-linux-${ARCH}.deb"
URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}.deb"

echo "Downloading $URL"
curl -fsSL "$URL" -o "$TMP_DEB"

echo "Installing cloudflared"
sudo dpkg -i "$TMP_DEB" || sudo apt-get -f install -y

cloudflared --version
