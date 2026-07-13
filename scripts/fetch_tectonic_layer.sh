#!/usr/bin/env bash
# Downloads the tectonic static Linux binary into the Lambda layer dir (gitignored,
# 25MB). Run once before `terraform apply` for the résumé-PDF compile layer.
set -euo pipefail
cd "$(dirname "$0")/.."
DEST=terraform/layers/tectonic/bin; mkdir -p "$DEST"
URL=$(curl -s https://api.github.com/repos/tectonic-typesetting/tectonic/releases \
  | grep -oE "https://[^\"]*x86_64-unknown-linux-musl.tar.gz" | head -1)
echo "fetching $URL"
curl -sL "$URL" | tar xz -C "$DEST"
chmod +x "$DEST/tectonic"
echo "tectonic ready at $DEST/tectonic"

# Seed the offline package cache so Lambda cold-starts skip the network fetch.
# (Compile any .tex once locally with this tectonic, then copy its cache dir here.)
CACHE="$(ls -d "$HOME/Library/Caches/Tectonic" "$HOME/.cache/Tectonic" 2>/dev/null | head -1)"
if [ -n "$CACHE" ]; then rm -rf terraform/layers/tectonic/cache; cp -R "$CACHE" terraform/layers/tectonic/cache; echo "seeded cache from $CACHE"; fi
