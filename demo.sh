#!/usr/bin/env bash
#
# RST-B1 — one command from a clean clone to a completed evaluation.
#
#   ./demo.sh
#
# Creates the virtualenv, installs dependencies, builds the schema with its
# append-only guards, seeds demo data, and serves the API. Nothing is asked of
# the operator at any point and no API credential is involved: the default
# evaluation provider is the deterministic offline mock.
#
# Re-running is safe. Seeding is skipped when the database already holds
# evaluations, so the command never overwrites a verdict that has been
# recorded — see the versioning rule in README.md.
#
# Options:
#   --port N     listen on N (default 8000, or $DEMO_PORT)
#   --host H     bind H   (default 127.0.0.1, or $DEMO_HOST)
#   --no-serve   do everything except start the server
#   --reset      rebuild the schema from scratch before seeding (destructive,
#                local demo data only; never the default)
#
# Environment:
#   DEMO_PYTHON  use this interpreter instead of creating a virtualenv. Set by
#                the test suite so its bring-up check can run offline; the CI
#                `demo` job deliberately leaves it unset so the install leg is
#                exercised on a bare runner.

set -euo pipefail

cd "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

HOST="${DEMO_HOST:-127.0.0.1}"
PORT="${DEMO_PORT:-8000}"
SERVE=1
RESET=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --port) PORT="${2:?--port needs a value}"; shift 2 ;;
        --host) HOST="${2:?--host needs a value}"; shift 2 ;;
        --no-serve) SERVE=0; shift ;;
        --reset) RESET=1; shift ;;
        # Prints the header comment above, stopping at the first line that is
        # not a comment. A hardcoded line range drifts the moment the header is
        # edited, and had already drifted into printing `set -euo pipefail`.
        -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"; exit 0 ;;
        *) echo "demo.sh: unknown argument '$1'" >&2; exit 2 ;;
    esac
done

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

# --- interpreter -----------------------------------------------------------
# Python 3.10 is the floor because that is the oldest version CI proves.

find_python() {
    local candidate
    for candidate in python3.12 python3.11 python3.10 python3; do
        if command -v "$candidate" >/dev/null 2>&1 &&
           "$candidate" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)' 2>/dev/null; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

has_dependencies() {
    "$1" - <<'PY' >/dev/null 2>&1
import importlib
for module in ("fastapi", "uvicorn", "sqlalchemy", "pydantic_settings", "jwt", "multipart"):
    importlib.import_module(module)
PY
}

if [ -n "${DEMO_PYTHON:-}" ]; then
    PY="$DEMO_PYTHON"
    say "using the interpreter named by DEMO_PYTHON"
elif [ -x .venv/bin/python ] && has_dependencies .venv/bin/python; then
    PY="$PWD/.venv/bin/python"
    say "reusing the existing virtualenv"
else
    if ! BOOTSTRAP_PY="$(find_python)"; then
        echo "demo.sh: needs Python 3.10 or newer on PATH; none found." >&2
        exit 1
    fi
    if [ ! -x .venv/bin/python ]; then
        say "creating .venv with $("$BOOTSTRAP_PY" --version)"
        "$BOOTSTRAP_PY" -m venv .venv
    fi
    PY="$PWD/.venv/bin/python"
fi

if ! has_dependencies "$PY"; then
    say "installing dependencies"
    "$PY" -m pip install --quiet --upgrade pip
    "$PY" -m pip install --quiet --requirement requirements.txt
fi

# --- schema and demo data --------------------------------------------------

if [ "$RESET" -eq 1 ]; then
    say "rebuilding the schema (--reset)"
    "$PY" -m scripts.init_db --drop
else
    say "creating the schema if absent"
    "$PY" -m scripts.init_db
fi

say "seeding demo data"
"$PY" -m scripts.seed --if-empty

# --- serve -----------------------------------------------------------------

if [ "$SERVE" -eq 0 ]; then
    say "ready (--no-serve, nothing started)"
    exit 0
fi

cat <<BANNER

  API      http://${HOST}:${PORT}
  Docs     http://${HOST}:${PORT}/docs
  Sign in  admin.acme@example.com / demo-password-123

  The review queue already has work in it:
    GET /reviews/pending     evaluations the router sent to a human
    GET /audit-log/verify    the hash chain, checked end to end

BANNER

exec "$PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT"
