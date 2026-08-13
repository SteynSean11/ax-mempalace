# PRD-MEMPALACE-700: MemPalace Persistent Backend and Hook Transport

## 1. Strategic Objective (The "Why")

Create the shared MemPalace service backed by VPS PostgreSQL/pgvector without database access from desktops.

## 2. The Problem

The production-readiness audit found that the durable VPS core is running, but its shared knowledge, gateway, routing, model, and client layers have deployment and configuration drift.

## 3. The Solution (The "What")

Deliver this work through small, reviewed beta tasks, then deliberately promote verified changes to the matching Windows alpha checkout.

## 4. Success Metrics

- **Evidence**: each task has a focused proof and MAR result.
- **Security**: database ports remain VPS-loopback only.
- **Reliability**: every declared endpoint has a real consumer health check.
- **Promotion**: alpha adoption has an explicit rollback path.

## 5. Scope & Boundaries

- **Branch domain**: implementation belongs on this repository's beta branch.
- **Contract status**: proposed; execution requires Trinity review through Trax.
- **Exclusions**: secrets, destructive data migration, and unrelated worktree changes.
