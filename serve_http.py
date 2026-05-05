#!/usr/bin/env python3
"""
MemPalace HTTP MCP adapter — FastMCP streamable-HTTP transport on port 5031.

Bridges the palace to the ApexSigma gateway as a persistent remote server
instead of a per-request stdio subprocess. Managed by the systemd user service
``apexsigma-mempalace-http``.

Run via systemd:
    uv run --extra http serve_http.py

Env resolved (in priority order):
    1. Existing process environment
    2. ~/.config/containers/secrets/apexsigma-vault.env (sourced at startup)
    3. Defaults baked into the pgvector backend (host=127.0.0.1, port=5432)
"""

import os
import sys
from pathlib import Path

# ── 1. Source vault credentials before importing mempalace ───────────────────
# mempalace.mcp_server reads env at module level via MempalaceConfig(). The
# pgvector backend calls os.environ.get("APEXSIGMA_VAULT_POSTGRES_*") on every
# connection, so late-binding is safe, but sourcing early avoids surprises.
_vault_env = Path.home() / ".config/containers/secrets/apexsigma-vault.env"
if _vault_env.is_file():
    for _line in _vault_env.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#"):
            continue
        # Strip optional shell 'export ' prefix
        if _line.startswith("export "):
            _line = _line[7:]
        if "=" not in _line:
            continue
        _key, _, _val = _line.partition("=")
        _key = _key.strip()
        _val = _val.strip()
        # Remove surrounding single or double quotes
        if len(_val) >= 2 and _val[0] == _val[-1] and _val[0] in ('"', "'"):
            _val = _val[1:-1]
        os.environ.setdefault(_key, _val)
else:
    print(
        f"[mempalace-http] WARNING: vault env file not found at {_vault_env}",
        file=sys.stderr,
    )

# ── 2. Force pgvector backend and Ollama embedding config ────────────────────
os.environ["MEMPALACE_BACKEND"] = "pgvector"
os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")
os.environ.setdefault("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text:v1.5")

# ── 3. Import palace tools (env must be set before this) ─────────────────────
# mcp_server.py redirects fd 1 (stdout) → fd 2 (stderr) at import time to
# protect JSON-RPC stdio from noisy transitive-dep banners (issue #225).
# Restore stdout after import so uvicorn's HTTP I/O operates normally.
from mempalace.mcp_server import TOOLS, _restore_stdout  # noqa: E402

_restore_stdout()

# ── 4. Build FastMCP server ───────────────────────────────────────────────────
from fastmcp import FastMCP  # noqa: E402

mcp = FastMCP(
    name="mempalace",
    instructions=(
        "MemPalace memory palace — verbatim storage, semantic search, "
        "knowledge graph, and agent diary. Call mempalace_status on wake-up."
    ),
)

# Register every tool from the existing TOOLS registry.
# FastMCP introspects the function signature for parameter types; the JSON
# Schema in input_schema is authoritative for the gateway manifest.
for _tool_name, _tool_config in TOOLS.items():
    mcp.tool(
        name=_tool_name,
        description=_tool_config["description"],
    )(_tool_config["handler"])

# ── 5. Entry point ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("[mempalace-http] Starting MemPalace HTTP MCP server on port 5031...", file=sys.stderr)
    mcp.run(transport="http", host="0.0.0.0", port=5031)
