#!/bin/bash
# macOS double-click launcher for Local Canvas.

set -u

ROOT="$(CDPATH= cd -- "$(dirname "$0")" && pwd -P)" || {
    echo "[ERROR] Unable to locate the project directory."
    printf "Press Return to close this window..."
    IFS= read -r _ || true
    exit 1
}

PORT="${LOCAL_CANVAS_PORT:-8900}"
APP_URL="http://127.0.0.1:${PORT}"
VENV_DIR="$ROOT/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
REQUIREMENTS="$ROOT/backend/requirements.txt"
REQUIREMENTS_MARKER="$VENV_DIR/.requirements.txt"

keep_terminal_open() {
    printf "%s" "${1:-Press Return to close this window...}"
    IFS= read -r _ || true
}

fail() {
    echo
    echo "[ERROR] $1" >&2
    echo "Startup failed. Read the message above, fix the issue, then run start.command again."
    echo
    keep_terminal_open
    exit 1
}

open_browser_when_ready() {
    local attempt=0

    while [ "$attempt" -lt 60 ]; do
        if curl -fsS --max-time 2 "$APP_URL/api/health" >/dev/null 2>&1; then
            open "$APP_URL" >/dev/null 2>&1 || true
            return 0
        fi
        attempt=$((attempt + 1))
        sleep 0.5
    done

    return 1
}

echo
echo "============================================"
echo "  Local Canvas - macOS launcher"
echo "============================================"
echo

if [ ! -f "$ROOT/backend/main.py" ] || [ ! -f "$REQUIREMENTS" ] || [ ! -f "$ROOT/web/package.json" ]; then
    fail "Run start.command from the extracted project folder."
fi

if ! command -v curl >/dev/null 2>&1; then
    fail "curl was not found. Install the macOS command line tools and try again."
fi

if curl -fsS --max-time 2 "$APP_URL/api/health" >/dev/null 2>&1; then
    echo "Local Canvas is already running at $APP_URL."
    if [ -z "${LOCAL_CANVAS_NO_BROWSER:-}" ]; then
        open "$APP_URL" >/dev/null 2>&1 || true
    fi
    exit 0
fi

if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    fail "Port $PORT is already in use by another program. Set LOCAL_CANVAS_PORT to a free port and run start.command again."
fi

if ! command -v python3 >/dev/null 2>&1; then
    fail "Python 3.10 or newer was not found. Install it from https://www.python.org/downloads/ and try again."
fi

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    fail "Python 3.10 or newer is required."
fi

if [ ! -x "$VENV_PYTHON" ]; then
    echo "[1/4] Creating local Python environment..."
    python3 -m venv "$VENV_DIR" || fail "Could not create .venv."
fi

if ! "$VENV_PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    fail "The existing .venv does not use Python 3.10 or newer. Remove .venv, then run start.command again."
fi

echo "[2/4] Checking Python dependencies..."
if ! "$VENV_PYTHON" -c 'import fastapi, uvicorn, requests, pydantic, multipart, socks, aiohttp_socks' >/dev/null 2>&1 \
    || [ ! -f "$REQUIREMENTS_MARKER" ] \
    || ! cmp -s "$REQUIREMENTS" "$REQUIREMENTS_MARKER" \
    || ! "$VENV_PYTHON" -m pip check >/dev/null 2>&1; then
    echo "      Installing Python dependencies..."
    "$VENV_PYTHON" -m pip install --upgrade pip || fail "Could not upgrade pip."
    "$VENV_PYTHON" -m pip install -r "$REQUIREMENTS" || fail "Could not install Python dependencies."
    cp "$REQUIREMENTS" "$REQUIREMENTS_MARKER" || fail "Could not save the dependency marker."
fi

echo "[3/4] Checking Node.js and frontend dependencies..."
if ! command -v node >/dev/null 2>&1; then
    fail "Node.js was not found. Install Node.js 20.19+ or 22.12+ from https://nodejs.org/."
fi

if ! command -v npm >/dev/null 2>&1; then
    fail "npm was not found with Node.js. Reinstall Node.js and try again."
fi

if ! node -e "const [major, minor] = process.versions.node.split('.').map(Number); process.exit((major === 20 && minor >= 19) || (major >= 22 && (major > 22 || minor >= 12)) ? 0 : 1)"; then
    fail "This frontend requires Node.js 20.19+ or 22.12+."
fi

if [ ! -f "$ROOT/web/package-lock.json" ] \
    || [ ! -f "$ROOT/web/node_modules/.package-lock.json" ] \
    || [ "$ROOT/web/package-lock.json" -nt "$ROOT/web/node_modules/.package-lock.json" ]; then
    echo "      Installing frontend dependencies..."
    (
        cd "$ROOT/web" || exit 1
        if [ -f package-lock.json ]; then
            npm ci --allow-remote=all
        else
            npm install --allow-remote=all
        fi
    ) || fail "Could not install frontend dependencies."
fi

echo "[4/4] Building frontend..."
(
    cd "$ROOT/web" || exit 1
    npm run build
) || fail "Could not build the frontend."

echo
echo "Starting Local Canvas at $APP_URL"
echo "Keep this Terminal window open while the service is running."
echo "Press Ctrl+C to stop it."
echo

browser_pid=""
if [ -z "${LOCAL_CANVAS_NO_BROWSER:-}" ]; then
    open_browser_when_ready &
    browser_pid=$!
fi

"$VENV_PYTHON" -m uvicorn backend.main:app --host 127.0.0.1 --port "$PORT"
server_status=$?

if [ -n "$browser_pid" ]; then
    kill "$browser_pid" >/dev/null 2>&1 || true
fi

if [ "$server_status" -ne 0 ] && [ "$server_status" -ne 130 ]; then
    fail "The server stopped with exit status $server_status."
fi

echo
echo "Local Canvas stopped."
keep_terminal_open
exit 0
