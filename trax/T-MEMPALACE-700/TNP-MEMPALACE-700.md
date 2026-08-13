# TNP-MEMPALACE-700: MemPalace Persistent Backend and Hook Transport

## 1. High-Level Objective

Create the shared MemPalace service backed by VPS PostgreSQL/pgvector without database access from desktops.

## 2. "Done Means Done" Criteria

- [ ] **Contract accepted**: the PRD, TEK, and SPEC are reviewed through Trax.
- [ ] **Atomic work proven**: each task has focused evidence and MAR approval.
- [ ] **No topology drift**: registry entries, endpoints, and running services agree.
- [ ] **Promotion ready**: alpha handoff and rollback are documented.

- [ ] **G1 Provisioned**: MemPalace service is active on VPS loopback, uses dedicated pgvector credentials, passes local health and tool-list checks, and is accepted by Gateway. Caddy is not required.
- [ ] **G2 Client validated**: After T-INFRA-700 provides authenticated Tailnet ingress, two real agent clients complete a remote hook or service round trip without direct database access.
- [ ] **Backend contract resolved**: Before G1, explicit evidence selects pgvector or ChromaDB for the deployed persistent service and proves the selected schema, migration, and rollback path. No existing memory data is deleted on assumption.
## 3. Required Specs

- **PRD**: [PRD-MEMPALACE-700.md](file:///home/steynsean11/projects/apexsigma-mempalace/trax/T-MEMPALACE-700/PRD-MEMPALACE-700.md)
- **TEK**: [TEK-MEMPALACE-700.md](file:///home/steynsean11/projects/apexsigma-mempalace/trax/T-MEMPALACE-700/TEK-MEMPALACE-700.md)
- **SPEC**: [SPEC-MEMPALACE-700.md](file:///home/steynsean11/projects/apexsigma-mempalace/trax/T-MEMPALACE-700/SPEC-MEMPALACE-700.md)
- **Governance**: SPEC-TRAX-GOV-001.md
- **Audit**: Audit - Production Deployment Readiness 2026-08-13.md

## 4. Task Breakdown
- [ ] [TN-MEMPALACE-700-001] **Define durable database access**: Create a dedicated least-privilege pgvector service account and backend contract.
- [ ] [TN-MEMPALACE-700-002] **Deploy MemPalace on VPS**: Install the pinned alpha checkout and loopback-only FastMCP HTTP service with target-local configuration.
- [ ] [TN-MEMPALACE-700-003] **Register the live MCP endpoint**: Register MemPalace in Gateway only after direct health and tool-list checks pass.
- [ ] [TN-MEMPALACE-700-004] **Route local hooks safely**: Use authenticated remote hooks with idempotency and retry/dead-letter evidence.
- [ ] [TN-MEMPALACE-700-005] **Prove shared memory**: Run isolated write and read tests from two agent clients through the production path.

- [ ] [TN-MEMPALACE-700-006] **Split provisioning from client validation**: Close G1 with VPS-local service evidence and Gateway registration. Keep G2 open until authenticated client hooks prove durable read and write behavior through the published service path; do not collapse these acceptance gates.
- [ ] [TN-MEMPALACE-700-007] **Resolve persistent backend sovereignty before provisioning**: Reconcile the documented ChromaDB-or-pgvector declaration with the desired central pgvector target. Produce a runtime-backed backend decision, schema ownership map, migration strategy, and rollback boundary before MemPalace G1 deployment.
## 5. Notes & Context

- This is a proposed federated beta track, not production-change authorization.
- The owner repository is the contract source of truth; the central registry provides discovery pointers.
