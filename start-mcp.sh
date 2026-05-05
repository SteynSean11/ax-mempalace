#!/bin/bash
set -a
source ~/.config/containers/secrets/apexsigma-vault.env
set +a
# Explicitly re-export vault vars after set +a to ensure they propagate
# through exec into the Python subprocess (set -a scoping doesn't always
# survive exec in bash -c contexts started by gateway subprocess managers)
export APEXSIGMA_VAULT_POSTGRES_USER="${APEXSIGMA_VAULT_POSTGRES_USER}"
export APEXSIGMA_VAULT_POSTGRES_PASSWORD="${APEXSIGMA_VAULT_POSTGRES_PASSWORD}"
export APEXSIGMA_VAULT_POSTGRES_DB="${APEXSIGMA_VAULT_POSTGRES_DB}"
export APEXSIGMA_VAULT_NEO4J_AUTH="${APEXSIGMA_VAULT_NEO4J_AUTH}"
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
export OLLAMA_EMBEDDING_MODEL="${OLLAMA_EMBEDDING_MODEL:-nomic-embed-text:v1.5}"
export MEMPALACE_BACKEND=pgvector
export PYTHONPATH="/home/steynsean11/projects/apexsigma-eco-data/mempalace:${PYTHONPATH:-}"
echo "[start-mcp] Starting MemPalace MCP server with pgvector backend..." >&2
exec /home/steynsean11/.hermes/hermes-agent/venv/bin/python3 -m mempalace.mcp_server
