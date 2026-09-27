# S4: multipart handling migration

Scope: upgrade Multer and verify the S3 upload contract, then add dependency
auditing for both Node lockfiles and the Python dependency graph.

## Ten commit-sized phases

1. Record the migration and acceptance plan.
2. Pin Multer 2.4.0 and regenerate the server lockfile.
3. Adapt the inclusive file-size limit to Multer 2.4 semantics.
4. Return safe, stable errors for malformed multipart requests.
5. Exercise malformed multipart and hostile field names over HTTP.
6. Harden storage cleanup when clients disconnect.
7. Verify admission recovery and forwarding ownership after failures.
8. Audit both Node lockfiles in CI and run upload regressions.
9. Resolve and audit Python dependencies in CI.
10. Record final verification and remaining boundaries.

## Release selection

Checked 27 September 2026: npm reports 2.4.0 as latest. The maintainer
[advisories](https://github.com/expressjs/multer/security/advisories) and
[changelog](https://github.com/expressjs/multer/blob/main/CHANGELOG.md) identify
fixes after 2.1.1, including aborted-upload cleanup and field-name handling.
Pin 2.4.0 rather than treating the old review's minimum as sufficient.

Migration checks: file size is now inclusive; parts limits are also inclusive;
new parser errors include INVALID_FIELD_NAME and STREAM_DESTROYED. Keep the
custom streaming storage engine and test its abort/error lifecycle.

## Acceptance

- Valid uploads remain on disk and survive the early response until forwarding ends.
- Exact-limit files succeed; one-byte-over and chunked oversized files fail.
- Malformed fields/boundaries terminate with safe 4xx responses, not crashes.
- Interrupted writes close before removal; upload slots become reusable.
- Existing source restrictions, media filters and quotas remain intact.
- CI audits server/client locks and resolved Python dependencies, with visible
  failures and reports; no blanket vulnerability suppression.

Dependency audit findings unrelated to Multer are reported for follow-up rather
than automatically applying unrelated breaking upgrades. Full AI processing,
durable workers, S5 general schema validation and S6 acceptance are separate work.

## Implemented behavior

Multer is pinned to 2.4.0 in the manifest and lockfile. The file limit is passed
directly (Multer now handles the Busboy boundary offset). Multipart nesting and
numeric array indexes are bounded at zero because submission fields are scalar.
The existing four-field/five-part envelope and supported media filters remain.

The upload boundary converts Multer and recognized malformed-form errors to
safe 400/413 responses. Unexpected storage failures remain 500 with a generic
message; unsupported media remains 415. Filenames and filesystem paths are not
reflected in these parser/storage error responses.

The custom disk engine exposes its owned path to Multer's pending-file cleanup
and waits for the write pipeline to close before unlinking. Tests cover aborts
before/during writes, malformed completed bodies, admission reuse, per-user and
global limits, and retaining files after early acceptance until forwarding ends.

## Verification on 27 September 2026

| Check | Result |
|---|---|
| Server `npm test` | 15 passed; 1 optional MongoDB integration test skipped |
| Python `python -m unittest discover -s tests -v` | 44 passed |
| Client `npm run build` | Passed |
| Two simultaneous 64 MiB uploads | RSS 51.9 to 89.9 MiB; growth 38 MiB, below 96 MiB guard |
| Workflow YAML and audit command checks | Passed |
| Manifest/lockfile Multer agreement | 2.4.0 |
| Server dependency audit | 4 findings: 3 moderate, 1 high; none reported for Multer |
| Client dependency audit | 6 findings: 4 moderate, 2 high |
| Installed Python environment audit | 158 packages checked; 18 reported vulnerability entries across 3 packages (includes repeated advisory IDs) |

The server findings concern brace-expansion and qs (also affecting Express and
body-parser). Client findings concern esbuild/Vite, nanoid, PostCSS and React
Router. These are audit snapshots, not a full exploitability assessment. The
suggested complete client repair includes a major Vite migration. They remain
visible follow-up work; the audit gate is expected to fail until resolved.

The separate local Python audit used `--path ai-service/.venv/Lib/site-packages`
from an isolated temporary tooling environment. It reported ChromaDB 1.5.9
(5 entries), deep-translator 1.11.4 (1), and pip 24.3.1 (12). Counts include
duplicate advisory IDs returned by the service. This checks the installed
Python 3.13 environment; it does not claim to have executed CI's fresh Python
3.11 requirements resolution. The raw report is in the local temporary file
`reel-s4-python-audit.json`. No installed AI dependencies were modified.

## CI and repeatable commands

`.github/workflows/dependency-security.yml` runs on PRs, pushes to main, a weekly
schedule and manual dispatch. Independent jobs run Node upload tests on Windows
and Linux, audit both Node lockfiles including development dependencies, and
resolve/audit the full Python 3.11 requirements graph with pip-audit 2.10.1.
Reports are retained for 14 days even after findings. No advisories are ignored.

From either Node package: `npm audit --package-lock-only --audit-level=low`.
From the repository root using isolated audit tooling:

```sh
python -m pip install pip-audit==2.10.1
python -m pip_audit -r ai-service/requirements.txt --strict --progress-spinner off
```

Python requirements use broad lower bounds; resolving them in CI audits today's
graph, not a locked deployment. A Python lock remains E2. Dependency resolution
may download large ML wheels. Resolution/network failures fail the job rather
than being treated as a clean audit. See [pip-audit](https://github.com/pypa/pip-audit).

GitHub-hosted CI/Linux execution has not been run from this local session. No
paid AI request, real-video end-to-end run, or deployment was performed. The
memory check measures upload transport, not the full inference service.
