#!/usr/bin/env python3
"""
External plugin ingest service.

A tiny HTTP API that lets admin.html add and remove "external" plugins by
GitHub URL. When a plugin is added, this service:

  1. Resolves the URL's ref to a full commit SHA via the unauthenticated
     GitHub commits API.
  2. Downloads the corresponding source tarball from GitHub.
  3. Extracts only the plugin sub-path (or the repo root) into:
       - /usr/share/nginx/html/plugins/<name>/   (static files served by nginx)
       - the working copy used to push into the bare git repo
  4. Re-renders the per-marketplace JSON files (.claude-plugin/marketplace.json
     and .factory-plugin/marketplace.json) with an entry for the new plugin
     so Claude Code and Droid CLI both pick it up.
  5. Commits and pushes the result into the bare repo so `git clone` of the
     marketplace returns the new files immediately.
  6. Persists the registry to /var/lib/marketplace/externals.json. The same
     file is served back to browsers via GET /api/external so all three
     pages (admin, index, repo) share a single source of truth.

API:
  GET    /api/external             -> 200 {"externals": [...]}
  POST   /api/external             -> 201 {"plugin": {...}}    body: {"url": "...", "sha": "..."}
  DELETE /api/external/<id>        -> 204
  GET    /api/external/health      -> 200 {"ok": true}

Errors return JSON: {"error": "..."} with an appropriate 4xx/5xx code.

All long-running work (download / extract / commit) happens under a global
lock so concurrent adds cannot corrupt the bare repo.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --- Configuration ---------------------------------------------------------

WEB_ROOT       = os.environ.get("WEB_ROOT",       "/usr/share/nginx/html")
PLUGINS_DIR    = os.path.join(WEB_ROOT, "plugins")
CLAUDE_MARKET  = os.path.join(WEB_ROOT, ".claude-plugin", "marketplace.json")
FACTORY_MARKET = os.path.join(WEB_ROOT, ".factory-plugin", "marketplace.json")
AGENTS_MARKET  = os.path.join(WEB_ROOT, ".agents", "plugins", "marketplace.json")

# Every marketplace JSON file kept in sync. Each entry is the path relative
# to the repository root that contains the file.
MARKETPLACE_FILES = (
    ".claude-plugin/marketplace.json",
    ".factory-plugin/marketplace.json",
    ".agents/plugins/marketplace.json",
)

GIT_DIR        = os.environ.get("GIT_DIR",        "/var/lib/git/droid/v1/marketplace.git")
STATE_DIR      = os.environ.get("STATE_DIR",      "/var/lib/marketplace")
EXTERNALS_FILE = os.path.join(STATE_DIR, "externals.json")

LISTEN_HOST    = os.environ.get("INGEST_HOST", "127.0.0.1")
LISTEN_PORT    = int(os.environ.get("INGEST_PORT", "8089"))

GITHUB_API     = "https://api.github.com"

# Concurrent adds must not race on the working tree / bare repo.
LOCK = threading.Lock()


# --- Helpers ---------------------------------------------------------------

def log(msg):
    print(f"[ingest] {msg}", flush=True)


def run(cmd, cwd=None, check=True, env=None):
    """Run a subprocess, returning (returncode, stdout, stderr)."""
    log(f"$ {' '.join(cmd)}  (cwd={cwd or os.getcwd()})")
    proc = subprocess.run(
        cmd, cwd=cwd, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
    return proc.returncode, proc.stdout, proc.stderr


def ensure_dirs():
    os.makedirs(PLUGINS_DIR,    exist_ok=True)
    os.makedirs(STATE_DIR,      exist_ok=True)
    os.makedirs(os.path.dirname(CLAUDE_MARKET),  exist_ok=True)
    os.makedirs(os.path.dirname(FACTORY_MARKET), exist_ok=True)


# --- File-tree walker -----------------------------------------------------

def walk_tree(root):
    """Walk `root` and return a JSON-serialisable tree of {name,type,ext,children}.

    Directories come first, then files; both groups sorted alphabetically.
    Used so repo.html can render external plugin contents the same way it
    renders built-in plugins (with expandable folders)."""
    if not os.path.isdir(root):
        return []
    entries = sorted(os.listdir(root), key=lambda n: (not os.path.isdir(os.path.join(root, n)), n.lower()))
    nodes = []
    for name in entries:
        full = os.path.join(root, name)
        if os.path.isdir(full):
            nodes.append({
                "name":     name,
                "type":     "dir",
                "children": walk_tree(full),
            })
        else:
            ext = os.path.splitext(name)[1].lstrip(".").lower()
            nodes.append({
                "name": name,
                "type": "file",
                "ext":  ext,
            })
    return nodes


# --- Repo file reader -----------------------------------------------------

# Files larger than this are refused as a defence against accidentally
# loading huge binaries (e.g. images) into the browser.
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB

# A small allow-list of extensions we treat as text. Anything else is
# rejected to keep the viewer from rendering arbitrary binary content.
TEXT_EXTS = {
    "md", "markdown", "txt", "text",
    "json", "yaml", "yml", "toml", "ini", "cfg", "conf",
    "py", "js", "mjs", "ts", "tsx", "jsx",
    "html", "htm", "css", "scss",
    "sh", "bash", "zsh", "fish",
    "rb", "go", "rs", "java", "c", "cc", "cpp", "h", "hpp",
    "php", "pl", "lua", "swift", "kt", "kts", "dart",
    "xml", "svg",
    "dockerfile", "makefile", "gitignore", "gitattributes",
    "lock", "log", "env", "example", "sample",
    "license", "licence",
    "",  # extensionless files (Dockerfile, Makefile, LICENSE...)
}


def _is_path_safe(p):
    """Reject absolute paths and any segment of '..' to prevent traversal."""
    if not p or p.startswith("/"):
        return False
    if any(part in ("", "..", ".") for part in p.split("/")):
        return False
    return True


def _classify(path):
    """Return a short type tag used by the front-end to choose a renderer."""
    name = os.path.basename(path).lower()
    ext  = os.path.splitext(name)[1].lstrip(".").lower()
    if ext in ("md", "markdown"):                   return "markdown"
    if ext == "json":                               return "json"
    if ext in ("yaml", "yml"):                      return "yaml"
    if name in ("dockerfile", "makefile", "license", "licence"):
        return "text"
    return "text"


def _looks_binary(blob):
    """Heuristic: treat as binary if there's any NUL byte or many control bytes."""
    if b"\x00" in blob:
        return True
    # Reject if >10% of the first 8 KiB are non-text control bytes.
    sample = blob[:8192]
    if not sample:
        return False
    nonprint = sum(1 for b in sample if b < 9 or (13 < b < 32))
    return nonprint / len(sample) > 0.1


def read_repo_file(path):
    """Read a file from the bare git repo by path (relative to repo root).

    Uses `git show master:<path>` so every file in the marketplace tree
    is accessible, including files that are not statically served by
    nginx (Dockerfile, start.sh, nginx.conf)."""
    if not _is_path_safe(path):
        raise ValueError("Path is not allowed")

    name = os.path.basename(path).lower()
    ext  = os.path.splitext(name)[1].lstrip(".").lower()
    extkey = ext if ext else name  # for extensionless files match by name
    if ext not in TEXT_EXTS and extkey not in TEXT_EXTS and name not in TEXT_EXTS:
        raise ValueError(f"File type '.{ext or name}' is not viewable as text")

    proc = subprocess.run(
        ["git", "-C", GIT_DIR, "show", f"master:{path}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_git_env(),
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        if "does not exist" in err or "exists on disk" in err:
            raise FileNotFoundError(path)
        raise RuntimeError(err or f"git show failed for {path}")
    blob = proc.stdout

    if len(blob) > MAX_FILE_BYTES:
        raise ValueError(f"File too large to display ({len(blob)} bytes; max {MAX_FILE_BYTES})")
    if _looks_binary(blob):
        raise ValueError("File appears to be binary")

    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError:
        text = blob.decode("latin-1")

    return {
        "path":    path,
        "kind":    _classify(path),
        "size":    len(blob),
        "content": text,
    }


# --- Registry persistence --------------------------------------------------

def load_externals():
    if not os.path.exists(EXTERNALS_FILE):
        return []
    try:
        with open(EXTERNALS_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log(f"warning: could not load externals.json: {e}")
        return []


def save_externals(externals):
    tmp = EXTERNALS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(externals, f, indent=2)
    os.replace(tmp, EXTERNALS_FILE)


# --- GitHub interaction ----------------------------------------------------

GITHUB_URL_RE = re.compile(
    r"^https?://github\.com/"
    r"(?P<owner>[^/\s?#]+)/(?P<repo>[^/\s?#]+?)(?:\.git)?"
    r"(?:/(?:tree|blob)/(?P<ref>[^/\s?#]+)(?:/(?P<path>[^?#]+?))?)?"
    r"/?(?:[?#].*)?$"
)


def parse_github_url(url):
    m = GITHUB_URL_RE.match((url or "").strip())
    if not m:
        return None
    owner = m.group("owner")
    repo  = m.group("repo")
    ref   = m.group("ref")
    path  = (m.group("path") or "").rstrip("/")
    return {
        "owner": owner,
        "repo":  f"{owner}/{repo}",
        "name":  repo,
        "ref":   ref,
        "path":  path,
    }


def http_get_json(url):
    req = urllib.request.Request(url, headers={
        "Accept":     "application/vnd.github+json",
        "User-Agent": "plugin-marketplace-ingest",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def resolve_commit(repo, ref):
    """Resolve any ref (branch/tag/short SHA/full SHA/None) to a full 40-char SHA."""
    ref_part = urllib.parse.quote(ref) if ref else "HEAD"
    url = f"{GITHUB_API}/repos/{repo}/commits/{ref_part}"
    try:
        data = http_get_json(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ValueError(f"Not found on GitHub: {repo} @ {ref or 'default branch'}")
        if e.code == 403:
            raise ValueError("GitHub API rate limit exceeded. Try again later or paste a commit SHA explicitly.")
        raise ValueError(f"GitHub API error {e.code} resolving {repo}@{ref or 'HEAD'}")
    sha = data.get("sha")
    if not sha:
        raise ValueError("GitHub returned no commit SHA")
    return sha


def download_tarball(repo, commit, dest_dir):
    """Download `https://codeload.github.com/<repo>/tar.gz/<commit>` to dest_dir.

    Returns the path to the extracted top-level directory (which GitHub names
    `<repo-name>-<commit>`).
    """
    url = f"https://codeload.github.com/{repo}/tar.gz/{commit}"
    tarball = os.path.join(dest_dir, "src.tar.gz")
    log(f"downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "plugin-marketplace-ingest"})
    with urllib.request.urlopen(req, timeout=120) as r, open(tarball, "wb") as f:
        shutil.copyfileobj(r, f)

    extracted_root = os.path.join(dest_dir, "extracted")
    os.makedirs(extracted_root, exist_ok=True)
    log(f"extracting tarball into {extracted_root}")
    with tarfile.open(tarball, "r:gz") as tf:
        # data filter is available on Python 3.12+; fall back gracefully.
        try:
            tf.extractall(extracted_root, filter="data")
        except TypeError:
            tf.extractall(extracted_root)
    entries = [e for e in os.listdir(extracted_root) if os.path.isdir(os.path.join(extracted_root, e))]
    if not entries:
        raise RuntimeError("tarball extracted no directories")
    return os.path.join(extracted_root, entries[0])


# --- Bare repo operations --------------------------------------------------

def _git_env():
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME",     "Plugin Marketplace")
    env.setdefault("GIT_AUTHOR_EMAIL",    "marketplace@plugin-marketplace.local")
    env.setdefault("GIT_COMMITTER_NAME",  env["GIT_AUTHOR_NAME"])
    env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])
    # The bare repo is owned by `nginx` (so the FastCGI worker can read it),
    # but the ingest service runs as root. Modern git refuses to operate on
    # a repo whose ownership differs from the current uid unless explicitly
    # whitelisted. We trust everything inside the container.
    env.setdefault("GIT_CONFIG_COUNT",   "1")
    env.setdefault("GIT_CONFIG_KEY_0",   "safe.directory")
    env.setdefault("GIT_CONFIG_VALUE_0", "*")
    return env


def _clone_working_copy(work):
    """Clone the bare repo (master) into a working copy for editing."""
    run(["git", "clone", GIT_DIR, work], env=_git_env())
    # Sanitize default branch name
    run(["git", "-C", work, "checkout", "master"], env=_git_env())


def _commit_and_push(work, message):
    run(["git", "-C", work, "add", "-A"], env=_git_env())
    code, out, _ = run(["git", "-C", work, "status", "--porcelain"], env=_git_env())
    if not out.strip():
        log("nothing to commit, skipping push")
        return
    run(["git", "-C", work, "commit", "-m", message], env=_git_env())
    run(["git", "-C", work, "push", "origin", "master"], env=_git_env())
    # Refresh dumb-http info files in the bare repo (best-effort).
    run(["git", "-C", GIT_DIR, "update-server-info"], check=False, env=_git_env())


# --- marketplace.json regeneration ----------------------------------------

def _read_marketplace(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _write_marketplace(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _claude_entry(ext):
    """Build a Claude Code marketplace entry for an external plugin.

    We point `source` at the local relative path (./plugins/<name>) because
    the files have been downloaded and committed into the marketplace repo.
    This makes the plugin install identically to a built-in plugin.
    """
    return {
        "name":        ext["name"],
        "source":      f"./plugins/{ext['name']}",
        "description": ext.get("description") or f"External plugin from {ext['repo']}",
        "version":     ext["commit"][:7],
        "author":      {"name": ext["repo"].split("/")[0]},
        "category":    "external",
        "tags":        ["external", "github"],
        "license":     "see source",
        "external": {
            "repo":   ext["repo"],
            "commit": ext["commit"],
            "ref":    ext.get("ref") or "HEAD",
            "path":   ext.get("path") or "",
            "url":    ext.get("url"),
        }
    }


def _filter_out_external(plugins, plugin_name):
    return [p for p in plugins if not (
        isinstance(p, dict)
        and p.get("name") == plugin_name
        and isinstance(p.get("external"), dict)
    )]


def update_marketplaces(work, externals):
    """Rewrite marketplace.json files inside the working copy to list all
    currently registered external plugins.

    Built-in entries are preserved verbatim; we only rewrite/remove entries
    that carry an `external` block (added by us)."""
    for relpath in MARKETPLACE_FILES:
        abs_path = os.path.join(work, relpath)
        if not os.path.exists(abs_path):
            log(f"marketplace.json missing in working copy: {relpath}")
            continue
        with open(abs_path) as f:
            data = json.load(f)
        builtins = [p for p in data.get("plugins", []) if not (
            isinstance(p, dict) and isinstance(p.get("external"), dict)
        )]
        ext_entries = [_claude_entry(e) for e in externals]
        data["plugins"] = builtins + ext_entries
        _write_marketplace(abs_path, data)


# --- Core add / remove flows ----------------------------------------------

def _external_id(repo, path):
    return f"github:{repo}:{path or ''}"


def _safe_plugin_name(raw):
    """Allow only filename-safe characters; collapse the rest."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", raw or "").strip("-.")
    return cleaned or "plugin"


def _ensure_agents_manifest(plugin_dir, name, repo, commit, path=""):
    """Create .agents/marketplace.json inside a plugin directory so the
    plugin is discoverable via the agents protocol."""
    agents_dir = os.path.join(plugin_dir, ".agents")
    os.makedirs(agents_dir, exist_ok=True)
    manifest = {
        "name":        name,
        "source":      f"./plugins/{name}",
        "description": f"External plugin from {repo}",
        "version":     commit[:7],
        "author":      {"name": repo.split("/")[0]},
        "external": {
            "repo":   repo,
            "commit": commit,
            "path":   path or "",
        },
    }
    manifest_path = os.path.join(agents_dir, "marketplace.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")


def add_external(url, sha_input):
    """Resolve + download + extract + commit an external plugin."""
    parsed = parse_github_url(url)
    if not parsed:
        raise ValueError("Not a recognized GitHub URL")

    repo    = parsed["repo"]
    ref     = parsed["ref"]
    subpath = parsed["path"]
    user_sha = (sha_input or "").strip()
    commit  = resolve_commit(repo, user_sha or ref)

    raw_name = subpath.split("/")[-1] if subpath else parsed["name"]
    name = _safe_plugin_name(raw_name)
    pid  = _external_id(repo, subpath)

    with LOCK:
        externals = load_externals()
        if any(p["id"] == pid for p in externals):
            raise ValueError("That plugin is already added")
        if any(p["name"] == name and p["id"] != pid for p in externals):
            raise ValueError(f"Another external plugin named '{name}' is already added")
        # Refuse to overwrite a built-in plugin directory.
        target_static = os.path.join(PLUGINS_DIR, name)
        if os.path.exists(target_static) and not any(p["name"] == name for p in externals):
            raise ValueError(f"A built-in plugin already exists at plugins/{name}")

        # --- Download tarball ---
        tmp = tempfile.mkdtemp(prefix="ingest-")
        try:
            extracted = download_tarball(repo, commit, tmp)
            # Source folder inside the tarball: either repo-root or sub-path.
            src = extracted
            if subpath:
                src = os.path.join(extracted, *subpath.split("/"))
                if not os.path.isdir(src):
                    raise ValueError(
                        f"Path '{subpath}' not found in {repo}@{commit[:7]}"
                    )

            # --- Stage into static plugins dir ---
            staging = os.path.join(tmp, "staged-" + name)
            shutil.copytree(src, staging)

            # Ensure the plugin directory contains .agents/marketplace.json
            # so it is visible under plugins/<name> in the repo view.
            _ensure_agents_manifest(staging, name, repo, commit, subpath)

            # --- Clone bare repo, sync files, commit, push ---
            work = os.path.join(tmp, "work")
            _clone_working_copy(work)
            work_target = os.path.join(work, "plugins", name)
            if os.path.exists(work_target):
                shutil.rmtree(work_target)
            shutil.copytree(staging, work_target)

            plugin = {
                "id":      pid,
                "name":    name,
                "repo":    repo,
                "path":    subpath,
                "ref":     ref or "HEAD",
                "commit":  commit,
                "url":     url.strip(),
                "addedAt": None,
                # File tree of the downloaded plugin, used by repo.html so
                # the external plugin folder is fully browseable.
                "tree":    walk_tree(staging),
            }
            new_externals = externals + [plugin]
            update_marketplaces(work, new_externals)

            _commit_and_push(
                work,
                f"Add external plugin {name} from {repo}@{commit[:7]}"
                + (f" path={subpath}" if subpath else "")
            )

            # --- Sync static files served by nginx ---
            if os.path.exists(target_static):
                shutil.rmtree(target_static)
            shutil.copytree(staging, target_static)
            # Sync marketplace.json files from working copy to nginx root.
            shutil.copyfile(os.path.join(work, ".claude-plugin",  "marketplace.json"), CLAUDE_MARKET)
            shutil.copyfile(os.path.join(work, ".factory-plugin", "marketplace.json"), FACTORY_MARKET)
            os.makedirs(os.path.dirname(AGENTS_MARKET), exist_ok=True)
            shutil.copyfile(os.path.join(work, ".agents", "plugins", "marketplace.json"), AGENTS_MARKET)

            # --- Persist registry ---
            save_externals(new_externals)
            return plugin
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def remove_external(plugin_id):
    with LOCK:
        externals = load_externals()
        match = next((p for p in externals if p["id"] == plugin_id), None)
        if not match:
            raise KeyError(plugin_id)
        new_externals = [p for p in externals if p["id"] != plugin_id]

        tmp = tempfile.mkdtemp(prefix="ingest-rm-")
        try:
            work = os.path.join(tmp, "work")
            _clone_working_copy(work)
            work_target = os.path.join(work, "plugins", match["name"])
            if os.path.exists(work_target):
                shutil.rmtree(work_target)
            update_marketplaces(work, new_externals)
            _commit_and_push(
                work,
                f"Remove external plugin {match['name']} ({match['repo']})"
            )

            # Sync static files.
            static_target = os.path.join(PLUGINS_DIR, match["name"])
            if os.path.exists(static_target):
                shutil.rmtree(static_target)
            shutil.copyfile(os.path.join(work, ".claude-plugin",  "marketplace.json"), CLAUDE_MARKET)
            shutil.copyfile(os.path.join(work, ".factory-plugin", "marketplace.json"), FACTORY_MARKET)
            os.makedirs(os.path.dirname(AGENTS_MARKET), exist_ok=True)
            shutil.copyfile(os.path.join(work, ".agents", "plugins", "marketplace.json"), AGENTS_MARKET)

            save_externals(new_externals)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# --- HTTP handler ----------------------------------------------------------

class IngestHandler(BaseHTTPRequestHandler):
    server_version = "PluginMarketplaceIngest/1.0"

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def log_message(self, fmt, *args):
        log("HTTP " + (fmt % args))

    def do_GET(self):
        if self.path == "/api/external/health":
            return self._send_json(200, {"ok": True})
        if self.path == "/api/external":
            return self._send_json(200, {"externals": load_externals()})
        # GET /api/file?path=<repo-relative-path>
        if self.path.startswith("/api/file"):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            raw = (qs.get("path") or [""])[0]
            try:
                payload = read_repo_file(raw)
            except FileNotFoundError:
                return self._send_json(404, {"error": "File not found"})
            except ValueError as e:
                return self._send_json(400, {"error": str(e)})
            except Exception as e:
                log(f"read_repo_file failed: {e}")
                return self._send_json(500, {"error": f"Read failed: {e}"})
            return self._send_json(200, payload)
        return self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        if self.path != "/api/external":
            return self._send_json(404, {"error": "Not found"})
        body = self._read_json()
        if body is None:
            return self._send_json(400, {"error": "Invalid JSON body"})
        url = (body.get("url") or "").strip()
        sha = (body.get("sha") or "").strip()
        if not url:
            return self._send_json(400, {"error": "Missing 'url'"})
        try:
            plugin = add_external(url, sha)
        except ValueError as e:
            return self._send_json(400, {"error": str(e)})
        except Exception as e:
            log(f"add_external failed: {e}")
            return self._send_json(500, {"error": f"Ingest failed: {e}"})
        return self._send_json(201, {"plugin": plugin})

    def do_DELETE(self):
        m = re.match(r"^/api/external/(?P<id>.+)$", self.path)
        if not m:
            return self._send_json(404, {"error": "Not found"})
        plugin_id = urllib.parse.unquote(m.group("id"))
        try:
            remove_external(plugin_id)
        except KeyError:
            return self._send_json(404, {"error": "Unknown plugin id"})
        except Exception as e:
            log(f"remove_external failed: {e}")
            return self._send_json(500, {"error": f"Remove failed: {e}"})
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()


# --- main ------------------------------------------------------------------

def backfill_trees():
    """Older registry entries lacked a `tree` field. On startup, walk the
    on-disk plugin folder of every external and populate it so repo.html
    can render it as an expandable folder without re-ingesting.
    Also ensures each external plugin has .agents/marketplace.json."""
    externals = load_externals()
    changed = False
    for ext in externals:
        plugin_dir = os.path.join(PLUGINS_DIR, ext["name"])
        if os.path.isdir(plugin_dir):
            _ensure_agents_manifest(
                plugin_dir, ext["name"], ext["repo"],
                ext["commit"], ext.get("path", ""),
            )
            if not ext.get("tree"):
                ext["tree"] = walk_tree(plugin_dir)
                changed = True
                log(f"backfilled tree for external {ext['name']}")
    if changed:
        save_externals(externals)


def main():
    ensure_dirs()
    backfill_trees()
    log(f"WEB_ROOT={WEB_ROOT}")
    log(f"PLUGINS_DIR={PLUGINS_DIR}")
    log(f"GIT_DIR={GIT_DIR}")
    log(f"STATE_DIR={STATE_DIR}")
    log(f"Listening on http://{LISTEN_HOST}:{LISTEN_PORT}")
    srv = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), IngestHandler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("shutting down")
        srv.shutdown()


if __name__ == "__main__":
    sys.exit(main() or 0)
