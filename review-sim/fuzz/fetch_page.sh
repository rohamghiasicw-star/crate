#!/usr/bin/env bash
# The page under test: engine/crate.html and the three legal pages.
# Default: release-next at the pinned build-20 commit, hash-checked (byte-identical to the live page).
# Gate a candidate build: PAGE_REF=<commit or branch> bash fetch_page.sh (prints its hash instead of checking it).
set -euo pipefail
cd "$(dirname "$0")"
PIN=90492515738af0d5b175e21f5cbb7e814c8f12c3
WANT=cf6905fd9a4be667e9102d4985807d4dcb5d69c8652d69c8c0dbffca247e898b
REF="${PAGE_REF:-$PIN}"
git fetch -q --no-tags --depth=1 origin "$REF"
mkdir -p page/pages
git show "FETCH_HEAD:engine/crate.html" > page/crate.html
for n in privacy support terms; do git show "FETCH_HEAD:engine/pages/$n.html" > "page/pages/$n.html"; done
GOT=$(sha256sum page/crate.html | cut -d' ' -f1)
if [ "$REF" = "$PIN" ] && [ "$GOT" != "$WANT" ]; then echo "crate.html sha256 $GOT, expected $WANT"; exit 1; fi
echo "page: crate.html sha256 $GOT at $(git rev-parse FETCH_HEAD); release-next head now $(git ls-remote origin refs/heads/release-next | cut -c1-12)"
