#!/usr/bin/env bash
# The page under test: engine/crate.html and the three legal pages from release-next at the
# pinned commit (build 20, 9049251). The hash check proves it is the byte-identical file.
set -euo pipefail
cd "$(dirname "$0")"
PIN=90492515738af0d5b175e21f5cbb7e814c8f12c3
WANT=cf6905fd9a4be667e9102d4985807d4dcb5d69c8652d69c8c0dbffca247e898b
git fetch -q --no-tags --depth=1 origin "$PIN"
mkdir -p page/pages
git show "$PIN:engine/crate.html" > page/crate.html
for n in privacy support terms; do git show "$PIN:engine/pages/$n.html" > "page/pages/$n.html"; done
GOT=$(sha256sum page/crate.html | cut -d' ' -f1)
if [ "$GOT" != "$WANT" ]; then echo "crate.html sha256 $GOT, expected $WANT"; exit 1; fi
echo "page: crate.html sha256 $GOT at $PIN; release-next head now $(git ls-remote origin refs/heads/release-next | cut -c1-12)"
