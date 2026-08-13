# TEK-MEMPALACE-700: Tech Stack Definition

## 1. Core Environment (The "With What")

- **Beta development**: /home/steynsean11/projects/apexsigma-mempalace.
- **Alpha promotion**: C:\Users\Sean\Projects matching repository.
- **Production**: VPS user services, rootless Podman, Caddy, and Tailscale.
- **Governance**: SPEC-TRAX-GOV-001 and the 2026-08-13 readiness audit.

## 2. Dependencies & Tooling

- **Trax**: repo-local contract surface and central compatibility registry.
- **Gateway/MCP**: authenticated HTTP transport with real health checks.
- **Storage**: VPS PostgreSQL/pgvector, Redis, and Neo4j remain private.
- **Secrets**: target-local environment files only; no values in Git.

## 3. Architectural Rules

- Active tracks live at trax/T-MEMPALACE-700/ in their owner repository.
- The central registry contains pointers, not the authoritative contract.
- Local runtimes use Tailnet application routes; no desktop client connects directly to a database.
- Do not modify existing user changes, including the current Omega uv.lock change.

## 4. Security & Auth

- Least-privilege credentials and explicit client allowlists.
- Caddy must publish only authenticated Tailnet application routes.
- Verify both an allowed request and an expected denial when access policy changes.
