PROPRIETARY — CYAN ECLIPSE TECHNOLOGIES LLC · COMPANY INTELLECTUAL PROPERTY

# (INT — INTERNAL) BENCH REQUEST — DELTA: Muster production data path (tenant-local store)

## Header

- Subject: eclipse-muster-mcp — delta bench for the shipped tenant-local production data path only (tools/tenant_store.py + synthetic tenant dataset + README "Your store" section + tenant-store tests). The server core, its 10 tools, and version fields are NOT changed by this delta.
- Filed: 2026-11-16 · Lane: Muster production data path + surface proof lane (program muster-production-readiness-20261116, Slate MUSTER-27 / tkt-cf46a9738484).
- Base: main 5edc2f2709c04a3e10a3d4d8340c7f8b390204b1 (v0.1.1, suite 66 OK). Delta lives on branch lane/production-data-path-20261116, NOT merged to main — the rebench target (bench-staging tar of 5edc2f2) is byte-pinned and untouched by this filing.
- Gap answered: STATE-CHECK.md data posture — an installer at 0.1.1 gets C0 fixtures and no load story; the C1 loader is estate-side only. This delta ships the tenant-local story: init / import (governed record_ingest only) / verify / backup-restore, plus a production-shaped synthetic dataset.
- Evidence: program evidence/SURFACE-TRANSCRIPT-20261116.txt (sha256 dfb3b0dc3508537360f05f6846f45c8296773321219d145e608187d8a54e28f2) — full request/response transcript: staged 0.1.1 artefact handshake + production-path exercise (init, import 50 + named reject, masking both surfaces, merge propose/execute/unmerge byte-restore, reply_draft MISSING_FACT + quarantine, retention sweep refusal + confirmed disposition of exactly one record, receipt chain valid, restart persistence). Suite on base+delta: 72 tests OK (66 inherited + 6 new tenant-store tests).
- Claims: 4 (claim sheet appended). No certification language is used or requested; no price; no publish.

CONTRACT: PENDING — for Verity to run a delta bench on the production data path claims D1–D4 below against branch lane/production-data-path-20261116, tied to the transcript evidence named above; the filer claims no verdict.

## What is being benched

Delta only: (1) tools/tenant_store.py — init refuses overwrite, import loads loader-form JSON exports exclusively through the server's governed record_ingest (provenance + Data Rights Ledger enforced server-side, rejects named and unwritten, tool never writes record data itself), verify reports receipt-chain validity + per-kind counts through the server, backup is a byte copy + sha256 manifest with file-level restore; stdlib only. (2) fixtures/tenant-synthetic/ — 51 invented records (50 main + 1 retention-expired imported separately): person 13, company 10, deal 8, ticket 8, conversation 6, activity 6; example domains, fictional names, 555 phones, placeholder gov_id; merge pair, quarantined instruction-shaped customer message, draft ticket with no resolution. (3) README "Your store" section. (4) tests/test_tenant_store.py — 6 tests.

## (INT — INTERNAL) QA-pattern conformance

| Pattern | Verdict | Evidence or N-A reason |
|---|---|---|
| P1 — Masked modes close every ingress | CLEAR (delta scope) | Import is the only new ingress and it routes through record_ingest; transcript B8: analytic email domain-only, name initials, phone last-2, gov_id null; owner full email/name, gov_id still null |
| P2 — Disclosure matches build | CLEAR | README "Your store" traces 1:1 to tenant_store subcommands; no certification, no price; suite P2/P13 tests still green on the branch |
| P3 — Save/close basics | CLEAR (store form) | Init/import durability + restart persistence proven in transcript B15 and test_persistence_across_server_restart; backup/restore byte-copy proven in test_backup_manifest_and_restore |
| P4 — AI content attributed | CLEAR | reply_draft on tenant data carries the standing AI attribution (server behaviour, exercised in transcript B10) |
| P5 — Version discipline | CLEAR | Delta touches no version field; VERSION/pyproject/server.json/server.py all still 0.1.1; branch is a delta on the pinned 5edc2f2, not a release |
| P6 — Integrity warnings prominent | CLEAR | Verify surfaces chain_valid + divergence seq from receipt_list (server MUSTER-13 behaviour, exercised B14/B15) |
| P7 — Erasure-class honesty | CLEAR | Transcript B12/B13: sweep refuses without PURGE_EXPIRED (zero dispositioned), confirmed run dispositions exactly the one expired record, byte-level |
| P8 — Preview equals apply | CLEAR | Merge on tenant data goes through the standing single compute path (MUSTER-15); unmerge byte-restore hash-equal for both records (transcript B9) |
| P9 — Sealgate (Mac) | N-A | No Mac artefact in this delta (MCP server distribution) |
| P10 — Launchgate (Windows) | CLEAR (MCP form) | Transcript section A: initialize + tools/list against the staged 0.1.1 artefact bytes; section B sessions launch the branch server the same way |
| P11 — Dependency completeness | CLEAR | tenant_store.py imports stdlib only (argparse, hashlib, json, os, shutil, subprocess, sys, datetime, pathlib) — MUS-GEN-003 holds |
| P12 — Runtime-state paths | CLEAR | Store path is explicit (--state / ECLIPSE_MUSTER_STATE contract); init creates only the named file; tests run stores in temp dirs outside the tree |
| P13 — Claim-surface binding | CLEAR | Every claim below traces to the transcript file + its sha256, the suite run, or the dataset files on the branch |
| P14 — Refusal-path quality | CLEAR | Transcript: init-overwrite refusal (test), PROVENANCE_MISSING reject named (B3), CONFIRM_TOKEN_INVALID merge refusal (B9), CONFIRM_REQUIRED sweep refusal (B12) — each named, receipted where the server receipts, recoverable in-session |
| P15 — Key custody + journal disclosure | CLEAR | No credential exists in this path; owner-confirm tokens derived, never stored (standing P15 tests green); receipts content-free |

## Named gaps / not claimed

- Multi-tenant: one store file per deployment; no tenant isolation inside a store. Not claimed.
- Backup scheduling: operator's; the tool is file-level copy + manifest only.
- Concurrency: no file locking in the server — one server process per store file; two processes on one file can overwrite each other. Stated in README + tool docstring, not cured by this delta.
- Desktop faces (MUSTER-23): separate GAPs, platform-gated — see program SURFACE-PROOF.md. Not part of this delta.

## Claim sheet (standard form)

```claims
[
  {"id": "D1", "statement": "tools/tenant_store.py on branch lane/production-data-path-20261116 provides init/import/verify/backup for a tenant-local Muster store, stdlib only, and import writes records exclusively through the server's governed record_ingest path (no direct store writes by the tool)", "evidence": "eclipse-muster-mcp/tools/tenant_store.py (branch); transcript SURFACE-TRANSCRIPT-20261116.txt sections B1-B5 (program evidence folder)", "verify": "read + recompute", "class": "behaviour"},
  {"id": "D2", "statement": "The shipped synthetic tenant dataset (fixtures/tenant-synthetic/, 51 invented records across all six kinds with full provenance + Data Rights Ledger blocks) imports with zero rejects on the main file (50: person 12, company 10, deal 8, ticket 8, conversation 6, activity 6), and a record without provenance is rejected by name (PROVENANCE_MISSING) and not written", "evidence": "fixtures/tenant-synthetic/records.json + expired.json; transcript B2/B3; tests/test_tenant_store.py", "verify": "recompute", "class": "behaviour"},
  {"id": "D3", "statement": "On the tenant store, the standing machinery operates end-to-end as transcripted: masking differs correctly by surface with gov_id null on both, merge propose/execute/unmerge restores both records byte-identical (hash-equal), reply_draft emits MISSING_FACT for the absent resolution and lists + excludes the quarantined message, retention_sweep refuses without the confirm string and on confirmation dispositions exactly the one expired record, receipt chain valid before and after a server restart with per-kind counts unchanged", "evidence": "evidence/SURFACE-TRANSCRIPT-20261116.txt sections B7-B15, sha256 dfb3b0dc3508537360f05f6846f45c8296773321219d145e608187d8a54e28f2", "verify": "recompute", "class": "behaviour"},
  {"id": "D4", "statement": "Scope honesty: this delta claims the tenant-local data path only — no multi-tenant isolation, no backup scheduling, no concurrency/locking cure, no desktop-face proof, no certification, no price", "evidence": "README 'Your store' section + this request's Named gaps; program SURFACE-PROOF.md + DATA-PATH-RECORD.md", "verify": "read", "class": "methodology"}
]
```

© Cyan Eclipse Technologies LLC — Proprietary · Company Intellectual Property
