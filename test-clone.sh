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
    ".agents/plugins/marketplace.json"
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
    "$CLONE_DIR/.factory-plugin/marketplace.json" \
    "$CLONE_DIR/.agents/plugins/marketplace.json"; do
    if command -v python3 >/dev/null 2>&1; then
        if ! python3 -c "import json,sys; json.load(open('$json'))"; then
            fail "Invalid JSON: $json"
        fi
        pass "valid JSON: $(basename "$(dirname "$json")")/$(basename "$json")"
    fi
done

info "5/5  Static marketplace.json endpoints (Claude Code plugin path)"
for path in /.claude-plugin/marketplace.json /.factory-plugin/marketplace.json /.agents/plugins/marketplace.json; do
    if ! curl -fsS "$BASE_URL$path" | head -c 1 >/dev/null; then
        fail "Static endpoint not serving: $path"
    fi
    pass "served $path"
done

# Optional: exercise the external-plugin ingest API end-to-end. Skipped
# when SKIP_INGEST=1 or when offline (downloads from github.com).
if [[ "${SKIP_INGEST:-0}" != "1" ]]; then
    info "6/6  External plugin ingest API (POST /api/external, clone, DELETE)"
    EXT_URL="https://github.com/anthropics/claude-plugins-official/tree/main/plugins/claude-md-management"
    EXT_ID="github:anthropics/claude-plugins-official:plugins/claude-md-management"

    # Make sure the slot is empty before we start, so re-runs are idempotent.
    curl -sS -X DELETE "$BASE_URL/api/external/$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "$EXT_ID")" >/dev/null || true

    BODY="$(curl -sS -X POST "$BASE_URL/api/external" \
        -H 'Content-Type: application/json' \
        -d "{\"url\":\"$EXT_URL\"}" \
        -w '\nHTTP_CODE=%{http_code}')"
    HTTP_CODE="$(printf '%s\n' "$BODY" | sed -n 's/^HTTP_CODE=//p')"
    if [[ "$HTTP_CODE" != "201" ]]; then
        printf '%s\n' "$BODY" >&2
        fail "POST /api/external returned HTTP $HTTP_CODE (expected 201)"
    fi
    pass "POST /api/external accepted (201)"

    # The registry entry should now include the captured file tree so
    # repo.html can render the plugin as a browseable folder.
    if ! curl -sS "$BASE_URL/api/external" | python3 -c "
import json, sys
d = json.load(sys.stdin)
ext = next((e for e in d.get('externals', []) if e.get('name') == 'claude-md-management'), None)
sys.exit(0 if (ext and isinstance(ext.get('tree'), list) and ext['tree']) else 1)
"; then
        fail "registry entry is missing its 'tree' field"
    fi
    pass "GET /api/external includes captured file tree"

    # Clone again and check the new plugin is in the bare repo and listed
    # in both marketplace.json files.
    CLONE2_DIR="$TMP_DIR/marketplace-with-ext"
    if ! git clone -q "$REPO_URL" "$CLONE2_DIR" 2>"$TMP_DIR/clone2.log"; then
        cat "$TMP_DIR/clone2.log" >&2
        fail "git clone after ingest failed"
    fi
    if [[ ! -d "$CLONE2_DIR/plugins/claude-md-management" ]]; then
        fail "ingested plugin missing from cloned repo: plugins/claude-md-management"
    fi
    pass "cloned repo contains plugins/claude-md-management/"

    for mf in .claude-plugin/marketplace.json .factory-plugin/marketplace.json; do
        if ! python3 -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if any(p.get('name')=='claude-md-management' for p in d.get('plugins', [])) else 1)" "$CLONE2_DIR/$mf"; then
            fail "ingested plugin not listed in $mf"
        fi
        pass "$mf lists claude-md-management"
    done

    DEL_CODE="$(curl -sS -o /dev/null -w '%{http_code}' -X DELETE "$BASE_URL/api/external/$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "$EXT_ID")")"
    if [[ "$DEL_CODE" != "204" ]]; then
        fail "DELETE /api/external returned HTTP $DEL_CODE (expected 204)"
    fi
    pass "DELETE /api/external accepted (204)"

    CLONE3_DIR="$TMP_DIR/marketplace-after-delete"
    git clone -q "$REPO_URL" "$CLONE3_DIR" 2>/dev/null
    if [[ -d "$CLONE3_DIR/plugins/claude-md-management" ]]; then
        fail "plugin still present after DELETE"
    fi
    pass "plugin removed from cloned repo after DELETE"
fi

info "7/7  File viewer API (GET /api/file)"
# Markdown
MD_BODY="$(curl -fsS "$BASE_URL/api/file?path=plugins/code-review/skills/code-review/SKILL.md")"
if ! python3 -c "import json,sys; d=json.loads(sys.argv[1]); assert d['kind']=='markdown' and d['size']>0 and d['content']" "$MD_BODY" 2>/dev/null; then
    fail "GET /api/file did not return markdown content for SKILL.md"
fi
pass "Markdown file fetched and classified as 'markdown'"

# JSON
JSON_BODY="$(curl -fsS "$BASE_URL/api/file?path=plugins/code-review/.claude-plugin/plugin.json")"
if ! python3 -c "import json,sys; d=json.loads(sys.argv[1]); assert d['kind']=='json' and d['size']>0; json.loads(d['content'])" "$JSON_BODY" 2>/dev/null; then
    fail "GET /api/file did not return parseable JSON content"
fi
pass "JSON file fetched, classified as 'json', and content parses"

# Path traversal must be rejected
TRAV_CODE="$(curl -sS -o /dev/null -w '%{http_code}' "$BASE_URL/api/file?path=../../../etc/passwd")"
if [[ "$TRAV_CODE" != "400" ]]; then
    fail "Path traversal returned HTTP $TRAV_CODE (expected 400)"
fi
pass "Path traversal rejected with 400"

# Missing file -> 404
MISSING_CODE="$(curl -sS -o /dev/null -w '%{http_code}' "$BASE_URL/api/file?path=plugins/does-not-exist.txt")"
if [[ "$MISSING_CODE" != "404" ]]; then
    fail "Missing file returned HTTP $MISSING_CODE (expected 404)"
fi
pass "Missing file returns 404"

printf '\n\033[32mAll checks passed — any CLI can clone %s\033[0m\n' "$REPO_URL"
