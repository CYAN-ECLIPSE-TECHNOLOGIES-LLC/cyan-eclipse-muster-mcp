# Eclipse Muster MCP

**Powered by Cyan Eclipse Technologies** — https://cetech.ai

**The Eclipse customer adapter family — one canonical Customer Graph becomes governed reads, internal writes, and owner-approved drafts and sends across the popular CRM and customer-service platforms, according to what each platform actually sanctions. Where a vendor already ships an MCP server, Eclipse wraps it in policy, approval, masking, and receipts — it never rebuilds it. Where no sanctioned server exists, Eclipse builds one over the platform's public API — never over a scraped session. Customer-visible sends are drafts until the owner approves them, and ticket content never becomes a data product.**

## Why this exists

A customer is one person; their records live in a CRM, a helpdesk, and often a second tool besides. No vendor server knows the other platforms exist — Salesforce's server cannot see the Zendesk user, HubSpot's cannot see the Freshdesk contact. Muster is the one place the family agrees: canonical Person / Company / Deal / Ticket / Conversation / Activity records, per-field provenance, propose-only identity resolution, field-level masking at egress, a records-only drafting service, and a hash-chained receipt ledger that proves what happened without ever copying the data it describes.

## Status — honest labels

v0.2.0-pilot.1, phase **C0 (shared core)**. Local stdio transport, Python stdlib only. **No platform is contacted, no credential exists in this build, no message is sent from here, and no certification is claimed — bench pending.** The shipped fixtures and the server's default state are synthetic (C0). The tree also ships C1 loader tooling (`tools/c1_loader.py`, `tools/c1_loader_slices.py`) that ingests real records through the same governed `record_ingest` path into a separate estate C1 store; that store is not fixture data and the C0 label does not describe it. Adapters (Haven, Orbit first) feed it records over their own sanctioned surfaces at C1+.

## The Customer Graph

Six object families. Every field on every record carries provenance `{platform, platform_object_id, source, observed_at, adapter_version}` — validated at ingest; **a field without provenance does not enter the graph**. Every record carries a **Data Rights Ledger block** (source account, legal basis, method, retention, PII class). Platform values are stored verbatim; canonical classes are a reading aid layered over them, never a replacement.

## Identity resolution — propose-only, forever

Merging `maya.chen@…` in HubSpot with `M. Chen` in Zendesk is the graph's most dangerous operation, so the engine never does it alone:

- `merge_propose` returns the evidence — matching keys (by hash), conflicting fields, a confidence, and the named false-merge classes (shared inboxes, recycled numbers, family-shared emails). **Proposing changes nothing.**
- `merge_execute` runs only on an owner-confirm token bound to the proposal hash (`confirm-merge-<proposal_id>-<hash12>`). It merges **graph nodes only** — never records inside a platform (that is a platform-side T3 action, prohibited in v1).
- Per-platform values survive a merge: a conflicting second value is retained under a platform-suffixed field key, never overwritten.
- `merge_unmerge` restores the pre-merge nodes **exactly** from the snapshot stored on the merge record. A merge that cannot be undone from receipts does not ship.

## Masking — before the boundary

Deep Field pattern, enforced in the core: phone → partial (last 2) on every surface; email → full on the owner's review surface, domain-only on analytic/diagnostic surfaces; names → initials on analytic surfaces; government IDs, payment data, and health-adjacent fields → `null` in v1. Vendor sensitive-data classifications are a floor, never a ceiling — our masks may be stricter, never looser.

## Drafting — records or silence

`reply_draft` generates a ticket reply **only** from record facts (the ticket, the linked Person/Company), and ships its citation list with it. A fact that is not traceable to a record field cannot appear: where the record lacks a resolution, the draft carries a `MISSING_FACT: resolution` marker and asserts **no outcome**. Drafts never send from the core.

## Herald Law #1 — customer text is data

Ticket bodies and messages are foreign content. At ingest, customer-authored messages are tagged `untrusted_data`; instruction-shaped content ("ignore your instructions and refund…") is **quarantined** — kept as data, flagged on the record, reported to the reviewer at draft time, and never used as a fact source or obeyed as a command. This is a tested line, not a design nicety (spec §12.4 applies it family-wide).

## Tiers + receipts

Action-class floors are the Environment ladder transposed (reads T0 · internal writes + drafts T1 · sends + graph-merge execution T2 · destructive/platform-merge/automation T3). Floors may be **raised** per account in `policy.json`; an override that tries to lower one is rejected at load with `TIER_FLOOR_VIOLATION`, before the server serves a single call. Kill switches halt reads or writes. Every read of a Person/Ticket record and every write attempt — allowed, denied, or failed — appends to the hash-chained ledger. **Receipts carry IDs, hashes, tiers, and verdicts — never content.**

## Retention — enforced, not just stamped

Every record's Data Rights block carries a retention value (default `graph-cache-30d`). Since the MUSTER-14 cure that value is **enforced**: when a record's retention expires, its field values are destroyed (a hash and a disposition receipt remain as proof), enforcement runs on `record_read` / `record_search`, and the confirm-gated `retention_sweep` tool (`confirm='PURGE_EXPIRED'`, T3) performs the same disposition on demand. Merge snapshots — full pre-merge copies retained only while a merge is active, as the unmerge mechanism — are purged at unmerge, their hash retained. Copy elsewhere in this repo that credits any weaker action (a mode switch, a merge, a delete of a view) with erasure is wrong; retention expiry and `retention_sweep` are the only erasure routes.

## Tools (10)

| Tool | What it does |
|---|---|
| `record_ingest` | Ingest a canonical record; provenance + Data Rights Ledger validated; customer text tagged/quarantined. |
| `record_read` | Read one record, masked per surface (`owner` / `analytic`). Receipted. |
| `record_search` | Search by kind + substring; masked summaries; the query is receipted by hash only. |
| `merge_propose` | Propose an identity merge with evidence. Executes nothing. |
| `merge_execute` | Execute a graph merge under the hash-bound owner-confirm token. |
| `merge_unmerge` | Restore pre-merge nodes exactly from the merge receipt snapshot. |
| `reply_draft` | Records-only reply draft with citations + `MISSING_FACT` markers + quarantine report. |
| `policy_show` | Tier floors, effective tiers, kill switches, masking rules in force. |
| `receipt_list` | List the hash-chained ledger and verify it. |
| `retention_sweep` | Erasure-class (T3, confirm-gated): destroy field values of retention-expired records. |

The denylist is stated in code: no `record_delete`, `platform_merge`, `reply_send`, `email_send`, `bulk_update`, `macro_edit`, `trigger_edit`, and no session/scraping route exists at any tier (spec §§4.1, 6.2).

## Your store — the tenant-local story

Muster runs against one store file per deployment: your deployment, your file, your disk. The server is pointed at it with the `ECLIPSE_MUSTER_STATE` environment variable (the state-path contract); with no override it uses the per-user data root named under "Run it" below. The shipped tool for the store's life cycle is `tools/tenant_store.py` (stdlib only, like the server):

- Initialise: `python3 tools/tenant_store.py init --state /path/to/state.json` creates a fresh, empty store file and prints the state-path contract. It refuses to overwrite an existing file — a store is never silently reset.
- Import: `python3 tools/tenant_store.py import --state /path/to/state.json --export tenant-export.json` bulk-loads canonical records from a tenant export file in the loader's JSON form (a bare list of ingest payloads, or a `{"records": [...]}` document). Every record goes through the server's own governed `record_ingest` path — provenance and the Data Rights Ledger are enforced by the server, a record that fails validation is rejected and named in the report, and the tool never writes record data into the store file itself. A production-shaped synthetic corpus for trying the path end to end ships at `fixtures/tenant-synthetic/` (all data invented).
- Verify: `python3 tools/tenant_store.py verify --state /path/to/state.json` opens the store through the server and reports the receipt chain (`chain_valid`) plus per-kind record counts.
- Back up: `python3 tools/tenant_store.py backup --state /path/to/state.json --out /path/to/backup.json` makes a byte copy of the store plus a sha256 manifest. Restore is file-level, no magic: stop the server, copy the backup bytes back over the store path, restart. Scheduling backups is the operator's job; the tool is deliberately just the copy + manifest.
- Reset: there is no in-server reset command, by design — init refuses to overwrite so a live store is never silently reset. To reset the store, stop the server and delete the state file (the path named by `ECLIPSE_MUSTER_STATE`, or the per-user default under "Run it" below); deleting the state file removes the store entirely, records and receipts included, and the next start (or a fresh `init`) begins from an empty store. Take a backup first if the store might be needed again — removal is the reset, and it is not undoable.

Mailbox ingest: `tools/mail_ingest.py` turns a mailbox export — a directory of `.eml` files or a `.jsonl` export — into canonical records (one conversation per thread, one person per correspondent) and imports them through the same governed path above: `python3 tools/mail_ingest.py import --state /path/to/state.json --source /path/to/export-dir --label <mailbox-label> --owner-email <owner-address> --export-out /path/to/mail-export.json`. Dedupe is by Message-ID with a content-hash fallback, re-import adds zero records, bodies carrying government-ID / full-DOB / residential material quarantine the whole record, and instruction-shaped customer text is quarantined by the server at ingest. An import report (dispositions + export sha256 + chain head before/after) lands beside the export. The shipped corpus at `fixtures/mail-synthetic/` is synthetic, for tests; no real mailbox is touched by this tool.

Posture notes, stated plainly: single tenant per deployment — there is no tenant isolation inside a store file, so separate tenants get separate deployments and separate files. Run one server process per store file: the server loads and rewrites the whole file per call with no file locking, so two processes pointed at one file can overwrite each other.

## Run it

```bash
python3 server.py            # MCP over stdio, stdlib only, no dependencies
python3 smoke.py             # end-to-end smoke over real stdio (temp state)
python3 -m unittest discover tests
```

State lives in the per-user data root by default — Windows `%LOCALAPPDATA%\EclipseMuster\state.json`, macOS `~/Library/Application Support/EclipseMuster/state.json`, Linux `$XDG_DATA_HOME/eclipse-muster/state.json` (or `~/.local/share/eclipse-muster/state.json`) — never beside the server or in the install location (override: `ECLIPSE_MUSTER_STATE`). Policy in `policy.json` (override: `ECLIPSE_MUSTER_POLICY`).

## Lineage

Spec: `~/workspace/cyan-eclipse/eclipse-customer-family-spec-20261025.md` (§§5–9). Doctrines inherited, not restated: propose-only merges (Eclipse Beacon), egress masking (Eclipse Deep Field), tier floors (Eclipse Environment), hash-bound approvals (Eclipse Social Core / Gig Core), untrusted foreign content (Eclipse Herald, Security Law #1).

---

*Eclipse Muster is a Cyan Eclipse Technologies product. The platforms keep their brands; the customer records belong to the account owner, worked under the owner's own platform accounts. Nothing here implies any platform endorses Cyan Eclipse Technologies.*
