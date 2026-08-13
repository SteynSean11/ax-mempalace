# TN-MEMPALACE-700-006: Split provisioning from client validation

## 1. Objective

Close G1 with VPS-local service evidence and Gateway registration. Keep G2 open until authenticated client hooks prove durable read and write behavior through the published service path; do not collapse these acceptance gates.

## 2. Prerequisites

- The parent Trinity is accepted through Trax.
- No deployment mutation proceeds before the stated evidence and rollback prerequisites are met.
- Existing user changes remain out of scope unless explicitly adopted.

## 3. Implementation Boundaries

- Use BEDC: inventory, contradiction register, evidence, reconciliation map, classification, bounded mutation, and verification.
- Do not treat a loopback health check as desktop-client acceptance evidence.
- Do not add credentials, secrets, merged session data, or raw backups to Git.

## 4. Required Proof

- Record the relevant inventory and reconciliation evidence.
- Record service, route, or restore proof appropriate to the task.
- Run MAR before completion.

## 5. Dynamic Context Pruning (DCP)

- **Load**: this task, immediate dependency outcomes, and the minimum supporting contract sections.
- **Keep**: evidence, rollback method, access constraints, and completion proof.
- **Prune**: obsolete path assumptions, completed sibling work, and unrelated runtime output.
- **Exit With**: files changed, verification result, residual risk, and next dependency.
