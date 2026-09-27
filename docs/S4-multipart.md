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
