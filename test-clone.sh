#!/usr/bin/env bash
#
# test-clone.sh — End-to-end test verifying that any CLI can `git clone`
# the marketplace served at /droid/v1/marketplace.git over HTTP.
#
# This mirrors what the Droid CLI does when it pulls the marketplace
# repository, and also exercises the same Smart-HTTP endpoints that the
# Claude Code plugin reaches when it fetches `marketplace.json`.
#
# Usage:
#   ./test-clone.sh                       # uses http://localhost:8081
#   MARKETPLACE_URL=http://host:port ./test-clone.sh

set -euo pipefail

BASE_URL="${MARKETPLACE_URL:-http://localhost:8081}"
REPO_URL="${BASE_URL}/droid/v1/marketplace.git"
TMP_DIR="$(mktemp -d -t droid-marketplace-clone.XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT

pass() { printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$1" >&2; exit 1; }
info() { printf '\n=== %s ===\n' "$1"; }

info "1/5  HTTP reachability"
if ! curl -fsS -o /dev/null "$BASE_URL/index.html"; then
    fail "Marketplace site is not reachable at $BASE_URL"
fi
pass "Site reachable at $BASE_URL"

info "2/5  Smart-HTTP advertisement (info/refs?service=git-upload-pack)"
HTTP_CODE="$(curl -sS -o "$TMP_DIR/refs.bin" -w '%{http_code}' \
    -H 'User-Agent: git/2.40.0' \
    "$REPO_URL/info/refs?service=git-upload-pack")"
if [[ "$HTTP_CODE" != "200" ]]; then
    fail "info/refs returned HTTP $HTTP_CODE"
fi
if ! grep -aq 'service=git-upload-pack' "$TMP_DIR/refs.bin"; then
    fail "info/refs did not advertise git-upload-pack service"
fi
pass "Smart-HTTP advertisement responded correctly"

info "3/5  git clone (the actual CLI workflow)"
CLONE_DIR="$TMP_DIR/marketplace"
if ! git clone --depth=1 "$REPO_URL" "$CLONE_DIR" 2>"$TMP_DIR/clone.log"; then
    cat "$TMP_DIR/clone.log" >&2
    fail "git clone failed against $REPO_URL"
fi
pass "git clone succeeded into $CLONE_DIR"

info "4/5  Cloned content sanity checks"
declare -a EXPECTED=(
    ".claude-plugin/marketplace.json"
    ".factory-plugin/marketplace.json"
    "plugins"
)
for path in "${EXPECTED[@]}"; do
    if [[ ! -e "$CLONE_DIR/$path" ]]; then
        fail "Expected path missing in clone: $path"
    fi
    pass "found $path"
done

# Validate the JSON files parse
for json in \
    "$CLONE_DIR/.claude-plugin/marketplace.json" \
    "$CLONE_DIR/.factory-plugin/marketplace.json"; do
    if command -v python3 >/dev/null 2>&1; then
        if ! python3 -c "import json,sys; json.load(open('$json'))"; then
            fail "Invalid JSON: $json"
        fi
        pass "valid JSON: $(basename "$(dirname "$json")")/$(basename "$json")"
    fi
done

info "5/5  Static marketplace.json endpoints (Claude Code plugin path)"
for path in /.claude-plugin/marketplace.json /.factory-plugin/marketplace.json; do
    if ! curl -fsS "$BASE_URL$path" | head -c 1 >/dev/null; then
        fail "Static endpoint not serving: $path"
    fi
    pass "served $path"
done

printf '\n\033[32mAll checks passed — any CLI can clone %s\033[0m\n' "$REPO_URL"
