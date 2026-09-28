# Chroma audit exception — approved, expires 28 October 2026

Reviewed 28 September 2026. The owner approved enabling this exact exception and
pushing it on 29 September 2026 (Asia/Kolkata). `approved` is true in
`python-audit-policy.json`; the scope and expiry below are unchanged.

## Exact scope

Only `chromadb==1.5.9`, with adapter `langchain-chroma==1.1.0`, and these IDs:

| pip-audit ID | Advisory | Reviewed condition |
|---|---|---|
| PYSEC-2026-311 | [CVE-2026-45829](https://github.com/advisories/GHSA-f4j7-r4q5-qw2c) | Server-side collection creation with attacker-chosen embedding model configuration |
| PYSEC-2026-3814 | [CVE-2026-45833](https://www.hiddenlayer.com/sai-security-advisory/2026-06-chromadb-5) | Server-side collection updates accepting remote-code model configuration |
| PYSEC-2026-3815 | [CVE-2026-45831](https://github.com/advisories/GHSA-xph7-9rjv-w5fr) | Chroma SimpleRBAC tenant/database/collection scope checks |
| PYSEC-2026-3813 | [CVE-2026-45830](https://github.com/advisories/GHSA-2wm9-hf6c-p5cr) | Chroma server collection authorization |

The exception would expire at **00:00 UTC on 28 October 2026** (30 days after
review). There is no automatic renewal. New IDs, another package/version, a
published fix, malformed/incomplete reports and scanner errors still fail.
The audit retains all findings in its raw JSON report, including accepted ones.

## Repository evidence and changes

- `ai-service/Dockerfile` starts `uvicorn main:app`. Compose defines no Chroma
  service. Render declares the AI application as a private service. This is a
  review of repository configuration, not a live deployment/network attestation.
- `main.py` registers application processing/chat endpoints, not Chroma's server
  router. Requests do not supply embedding functions, model names or Chroma
  collection configuration to the store.
- `utils/chroma_local.py` constructs a `PersistentClient` with the Rust embedded
  API explicitly. It rejects remote/server API environment settings and disables
  server/client auth provider selection, telemetry and reset in its settings.
- `core/vector_store.py` passes that client explicitly to the adapter. The
  reviewed adapter calls `get_or_create_collection` with `embedding_function=None`;
  embeddings are calculated by our fixed HuggingFace model with
  `trust_remote_code=False`. Model selection is not an API field.
- Job IDs are validated and storage paths remain under the configured root;
  collections/directories are per job. Express performs user ownership checks.
  Chroma's server RBAC is not our authentication boundary.
- Boundary regression checks use actual embedded storage with deterministic
  embeddings, cover reopening/isolation and reject remote configuration. They
  do not download models or access existing user indexes.

## Limits and invalidation conditions

The vulnerable dependency is **not patched**. Local disk/process compromise,
malicious modification of persisted indexes, unrelated vulnerabilities, and
future code changes are outside this assessment. Existing storage must be private
to the application account. No full penetration test or deployment inspection
was performed.

Reassess before introducing `chroma run`, a Chroma FastAPI server, HTTP/cloud
clients, server RBAC, user-controlled model/embedding configuration, remote model
code, shared storage across untrusted users, a dependency upgrade, or a public
deployment with different boundaries. Prefer a patched release when available.

## Approval and verification

The single `approved` flag was enabled after explicit owner approval. No CLI `--ignore-vuln` or blanket
`continue-on-error` is used. Passing means the reviewed policy is satisfied, not
that the package has zero vulnerabilities.

```sh
python -m unittest discover -s security/tests -v
cd ai-service
python -m unittest discover -s tests -p test_chroma_boundary.py -v
```

The full requirements scan is run by `python security/audit_python.py` from the
repository root with pip-audit installed. It succeeds only when every finding is
covered by the approved, unexpired policy and the full scan completed successfully.

Local verification: **50 AI-service tests** and **6 policy tests** passed. The
storage test read a temporary index created through the previous persistence
API, wrote/retrieved another job, and verified their isolation while Python
socket connections were blocked. A policy evaluation against the last GitHub
audit report rejected all four unique advisory IDs with approval false; an
in-memory approval simulation accepted exactly those four. The committed
approval flag was not changed by the simulation. No existing user index was read
or modified, and no model download or paid provider call was made.

A fresh full requirements scan with the pinned versions reported only ChromaDB:
five entries (four unique IDs, due to a duplicate). The proposed gate correctly
returned exit code 1 with approval false. Raw findings remain in the local
generated `python-audit.json` (Git-ignored). This scan used local Python 3.13;
GitHub's Python 3.11 run remains to be verified after approval/push.
