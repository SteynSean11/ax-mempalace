#!/usr/bin/env bash
set -euo pipefail
HOOK_NAME="${1:?Usage: mempal-hook.sh <hook-name>}"
INPUT_FILE=$(mktemp) || { echo "Failed to create temp file" >&2; exit 1; }
CONFIG_FILE="${HOME}/.mempalace/config.json"
VAULT_ENV="${HOME}/.config/containers/secrets/apexsigma-vault.env"
cat > "$INPUT_FILE"

BACKEND=$(
python3 - <<'PY' "$CONFIG_FILE"
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
backend = "chroma"
try:
    backend = str(json.loads(path.read_text(encoding="utf-8")).get("backend", "chroma"))
except Exception:
    pass
backend = backend.strip().lower()
print("pgvector" if backend in {"postgres", "postgresql", "pgvector"} else "chroma")
PY
)

if [[ "$BACKEND" == "pgvector" ]]; then
    if [[ -f "$VAULT_ENV" ]]; then
        set -a
        # shellcheck disable=SC1090
        source "$VAULT_ENV"
        set +a
    fi
    export MEMPALACE_BACKEND=pgvector
    export PGHOST="${PGHOST:-127.0.0.1}"
    export PGPORT="${PGPORT:-5432}"
    export PGDATABASE="${PGDATABASE:-${APEXSIGMA_VAULT_POSTGRES_DB:-omega}}"
    export PGUSER="${PGUSER:-${APEXSIGMA_VAULT_POSTGRES_USER:-omega}}"
    export PGPASSWORD="${PGPASSWORD:-${APEXSIGMA_VAULT_POSTGRES_PASSWORD:-}}"
    export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
    export OLLAMA_EMBEDDING_MODEL="${OLLAMA_EMBEDDING_MODEL:-nomic-embed-text:v1.5}"
fi

cat "$INPUT_FILE" | mempalace hook run --hook "$HOOK_NAME" --harness codex
EXIT_CODE=$?
rm -f "$INPUT_FILE" 2>/dev/null
exit $EXIT_CODE
