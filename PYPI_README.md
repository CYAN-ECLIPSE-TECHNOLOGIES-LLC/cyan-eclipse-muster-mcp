# eclipse-muster

mcp-name: io.github.CYAN-ECLIPSE-TECHNOLOGIES-LLC/muster

**What this is:** the MCP server for **Eclipse Muster** — the shared
core of Cyan Eclipse Technologies' Eclipse customer adapter family.
One canonical Customer Graph becomes governed reads, internal writes,
and owner-approved drafts across the popular CRM and customer-service
platforms, according to what each platform actually sanctions.

**What this is not:** phase C0 is a local core over owner-fed records;
its fixtures and default state are synthetic. The source tree also
ships C1 loader tooling (`tools/c1_loader.py`,
`tools/c1_loader_slices.py`) used estate-side to ingest real records
through the same governed `record_ingest` path into a separate C1
store — that store is not the C0 fixture state and is not served as
fixture data. **No platform is contacted by the server, no credential
exists in this build, and no message is sent from here** — sends are
adapter tools gated by owner approval tokens, elsewhere.

## Run it

```console
$ pip install eclipse-muster
$ eclipse-muster             # MCP over stdio, Python stdlib only
```

MCP client config:

```json
{ "mcpServers": { "eclipse-muster": { "command": "uvx", "args": ["eclipse-muster"] } } }
```

## Tools (10)

`record_ingest` · `record_read` · `record_search` · `merge_propose` ·
`merge_execute` · `merge_unmerge` · `reply_draft` · `policy_show` ·
`receipt_list` · `retention_sweep`

## Honest scope

- Every field on every record carries provenance and every record a
  Data Rights Ledger block; a field without provenance does not enter
  the graph. Identity merges are **propose-only** — a merge executes
  only on an owner-confirm token bound to the proposal hash, and can
  be unmerged exactly from its receipt.
- Field-level masking is enforced at egress; reply drafts are
  generated only from record facts with citations — untraceable facts
  surface as `MISSING_FACT` markers. Instruction-shaped customer text
  is quarantined at ingest (Herald Security Law #1): data, never
  commands. Receipts carry IDs, hashes, tiers, and verdicts — never
  content.
- Policy floors live in `policy.json`, shipped beside the server in
  the wheel (override with `ECLIPSE_MUSTER_POLICY`); state persists in
  the per-user data root (`EclipseMuster/state.json` under
  `%LOCALAPPDATA%` on Windows, `~/Library/Application
  Support/EclipseMuster/state.json` on macOS,
  `$XDG_DATA_HOME/eclipse-muster/state.json` or
  `~/.local/share/eclipse-muster/state.json` on Linux; override
  with `ECLIPSE_MUSTER_STATE`) — never beside the installed server.
  Reset route: there is no in-server reset command; to reset, stop
  the server and delete the state file — removing the store file
  removes the store entirely, and the next start begins from an
  empty store. Take a backup first; removal is not undoable.
- Bench pending — no certification is claimed. The listing status
  in the Eclipse Bazaar catalog is in-development. (Earlier copies
  of this readme claimed a local certification; that claim was
  withdrawn as contradictory in the MUSTER-10 disclosure cure,
  2026-11-13.)

Powered by Cyan Eclipse Technologies LLC (cetech.ai) — a Veteran-Owned
Small Business. MIT licensed.
