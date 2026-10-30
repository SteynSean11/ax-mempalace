# TN-MEMPALACE-700-007: Resolve persistent backend sovereignty before provisioning

## 1. Objective

Reconcile the documented ChromaDB-or-pgvector declaration with the desired central pgvector target. Produce a runtime-backed backend decision, schema ownership map, migration strategy, and rollback boundary before MemPalace G1 deployment.

## 2. Prerequisites

- The parent Trinity is accepted through Trax.
- Required dependency evidence is present and rollback or recovery conditions are met.
- No secret value is printed, staged, copied, or entered into an agent prompt.

## 3. Implementation Boundaries

- Use bounded evidence collection and explicit path classification before mutation.
- Stage only scoped files and preserve unrelated worktree changes.
- Treat client access proof separately from VPS-loopback service proof.

## 4. Required Proof

- Record commands and outputs that prove the stated result without exposing credentials.
- Test the expected secure behavior and one deliberate denial or exclusion path where applicable.
- Run MAR before completion.

## 5. Dynamic Context Pruning (DCP)

- **Load**: this task, direct dependencies, and the smallest governing evidence.
- **Keep**: recovery status, authority decisions, security constraints, and proof target.
- **Prune**: secret values, raw service logs, obsolete path assumptions, and unrelated changes.
- **Exit With**: changed files, validation, residual risks, and next dependency.

## Decision D1 (RUNBOOK-INFRA-700-001 G0)

The MemPalace persistent backend is **PostgreSQL + pgvector**. ChromaDB is not deployed.

- **Source:** `RUNBOOK-INFRA-700-001` (apexsigma-eco-tools, `trax/T-INFRA-700/`), decision record D1 — owner direction 2026-10-15.
- **Runbook:** `apexsigma-eco-tools/trax/T-INFRA-700/RUNBOOK-INFRA-700-001.md` — §G0 step 8 (ledger stub).
- **Consistent with:** O2 ("Local moves to PostgreSQL 18"; `pgvector/pgvector:0.8.7-pg18` pinned by digest at deploy; the VPS uses the system `postgresql@18-main` with the pgvector 0.8.x package).
- **Scope:** this is a ledger stub recording an owner decision already taken. It decides nothing new, and the backend decision is re-opened for the authority question at G8 entry per O1.

