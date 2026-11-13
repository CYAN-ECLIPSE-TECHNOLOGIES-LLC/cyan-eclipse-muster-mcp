#!/usr/bin/env python3
# Eclipse Muster MCP — C1 loader (tools/c1_loader.py)
# Copyright © 2026 Cyan Eclipse Technologies LLC · cetech.ai
"""C1 real-data loader for the firm's own customer graph.

Two halves, deliberately separated:

  extract  reads the relationship-map index files (people + groups)
           and emits a canonical slice file of record_ingest payloads.
           The slice file is data; it stays in-estate.
  ingest   spawns the Muster server against the C1 real-data store
           (ECLIPSE_MUSTER_STATE) and issues one record_ingest call
           per payload over stdio JSON-RPC — the server's own governed
           write path. No state file is ever edited directly.
  exercise runs the C1 proof round on the real store: a masked read on
           both surfaces, a merge proposal, a refused execute without
           the owner-confirm token, an execute with it, and an unmerge
           restoring both records exactly.

The ingester refuses any state path whose filename does not carry the
c1 marker, so a C0 fixture store can never be written by this tool.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ADAPTER_VERSION = "c1-loader-1.0.0"
PLATFORM = "cyan-eclipse-relationship-map"
OBSERVED_AT = "2026-11-13T00:00:00Z"

DATA_RIGHTS = {
    "source_account": "cyan-eclipse-owner-records",
    "legal_basis": "account-owner instruction (C1 phase ruling "
                   "2026-11-13)",
    "method": "c1_loader",
    "retention": "in-estate persistent (owner records)",
    "pii_class": "strictest",
}

ENTRY_RE = re.compile(r"^- \*\*(.+?)\*\* — `([^`]+)` — (.+)$")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
HANDLE_RE = re.compile(r"@[\w]+(?:\.[\w]+)*")


def parse_index(text: str, kind: str) -> list:
    """Parse one relationship index file into ingest payloads."""
    payloads = []
    for line in text.splitlines():
        m = ENTRY_RE.match(line.strip())
        if not m:
            continue
        name, ref, summary = (g.strip() for g in m.groups())
        slug = Path(ref).stem
        fields = {"name": name, "summary": summary, "source_ref": ref}
        emails = EMAIL_RE.findall(summary)
        if emails:
            fields["emails"] = emails
        scrubbed = EMAIL_RE.sub("", name + " " + summary)
        handles = []
        for h in HANDLE_RE.findall(scrubbed):
            if h not in handles:
                handles.append(h)
        if handles:
            fields["handles"] = handles
        prov = {"platform": PLATFORM, "platform_object_id": slug,
                "source": "import", "observed_at": OBSERVED_AT,
                "adapter_version": ADAPTER_VERSION}
        payloads.append({
            "kind": kind,
            "record_id": f"{kind}-{slug}",
            "external_ids": {"relationship_map": slug},
            "fields": fields,
            "field_provenance": {f: dict(prov) for f in fields},
            "data_rights": dict(DATA_RIGHTS),
        })
    return payloads


def extract(people_index: Path, groups_index: Path) -> list:
    return (parse_index(people_index.read_text(encoding="utf-8"),
                        "person")
            + parse_index(groups_index.read_text(encoding="utf-8"),
                          "company"))


class Session:
    """One stdio JSON-RPC session against the Muster server."""

    def __init__(self, server_py: Path, state: Path):
        env = dict(os.environ)
        env["ECLIPSE_MUSTER_STATE"] = str(state)
        self.proc = subprocess.Popen(
            [sys.executable, str(server_py)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, cwd=str(server_py.parent), env=env)
        self.rid = 0
        self.call("initialize", None, raw=True)

    def call(self, method, params=None, raw=False):
        self.rid += 1
        req = {"jsonrpc": "2.0", "id": self.rid, "method": method}
        if raw:
            req["params"] = {"protocolVersion": "2025-03-26"}
        elif params is not None:
            req["params"] = params
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        resp = json.loads(line)
        if "error" in resp:
            raise RuntimeError(f"{method}: {resp['error']}")
        return resp.get("result")

    def tool(self, name, args):
        result = self.call("tools/call",
                           {"name": name, "arguments": args})
        text = result["content"][0]["text"]
        out = json.loads(text)
        return out, bool(result.get("isError"))

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)


def guard_state_path(state: Path) -> None:
    if "c1" not in state.name:
        raise SystemExit(
            f"refusing state path without the c1 marker: {state} "
            "(fixture-store protection)")


def do_ingest(server_py: Path, state: Path, slice_path: Path) -> dict:
    guard_state_path(state)
    payloads = json.loads(slice_path.read_text(encoding="utf-8"))
    session = Session(server_py, state)
    receipts = []
    try:
        for payload in payloads:
            out, is_err = session.tool("record_ingest", payload)
            if is_err:
                raise SystemExit(f"ingest failed for "
                                 f"{payload['record_id']}: {out}")
            receipts.append({"record_id": out["record_id"],
                             "kind": out["kind"],
                             "receipt_seq": out["receipt_seq"]})
    finally:
        session.close()
    counts = {}
    for r in receipts:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    return {"ingested": len(receipts), "counts": counts,
            "receipts": receipts}


def do_exercise(server_py: Path, state: Path, read_id: str,
                rec_a: str, rec_b: str) -> dict:
    guard_state_path(state)
    session = Session(server_py, state)
    out = {}
    try:
        read_analytic, _ = session.tool(
            "record_read", {"record_id": read_id, "surface": "analytic"})
        read_owner, _ = session.tool(
            "record_read", {"record_id": read_id, "surface": "owner"})
        out["read_analytic_fields"] = read_analytic["fields"]
        out["read_owner_fields"] = read_owner["fields"]

        proposal, _ = session.tool(
            "merge_propose", {"record_a": rec_a, "record_b": rec_b})
        prop = proposal["proposal"]
        out["proposal"] = {"proposal_id": prop["proposal_id"],
                           "proposal_hash": prop["proposal_hash"],
                           "confidence":
                               prop["evidence"]["confidence"],
                           "matched_keys":
                               prop["evidence"]["matched_keys"],
                           "conflicts": prop["evidence"]["conflicts"]}
        token = (f"confirm-merge-{prop['proposal_id']}-"
                 f"{prop['proposal_hash'][:12]}")

        denied, is_err = session.tool(
            "merge_execute", {"proposal_id": prop["proposal_id"],
                              "confirm_token": "confirm-merge-WRONG"})
        out["execute_without_token"] = {
            "is_error": is_err, "error": denied.get("error")}

        executed, is_err = session.tool(
            "merge_execute", {"proposal_id": prop["proposal_id"],
                              "confirm_token": token})
        if is_err:
            raise SystemExit(f"execute with token failed: {executed}")
        out["execute_with_token"] = executed

        merge_id = executed["merge_id"]
        unmerged, is_err = session.tool(
            "merge_unmerge",
            {"merge_id": merge_id,
             "confirm_token": f"confirm-unmerge-{merge_id}"})
        if is_err:
            raise SystemExit(f"unmerge failed: {unmerged}")
        out["unmerge"] = unmerged

        for rid in (rec_a, rec_b):
            # record_read refuses merged-away records, so a successful
            # read here proves the record is active again post-unmerge.
            back, is_err = session.tool(
                "record_read", {"record_id": rid, "surface": "owner"})
            out.setdefault("restored", {})[rid] = {
                "readable": not is_err and "error" not in back,
                "name": back["fields"].get("name")}

        ledger, _ = session.tool("receipt_list", {"limit": 1000})
        out["chain_valid"] = ledger["chain_valid"]
        out["receipt_count"] = len(ledger["receipts"])
        out["chain_head"] = (ledger["receipts"][-1]["hash"]
                             if ledger["receipts"] else None)
    finally:
        session.close()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("extract")
    p.add_argument("--people-index", required=True)
    p.add_argument("--groups-index", required=True)
    p.add_argument("--out", required=True)

    p = sub.add_parser("ingest")
    p.add_argument("--server", required=True)
    p.add_argument("--state", required=True)
    p.add_argument("--slice", required=True)

    p = sub.add_parser("exercise")
    p.add_argument("--server", required=True)
    p.add_argument("--state", required=True)
    p.add_argument("--read-id", required=True)
    p.add_argument("--record-a", required=True)
    p.add_argument("--record-b", required=True)

    args = ap.parse_args(argv)
    if args.cmd == "extract":
        payloads = extract(Path(args.people_index),
                           Path(args.groups_index))
        Path(args.out).write_text(
            json.dumps(payloads, indent=1, ensure_ascii=False),
            encoding="utf-8")
        counts = {}
        for pl in payloads:
            counts[pl["kind"]] = counts.get(pl["kind"], 0) + 1
        print(json.dumps({"extracted": len(payloads),
                          "counts": counts}))
    elif args.cmd == "ingest":
        print(json.dumps(do_ingest(Path(args.server), Path(args.state),
                                   Path(args.slice)), indent=1))
    elif args.cmd == "exercise":
        print(json.dumps(do_exercise(
            Path(args.server), Path(args.state), args.read_id,
            args.record_a, args.record_b), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
