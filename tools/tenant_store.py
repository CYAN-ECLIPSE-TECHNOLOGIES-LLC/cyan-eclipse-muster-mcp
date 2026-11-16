#!/usr/bin/env python3
# PROPRIETARY — CYAN ECLIPSE TECHNOLOGIES LLC · COMPANY INTELLECTUAL PROPERTY
# Eclipse Muster MCP — tenant store tool (tools/tenant_store.py)
# Copyright © 2026 Cyan Eclipse Technologies LLC · cetech.ai
"""(IP — PROPRIETARY IP) Tenant-local store path for Cyan Eclipse Muster.

The shipped product story for a tenant's own store file. Four
subcommands, stdlib only (MUS-GEN-003 holds for this tool too):

  init    create a fresh, empty Muster store file at a tenant path
          and report the state-path contract (the server is pointed
          at the file with ECLIPSE_MUSTER_STATE). Refuses to
          overwrite an existing file — a store is never silently
          reset.
  import  bulk-load canonical records from a tenant export file in
          the loader's JSON form (a bare list of record_ingest
          payloads, or {"records": [...]}) through the server's own
          governed record_ingest path, over stdio JSON-RPC.
          Provenance + Data Rights Ledger are enforced by the
          server; a record that fails validation is REJECTED, named
          in the report, and never written. This tool never writes
          record data into the store file itself — the only writer
          is the server.
  verify  open the store through the server: receipt_list chain
          verification + per-kind record counts (via record_search).
  backup  byte copy of the store file + a sha256 manifest beside
          it. Restore is file-level, no magic: stop the server,
          copy the backup bytes back over the store path, restart.

Posture: one store file per deployment (single tenant per
deployment). There is no tenant isolation inside a store file and
no backup scheduling here — scheduling is the operator's.
Concurrency: the server loads and rewrites the whole store file
per call with no file locking; run ONE server process per store
file. Two processes pointed at one file can overwrite each other.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
DEFAULT_SERVER = ROOT / "server.py"
EMPTY_STATE = {"records": {}, "proposals": {}, "merges": {},
               "drafts": {}, "receipts": []}
KINDS = ("person", "company", "deal", "ticket", "conversation",
         "activity")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Session:
    """One stdio JSON-RPC session against the Muster server,
    pointed at a tenant store via ECLIPSE_MUSTER_STATE."""

    def __init__(self, server_py: Path, state: Path):
        env = dict(os.environ)
        env["ECLIPSE_MUSTER_STATE"] = str(state)
        self.proc = subprocess.Popen(
            [sys.executable, str(server_py)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, cwd=str(server_py.parent), env=env)
        self.rid = 0
        self.call("initialize",
                  {"protocolVersion": "2025-03-26"})

    def call(self, method, params=None):
        self.rid += 1
        req = {"jsonrpc": "2.0", "id": self.rid, "method": method}
        if params is not None:
            req["params"] = params
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError(f"{method}: server closed the stream")
        resp = json.loads(line)
        if "error" in resp:
            raise RuntimeError(f"{method}: {resp['error']}")
        return resp.get("result")

    def tool(self, name, args):
        result = self.call("tools/call",
                           {"name": name, "arguments": args})
        out = json.loads(result["content"][0]["text"])
        return out, bool(result.get("isError"))

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        finally:
            self.proc.stdout.close()


def state_contract(state: Path) -> str:
    return (f"ECLIPSE_MUSTER_STATE={state} "
            f"python3 server.py  # MCP over stdio")


def do_init(state: Path) -> dict:
    if state.exists():
        raise SystemExit(
            f"refusing to initialise over an existing file: {state} "
            f"(a store is never silently reset; pick a new path or "
            f"back the file up and remove it yourself first)")
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps(EMPTY_STATE, indent=1),
                     encoding="utf-8")
    return {"created": True, "state_path": str(state),
            "bytes": state.stat().st_size,
            "sha256": sha256_file(state),
            "records": 0, "receipts": 0,
            "state_path_contract": state_contract(state)}


def load_export(export: Path) -> list:
    doc = json.loads(export.read_text(encoding="utf-8"))
    if isinstance(doc, dict):
        doc = doc.get("records")
    if not isinstance(doc, list):
        raise SystemExit(
            f"export file is not a payload list or "
            f"{{'records': [...]}} document: {export}")
    return doc


def do_import(server_py: Path, state: Path, export: Path) -> dict:
    if not state.exists():
        raise SystemExit(
            f"store does not exist: {state} (run init first)")
    payloads = load_export(export)
    session = Session(server_py, state)
    ingested, rejects = [], []
    try:
        for payload in payloads:
            rid = payload.get("record_id") if isinstance(
                payload, dict) else None
            out, is_err = session.tool("record_ingest", payload)
            if is_err:
                rejects.append({"record_id": rid,
                                "error": out.get("error")})
            else:
                ingested.append({"record_id": out["record_id"],
                                 "kind": out["kind"],
                                 "receipt_seq": out["receipt_seq"],
                                 "quarantined_message_ids":
                                     out.get(
                                         "quarantined_message_ids",
                                         [])})
    finally:
        session.close()
    counts = {}
    for row in ingested:
        counts[row["kind"]] = counts.get(row["kind"], 0) + 1
    return {"state_path": str(state), "export": str(export),
            "attempted": len(payloads), "ingested": len(ingested),
            "counts": counts, "rejects": rejects,
            "receipts": ingested}


def do_verify(server_py: Path, state: Path) -> dict:
    if not state.exists():
        raise SystemExit(f"store does not exist: {state}")
    session = Session(server_py, state)
    try:
        ledger, is_err = session.tool("receipt_list", {})
        if is_err:
            raise SystemExit(f"receipt_list failed: {ledger}")
        counts = {}
        for kind in KINDS:
            hits, is_err = session.tool(
                "record_search",
                {"kind": kind, "query": "", "surface": "analytic"})
            if is_err:
                raise SystemExit(f"record_search failed: {hits}")
            counts[kind] = hits["count"]
    finally:
        session.close()
    return {"state_path": str(state),
            "chain_valid": ledger["chain_valid"],
            "chain_divergence_seq": ledger["chain_divergence_seq"],
            "receipt_count": ledger["count"],
            "counts": counts,
            "total_records": sum(counts.values())}


def do_backup(state: Path, out: Path) -> dict:
    if not state.exists():
        raise SystemExit(f"store does not exist: {state}")
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(state, out)
    manifest = {
        "source_state_path": str(state),
        "backup_path": str(out),
        "bytes": out.stat().st_size,
        "sha256": sha256_file(out),
        "created_at": now_iso(),
        "restore": ("stop the server, copy the backup file back "
                    "over the store path byte-for-byte, restart the "
                    "server (file-level restore; no other step)"),
    }
    manifest_path = out.with_name(out.name + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=1),
                             encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.add_argument("--state", required=True)

    p = sub.add_parser("import")
    p.add_argument("--state", required=True)
    p.add_argument("--export", required=True)
    p.add_argument("--server", default=str(DEFAULT_SERVER))

    p = sub.add_parser("verify")
    p.add_argument("--state", required=True)
    p.add_argument("--server", default=str(DEFAULT_SERVER))

    p = sub.add_parser("backup")
    p.add_argument("--state", required=True)
    p.add_argument("--out", required=True)

    args = ap.parse_args(argv)
    if args.cmd == "init":
        print(json.dumps(do_init(Path(args.state)), indent=1))
        return 0
    if args.cmd == "import":
        report = do_import(Path(args.server), Path(args.state),
                           Path(args.export))
        print(json.dumps(report, indent=1))
        return 2 if report["rejects"] else 0
    if args.cmd == "verify":
        report = do_verify(Path(args.server), Path(args.state))
        print(json.dumps(report, indent=1))
        return 0 if report["chain_valid"] else 2
    if args.cmd == "backup":
        print(json.dumps(do_backup(Path(args.state), Path(args.out)),
                         indent=1))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
