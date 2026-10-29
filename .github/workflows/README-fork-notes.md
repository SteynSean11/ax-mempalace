# Fork CI notes — ax-mempalace

This file records the ApexSigma CI baseline port applied to this fork, so
that a future upstream sync is a merge, not a re-derivation.

**Context:** `RUNBOOK-INFRA-700-001` (gate GC, finding C4) of
`apexsigma-eco-tools`. The audit recorded that `ax-mempalace` and
`ax-hermes` had no CI, no Security workflow, no MAR and no branch
protection, while being the intended daily-driver and collective-memory
repositories. This directory carries the shared baseline over.

## What is in this fork's `.github/`

| Path | Origin | Notes |
|---|---|---|
| `workflows/mar-review.yml` | copied **byte-identical** from `ax-trax@origin/beta` | git blob `edd4a7a2043b0348844f2dd2cf5d001865c5bbaa` — the same blob as `ax-trax`, `ax-gateway`, `ax-omega` and `ax-eco-context` |
| `workflows/mar-audit.yml` | copied byte-identical from `ax-trax@origin/beta` | git blob `ff19a727b66cf509df70fb4eef003b636b6b4bd1` — the post-merge MAR audit (SPEC-MAR-003 §12 option D) |
| `workflows/security.yml` | adapted from the shared baseline | Gitleaks + OSV + Semgrep. CodeQL and dependency review are **not** ported: both need GitHub Advanced Security, which is not provisioned on this fork |
| `workflows/ci.yml` | upstream's own file, minimally edited | The only change is adding `beta` to the push/PR trigger branches, so the fork's existing tests and lint run on the ApexSigma integration branch. The jobs themselves are unmodified upstream jobs |
| `workflows/version-guard.yml` | upstream's own file, unmodified | Runs on tag pushes and PRs touching version sources |
| `workflows/deploy-docs.yml.disabled` | upstream's file, renamed | **Disabled**: the fork has no GitHub Pages site (`GET /repos/{owner}/{repo}/pages` → 404) and no `pages: write` grant. Renamed, not deleted, so an upstream sync stays clean |
| `workflows/bump-plugin-version.yml.disabled` | upstream's file, already disabled upstream | Untouched |
| `mar/prompt.md` | copied byte-identical from `ax-trax@origin/beta` | The MAR reviewer prompt contract |
| `mar/verdict.schema.json` | copied byte-identical from `ax-trax@origin:beta` | The MAR verdict schema |

Outside `.github/`, the port adds:

| Path | Origin | Notes |
|---|---|---|
| `scripts/mar_gate.py` | copied byte-identical from `ax-trax@origin/beta` | git blob `a5c9d3b7768341d5036bacc5d0a796ed4b18e202`. Pure decision logic; the workflow does all network I/O. Stdlib only, no new dependency |
| `osv-scanner.toml` | new | Owner-approved, time-boxed OSV ignores. Currently empty of exceptions |

## Two deliberate differences from the shared `security.yml` baseline

### 1. Semgrep scans `.`, not `src`

The shared baseline scans a `src/` layout. This fork is a flat package: the
`mempalace/` package sits at the repository root. There is no `src/` here,
and `semgrep --error src` aborts with `Invalid scanning root: src`
(verified locally).

### 2. Semgrep uses `--baseline-commit`

The upstream tree carries **6 pre-existing findings** under
`p/security-audit`, all WARNING severity: five
`dynamic-urllib-use-detected` and one `avoid-pickle`. This is inherited
upstream code, not a regression from this port. A C4 baseline that lands red
teaches everyone to ignore the Security gate.

`--baseline-commit` compares against the integration branch and reports only
findings **not already present** on it. Existing debt is grandfathered; any
**new** issue still fails the build. `--severity ERROR` additionally drops the
WARNING-severity audit rules, which are advisory ("audit uses of urllib calls
to ensure user data cannot control the URLs") rather than defects.

Verified locally on this tree:

| Scenario | Result |
|---|---|
| baseline scan (`--baseline-commit origin/beta`) | 0 findings |
| a newly added file with a real `subprocess.run(..., shell=True)` call | **1 blocking finding** |

## The CI workflow's `name:` was changed

Upstream's `ci.yml` was named `Tests`. It is named `CI` here because the
MAR gate matches required workflows by **name**
(`MAR_REQUIRED_WORKFLOWS` defaults to `CI,Security`), so a workflow called
`Tests` would never satisfy the MAR evidence check and MAR could never run.
This matches `ax-trax`, `ax-gateway`, `ax-omega` and `ax-eco-context`.

## Why `scripts/` and not `.github/`

`mar-review.yml` reads `scripts/mar_gate.py` from the **base branch** via a
sparse checkout, and `mar-audit.yml` reads it from the base-branch commit.
That path is fixed by the workflow contract in SPEC-MAR-003, so the file
cannot live under `.github/` without diverging from the byte-identical
`mar-review.yml` requirement. The file is a pure addition — upstream has no
`scripts/mar_gate.py` — so an upstream sync stays clean.

## What still needs an owner

- **Secret `CLAUDE_CODE_OAUTH_TOKEN`** on `SteynSean11/ax-mempalace`. Without
  it the MAR workflow records `error` (never `success`): "MAR not run:
  CLAUDE_CODE_OAUTH_TOKEN is not set".
- **Branch protection on `beta`** once the workflows above are merged. Required
  checks: `MAR` plus the `CI` and `Security` checks, `enforce_admins: true`,
  0 required reviews — matching `ax-trax`.

## Integration branch

`beta` is the integration branch. Upstream's default branch is `develop`;
`beta` matches the other four ApexSigma repositories and is where the
ApexSigma-side work lands. Upstream's `main`/`develop` workflows are left
running untouched.
