# TN-MEMPALACE-700-005: Prove shared memory

## 1. Objective

Run isolated write and read tests from two agent clients through the production path.

## 2. Prerequisites

- The proposed Trinity and this task note are reviewed through Trax.
- Direct dependencies in TNP-MEMPALACE-700.md are complete or explicitly waived by named human approval.
- No secrets, runtime databases, or unrelated worktree changes are included.

## 3. Implementation Boundaries

- Make only changes required for this task.
- Preserve beta/alpha branch domains and existing user work.
- Keep backing services private unless an authenticated Tailnet route is explicitly required.

## 4. Required Proof

- Record a focused test, health check, or configuration validation.
- Where access policy changes, prove one permitted and one denied case.
- Run MAR before marking the task complete.

## 5. Dynamic Context Pruning (DCP)

- **Load**: this TN, immediate dependencies, and the smallest relevant contract slices.
- **Keep**: objective, security constraints, acceptance proof, and current blocker evidence.
- **Prune**: completed sibling work, stale topology assumptions, and unrelated changes.
- **Exit With**: changed files, proof, residual risks, and next-task handoff.
