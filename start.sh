#!/bin/sh
set -e

# Ensure bare repo exists (idempotent)
GIT_DIR=/var/lib/git/droid/v1/marketplace.git
if [ ! -d "$GIT_DIR" ]; then
    echo "Initialising bare git repo at $GIT_DIR"
    mkdir -p "$GIT_DIR"
    cd "$GIT_DIR"
    git init --bare
    # Copy project files from nginx root into a temp working tree and push
    tmp=$(mktemp -d)
    cp -a /usr/share/nginx/html/* "$tmp/"
    cp -a /usr/share/nginx/html/.factory-plugin "$tmp/"
    cp -a /usr/share/nginx/html/.claude-plugin "$tmp/"
    cp -a /usr/share/nginx/html/.agents "$tmp/"
    cd "$tmp"
    git init
    git config user.email "marketplace@droid.local"
    git config user.name "Droid Marketplace"
    git add -A
    git commit -m "Droid Plugin Marketplace"
    git remote add origin "$GIT_DIR"
    git push origin master
    cd / && rm -rf "$tmp"
    # Generate dumb-HTTP info files (fallback)
    cd "$GIT_DIR"
    git update-server-info
    # Mark repo as exportable for git-http-backend (belt-and-braces)
    touch "$GIT_DIR/git-daemon-export-ok"
fi

# Ensure the nginx/fcgiwrap user can read the repo regardless of who created it
chown -R nginx:nginx /var/lib/git

# Persistent state for the ingest service (external plugin registry)
mkdir -p /var/lib/marketplace
chown -R nginx:nginx /var/lib/marketplace

# The ingest service runs as root but the bare repo is owned by `nginx`.
# Modern git refuses to operate on a repo whose ownership differs from
# the calling uid unless that path is on the `safe.directory` whitelist.
# We trust everything inside the container, so whitelist all paths.
git config --system --add safe.directory '*' || true

# Start fcgiwrap
echo "Starting fcgiwrap..."
spawn-fcgi -s /var/run/fcgiwrap.socket -M 766 -u nginx -g nginx /usr/sbin/fcgiwrap
sleep 0.5
chmod 766 /var/run/fcgiwrap.socket

# Start the external-plugin ingest service. Runs as root so it can write
# into /usr/share/nginx/html and the bare repo; nginx proxies /api/external
# to it on 127.0.0.1:8089.
echo "Starting ingest service..."
INGEST_HOST=127.0.0.1 INGEST_PORT=8089 \
  python3 /opt/ingest/ingest.py >/var/log/ingest.log 2>&1 &
sleep 0.5

# Start nginx in foreground
echo "Starting nginx..."
exec nginx -g "daemon off;"
