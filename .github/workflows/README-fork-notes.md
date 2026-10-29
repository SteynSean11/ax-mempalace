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

## The OSV scan: what was done to make it green

The OSV scan is the part of the Security gate that needed real work. The
fork's `uv.lock` had drifted well behind upstream and carried **103
advisories**. The resolution was three steps, all in this PR.

### Step 1 — drop Python 3.9

`requires-python` moved from `>=3.9` to `>=3.10`.

Two separate failures had the same root cause. The CI matrix leg failed
with `The version '3.9' with architecture 'x64' was not found for Ubuntu
26.04` (ubuntu-latest moved to Ubuntu 26.04, where setup-python has no 3.9
build). And the OSV scan reported `urllib3 2.6.3`, `requests 2.32.5` and
`starlette 1.0.0` — the vulnerable versions are reachable in the lockfile
ONLY through the `python_full_version < '3.10'` marker, because every
patched release (`urllib3 2.8.0`, `requests 2.34.2`, `starlette 1.10.0`)
requires Python >= 3.10. Upgrading the packages alone does not help; the
marker keeps the old pins.

This is not a divergence from upstream — upstream's default branch
(`develop`) already declares `requires-python = ">=3.10"`. The fork's
`beta` was behind upstream on this.

### Step 2 — full `uv lock --upgrade`

Cleared 77 of the 103 advisories. The same shape as the C2 rounds the core
repos already ran (ax-omega #47/#50/#51, ax-trax #62/#64/#68, ax-gateway
#54, ax-eco-context #53).

### Step 3 — time-boxed `osv-scanner.toml` exceptions for the last 26

The remaining 26 cannot be fixed while this repository supports Python
>= 3.10. Each entry in `osv-scanner.toml` carries an `ignoreUntil` and a
written reason, per SPEC-CICD-001 §11.7:

| Group | Count | Why it cannot be fixed here |
|---|---|---|
| `filelock 4.1.0` | 15 | Fixed in 4.2.1, which requires Python >= 3.11. The vulnerable pin exists only under the `python_full_version < '3.11'` marker. |
| `dnspython 2.8.0` | 1 | Fixed in 2.9.0, which requires Python >= 3.11. Same marker situation. |
| `chromadb 1.5.9` | 4 (+4 aliases) | **No fixed release exists** — the OSV records carry an empty `fixed` list and 1.5.9 is the newest release on PyPI. |

The `filelock` and `dnspython` entries are **cleared the moment this
repository drops Python 3.10** (`requires-python >= 3.11`). That is a
separate decision from the C4 CI baseline port and is recorded here as a
known gap.

The four `chromadb` advisories are genuine upstream debt. The exposure is
bounded for this deployment: mempalace runs ChromaDB as a local,
single-tenant store bound to 127.0.0.1, with no HTTP server, no
multi-tenant mode and no RBAC provider enabled — the pre-authentication and
cross-tenant advisories need an exposed Chroma server, which this
deployment does not have.

Verified locally with osv-scanner 2.1.0 (the version the CI action pins):

| Scenario | Result |
|---|---|
| `osv-scanner scan --lockfile=uv.lock --config=osv-scanner.toml` | "No issues found", exit 0 |
| the same scan with one ignore removed | the advisory is reported again, exit non-zero |

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

## Known gaps

These are NOT claims that the surface is covered. They are the parts of the
baseline that could not be closed in the C4 port.

1. **The upstream lint drift is still there.** `ruff check .` reports 3
   pre-existing upstream findings (`mempalace/backends/pgvector.py:25`
   F401, `mempalace/mcp_server.py:81` F401, `mempalace/palace.py:52` E402)
   and `ruff format --check .` reports 3 more upstream files. The lint job
   is scoped to `scripts/` so the gate is meaningful rather than red on
   arrival. Fixing upstream's drift here would make the fork diverge from
   upstream on files upstream owns, and would conflict on every upstream
   sync. This lint job had never run on this fork before (the fork has zero
   workflow runs in its history), which is why the drift went unnoticed.

2. **16 OSV advisories are time-boxed, not fixed.** See "Step 3" above.
   The `filelock` and `dnspython` ones clear when Python 3.10 support is
   dropped. The four `chromadb` ones have no fixed release at any version.

3. **`deploy-docs.yml` is disabled.** The fork has no GitHub Pages site.

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
