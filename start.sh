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
fi

# Start fcgiwrap
echo "Starting fcgiwrap..."
spawn-fcgi -s /var/run/fcgiwrap.socket -M 766 -u nginx -g nginx /usr/bin/fcgiwrap
sleep 0.5
chmod 766 /var/run/fcgiwrap.socket

# Start nginx in foreground
echo "Starting nginx..."
exec nginx -g "daemon off;"
