#!/usr/bin/env python3
# Eclipse Muster MCP — licence gate (MUSTER-26 licence leg, 2026-11-16).
# PROPRIETARY — CYAN ECLIPSE TECHNOLOGIES LLC · COMPANY INTELLECTUAL PROPERTY

"""Cyan Eclipse Muster — the License Server licence gate.

Every sellable Cyan Eclipse application carries a licence gate
against the firm's License Server (house pattern: Folio L-3
licence_gate.py · Loom ETO-22 server/src/licence.ts · Plumb PLU-16).
Muster's gate guards ONE named paid boundary:

    PAID_BOUNDARY_FEATURE = "graph_mutation"

Boundary (owner-revisable; rationale mirrors the ETO-22 ruling):
GATED = the write-class operations — record_ingest (internal_write),
reply_draft (draft_create), merge_propose (internal_write),
merge_execute / merge_unmerge (graph_merge_execute) and
retention_sweep (destructive). These are the operations that create
or destroy value in the graph; they are the priced surface.
NEVER GATED: record_read and record_search (reads are preserved in
every licence state, the S12 posture every other product ships),
policy_show and receipt_list (the diagnostic surfaces an operator
needs in order to SEE a refusal and verify the receipt chain that
recorded it — gating them would hide the gate's own evidence).

Arming: the gate exists only when configuration names it — the
environment key ECLIPSE_MUSTER_LICENCE_STATE (path of the licence
state file), or a "licence" block with a "state_path" in
policy.json. ECLIPSE_MUSTER_LICENCE_URL / the block's "server_url"
override the License Server base URL. Unconfigured installs behave
exactly as they did before this leg (the server module holds no gate
at all). Armed but holding no licence artefact, the gated path
refuses with the named state LICENSE_NOT_CONFIGURED: a typed
refusal, never a crash, zero silent fallbacks.

Posture (License Server SPEC S12, model B — identical across
products): activation-once binding a client-generated install_id
(random UUID in a 0600 local state file; no hardware identifier is
read or transmitted) · check-in presents activation token +
artefact hash only · a successful check is reused for a 4-hour
heartbeat · server unreachable inside 72h of the last good check-in
-> LICENSE_GRACE (gated features continue) · past 72h ->
LICENSE_SUSPENDED_OFFLINE (new work suspends; existing work stays
readable) · revocation effective at the answering check-in, never
overridden by grace · a never-activated install has no offline path.

Key custody (QA-PATTERNS P15): the licence artefact + activation
token live ONLY in the operator's licence state file (0600, outside
the source tree — the per-user data root class of path). No key
material is committed to this repository, baked into any artefact,
or carried in any receipt (refusals receipt IDs + states only).

Stdlib only (MUS-GEN-003): urllib for the License Server HTTP face.
"""
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

# The one gated feature. Owner-revisable; see the header.
PAID_BOUNDARY_FEATURE = "graph_mutation"

# This application's product id on the License Server.
LICENCE_PRODUCT_ID = "muster"

# Default License Server HTTP face (loopback, per LIC-SPEC S12).
DEFAULT_LICENCE_SERVER_URL = "http://127.0.0.1:8799"

# A successful server check is reused for this long (seconds).
HEARTBEAT_S = 4 * 60 * 60

# Offline window from the last successful check-in (seconds).
GRACE_S = 72 * 60 * 60

INSTALL_LABEL = "Cyan Eclipse Muster"


class LicenceTransportError(Exception):
    """The License Server could not be reached or did not answer
    intelligibly. Folds into the offline posture at the gate; never
    surfaces as an untyped crash."""


class LicenceRefusal(Exception):
    """A typed gate refusal. Carries the named state."""

    def __init__(self, state: str, detail: str):
        super().__init__(f"licence gate refused ({state}): {detail}")
        self.state = state
        self.detail = detail


class LicenceDecision:
    def __init__(self, allowed: bool, state: str, detail: str):
        self.allowed = allowed
        self.state = state
        self.detail = detail

    def __repr__(self):
        return (f"LicenceDecision(allowed={self.allowed!r}, "
                f"state={self.state!r}, detail={self.detail!r})")


class HttpLicenceClient:
    """Production client: POST /activate + POST /checkin against the
    License Server HTTP face, JSON in / JSON out (the face the
    ETO-22 record documents). Tests inject a fake with the same
    two methods."""

    def __init__(self, base_url: str = DEFAULT_LICENCE_SERVER_URL,
                 timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) \
                    as resp:
                status = resp.status
                text = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            status = exc.code
            text = exc.read().decode("utf-8", "replace")
            if status < 500:
                try:
                    return json.loads(text)
                except ValueError:
                    pass
            raise LicenceTransportError(
                f"licence server {path}: HTTP {status}")
        except (urllib.error.URLError, OSError) as exc:
            raise LicenceTransportError(
                f"licence server {path}: unreachable ({exc})")
        try:
            parsed = json.loads(text)
        except ValueError:
            raise LicenceTransportError(
                f"licence server {path}: response was not JSON "
                f"(HTTP {status})")
        if status >= 500:
            raise LicenceTransportError(
                f"licence server {path}: HTTP {status}")
        if not isinstance(parsed, dict):
            raise LicenceTransportError(
                f"licence server {path}: response was not an object")
        return parsed

    def activate(self, artefact, install_id: str,
                 install_label: str) -> dict:
        out = self._post("/activate", {
            "artefact": artefact,
            "install_id": install_id,
            "install_label": install_label})
        return {"state": str(out.get("state") or "LICENSE_UNKNOWN"),
                "activation_token": out.get("activation_token")
                if isinstance(out.get("activation_token"), str)
                else None}

    def checkin(self, activation_token: str,
                artefact_hash: str) -> dict:
        out = self._post("/checkin", {
            "activation_token": activation_token,
            "artefact_hash": artefact_hash})
        return {"state": str(out.get("state") or "LICENSE_UNKNOWN")}


class LicenceGate:
    """The gate over one licence state file + one client."""

    def __init__(self, state_path, client, install_label=None,
                 now=None, heartbeat_s=None, grace_s=None):
        self.state_path = Path(state_path)
        self.client = client
        self.install_label = install_label or INSTALL_LABEL
        self._now = now or time.time
        self.heartbeat_s = HEARTBEAT_S if heartbeat_s is None \
            else heartbeat_s
        self.grace_s = GRACE_S if grace_s is None else grace_s

    # ------------------------------------------------ state file
    def _read_state(self):
        try:
            parsed = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(parsed, dict) or "artefact" not in parsed \
                or not isinstance(parsed.get("artefact_hash"), str):
            return None
        return parsed

    def _persist(self, state: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_name(
            f"{self.state_path.name}.tmp-{os.getpid()}")
        tmp.write_text(json.dumps(state, indent=2) + "\n")
        os.replace(tmp, self.state_path)
        try:
            os.chmod(self.state_path, 0o600)
        except OSError:
            pass  # perms are best-effort on non-POSIX hosts

    # ---------------------------------------------------- the gate
    def check(self, feature: str) -> LicenceDecision:
        """Decide whether the named feature may proceed. Never
        raises for an expected refusal condition; transport failures
        fold into the offline posture."""
        if feature != PAID_BOUNDARY_FEATURE:
            return LicenceDecision(
                True, "FEATURE_NOT_GATED", "not the paid boundary")
        state = self._read_state()
        if state is None:
            return LicenceDecision(
                False, "LICENSE_NOT_CONFIGURED",
                "no licence artefact installed at the configured "
                "licence state path")
        now = self._now()
        last_at = state.get("last_checkin_at")
        if state.get("last_state") == "LICENSE_VALID" \
                and isinstance(last_at, (int, float)) \
                and now - last_at < self.heartbeat_s:
            return LicenceDecision(
                True, "LICENSE_VALID",
                "heartbeat reuse of the last check-in")
        if not state.get("install_id"):
            state["install_id"] = str(uuid.uuid4())
            self._persist(state)
        try:
            if not state.get("activation_token"):
                act = self.client.activate(
                    state["artefact"], state["install_id"],
                    self.install_label)
                state["last_state"] = act["state"]
                state["last_checkin_at"] = now
                if act["state"] == "LICENSE_VALID" \
                        and act.get("activation_token"):
                    state["activation_token"] = act["activation_token"]
                self._persist(state)
                if act["state"] == "LICENSE_VALID":
                    return LicenceDecision(
                        True, act["state"],
                        "activated with the License Server")
                return LicenceDecision(
                    False, act["state"],
                    "activation refused by the License Server")
            ci = self.client.checkin(state["activation_token"],
                                     state["artefact_hash"])
            state["last_state"] = ci["state"]
            state["last_checkin_at"] = now
            self._persist(state)
            if ci["state"] == "LICENSE_VALID":
                return LicenceDecision(
                    True, ci["state"], "check-in valid")
            return LicenceDecision(
                False, ci["state"],
                "check-in refused by the License Server")
        except Exception as exc:  # transport failure -> offline posture
            if state.get("last_state") \
                    and state["last_state"] != "LICENSE_VALID":
                return LicenceDecision(
                    False, state["last_state"],
                    "last definitive server state stands; the "
                    "server is unreachable")
            last_at = state.get("last_checkin_at")
            if state.get("activation_token") \
                    and state.get("last_state") == "LICENSE_VALID" \
                    and isinstance(last_at, (int, float)) \
                    and now - last_at <= self.grace_s:
                return LicenceDecision(
                    True, "LICENSE_GRACE",
                    "License Server unreachable; inside the 72h "
                    f"offline window — {str(exc)[:120]}")
            if not state.get("activation_token"):
                return LicenceDecision(
                    False, "LICENSE_NOT_CONFIGURED",
                    "activation could not complete and a "
                    "never-activated licence has no offline path")
            return LicenceDecision(
                False, "LICENSE_SUSPENDED_OFFLINE",
                "License Server unreachable past the 72h offline "
                "window")

    def assert_allowed(self, feature: str) -> LicenceDecision:
        """The throwing form call sites use."""
        decision = self.check(feature)
        if not decision.allowed:
            raise LicenceRefusal(decision.state, decision.detail)
        return decision


def licence_gate_from_env(env, policy=None):
    """Build the gate from configuration, or return None (gate not
    wired — behaviour identical to before this leg).

    Armed only by an explicit licence state path: the environment
    key ECLIPSE_MUSTER_LICENCE_STATE wins; a policy.json "licence"
    block's "state_path" is the fallback (the ETO-22 posture:
    arming is a configuration act, never a default). The License
    Server base URL comes from ECLIPSE_MUSTER_LICENCE_URL, else the
    policy block's "server_url", else the loopback default.
    """
    block = {}
    if isinstance(policy, dict) and isinstance(
            policy.get("licence"), dict):
        block = policy["licence"]
    state_path = (env.get("ECLIPSE_MUSTER_LICENCE_STATE") or "").strip()
    if not state_path:
        state_path = str(block.get("state_path") or "").strip()
    if not state_path:
        return None
    base = (env.get("ECLIPSE_MUSTER_LICENCE_URL") or "").strip() \
        or str(block.get("server_url") or "").strip() \
        or DEFAULT_LICENCE_SERVER_URL
    return LicenceGate(state_path, HttpLicenceClient(base))
