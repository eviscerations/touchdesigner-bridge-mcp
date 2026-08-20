"""td_executor/handlers/device.py -- the consent-gated PROJECTOR device-control lane (`device_send`).

This is the FIRST tool in the surface that sends bytes OUT of TouchDesigner. It holds the same discipline
as the `pulse` action-allowlist (handlers/control.py) and the validated code lanes (glsl/expr):

  * DEFAULT-OFF CONSENT: `allow_device_control` read FRESH from arm.json; refuse before touching anything.
  * CLOSED, REVIEWED VOCABULARY: the caller/AI selects a COMMAND TOKEN from a fixed PJLink Class-1 map; the
    executor emits the exact reviewed wire body. NO caller-supplied bytes ever reach the wire (mirrors the
    _ALLOW_PULSE reviewed-set discipline -- the argument that makes `pulse` safe applies here too).
  * TARGET VALIDATION: the send target must be a writable `tcpipDAT` the user created (not bridge infra,
    not another optype).
  * AUDITED: every call (and every refusal) appends to device_audit.log.

Scope (first release): PJLink Class 1 over TCP/IP, UNAUTHENTICATED. RS-232 (serialDAT) profiles
and PJLink authentication are deliberately out of scope for this release.
PJLink replies to query commands arrive asynchronously into the tcpipDAT's own rows -- read them with
`inspect` on the DAT; device_send returns only the frame it SENT.
"""
import os
import json
import time

from td_executor import server


def _config_dir():
    return os.path.join(os.path.expanduser("~"), ".touchdesigner-bridge-mcp")


def _read_allow_device_control():
    """Read `allow_device_control` from ~/.touchdesigner-bridge-mcp/arm.json, default False if file/key
    absent. Read FRESH on every call (mirror of the allow_glsl/allow_expr consent reads); the AI cannot flip
    it (the config dir is off-limits to every mutating tool)."""
    try:
        with open(os.path.join(_config_dir(), "arm.json"), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return bool(data.get("allow_device_control", False))
    except Exception:
        return False


def _audit(record):
    """Best-effort append one line to device_audit.log in the working dir. Never blocks / raises."""
    try:
        path = os.path.join(server.working_dir(), "device_audit.log")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


# ---- PJLink Class-1 command ALLOWLIST (the ONLY bytes device_send may emit) --------------------------
# PJLink Class-1 command frame (unauthenticated): "%1<CMD> <PARAM>" terminated by CR (0x0D). The AI selects
# a TOKEN below; the executor emits the mapped body -- NEVER a caller string. Reviewed, closed set (mirrors
# control._ALLOW_PULSE). Control tokens change projector state; query tokens (body ends '?') are read-only.
# PJLink input codes: 1x=RGB, 2x=Video, 3x=Digital (HDMI/DP/SDI), 4x=Storage, 5x=Network; x = 1..9.
# AVMT: 30=mute off (showing), 31=video+audio mute (blanked), 11/10 = video mute on/off.
_PJLINK_FORBIDDEN_MARKERS = ("\r", "\n", "\x00", ";")  # no CR/LF/NUL/';' inside a mapped body (injection guard)
_PJLINK_COMMANDS = {
    # --- power ---
    "power_on":            "%1POWR 1",
    "power_off":           "%1POWR 0",
    "power_query":         "%1POWR ?",
    # --- AV-mute / shutter (dowser) ---
    "shutter_close":       "%1AVMT 31",   # blank (video+audio mute on)
    "shutter_open":        "%1AVMT 30",   # show (mute off)
    "video_mute_on":       "%1AVMT 11",
    "video_mute_off":      "%1AVMT 10",
    "mute_query":          "%1AVMT ?",
    # --- input select ---
    "input_rgb1":          "%1INPT 11",
    "input_rgb2":          "%1INPT 12",
    "input_video1":        "%1INPT 21",
    "input_digital1":      "%1INPT 31",   # HDMI/DP/SDI #1
    "input_digital2":      "%1INPT 32",
    "input_network1":      "%1INPT 51",
    "input_query":         "%1INPT ?",
    # --- status / info queries (read-only) ---
    "lamp_query":          "%1LAMP ?",
    "error_status_query":  "%1ERST ?",
    "name_query":          "%1NAME ?",
    "manufacturer_query":  "%1INF1 ?",
    "product_query":       "%1INF2 ?",
    "other_info_query":    "%1INFO ?",
    "class_query":         "%1CLSS ?",
}
_PJLINK_TERMINATOR = "\r"  # CR, per the PJLink spec
_PJLINK_TCP_PORT = 4352    # PJLink Class-1 is TCP/4352 by spec; device_send pins the DESTINATION SERVICE to it

# Build-time self-check (mirrors the _ALLOW_PULSE marker fence): every mapped body is a PJLink Class-1
# frame ("%1"-prefixed) carrying no injection/terminator marker, so a future edit cannot slip an unsafe
# or multi-command payload into the reviewed set.
for _tok, _body in _PJLINK_COMMANDS.items():
    assert _body.startswith("%1") and not any(m in _body for m in _PJLINK_FORBIDDEN_MARKERS), \
        "device_send: token %r maps to a non-Class-1 / unsafe body %r" % (_tok, _body)

_QUERY_TOKENS = frozenset(t for t, b in _PJLINK_COMMANDS.items() if b.endswith("?"))


@server.endpoint("device_send")
def device_send(params):
    """Send ONE allowlisted PJLink Class-1 command to a projector via a user-created tcpipDAT.

    params: { op: <tcpipDAT path>, command: <allowlisted token> }
    FLOW (strict order, fail-closed): consent -> validate the token against the CLOSED allowlist -> resolve
    + assert the target is a writable tcpipDAT -> emit the reviewed wire body via the DAT's send() -> audit.
    On any refusal NOTHING is sent. No caller bytes ever reach the wire -- only the reviewed mapped body.
    Returns {op, command, sent, is_query, note}. Query replies arrive async in the DAT's rows (read via inspect)."""
    op_path = params.get("op")
    command = params.get("command")

    # (1) CONSENT -- default off, read fresh. Refuse before touching anything.
    if not _read_allow_device_control():
        _audit({"ts": time.time(), "event": "refused_consent", "op": op_path, "command": command})
        raise PermissionError(
            "device control not consented (set \"allow_device_control\": true in "
            "~/.touchdesigner-bridge-mcp/arm.json and re-arm)")

    # (2) CLOSED-ALLOWLIST token -> reviewed body (a caller string NEVER reaches the wire)
    if not isinstance(command, str) or command not in _PJLINK_COMMANDS:
        raise ValueError(
            "device_send: %r is not an allowlisted PJLink command. Valid tokens: %s"
            % (command, ", ".join(sorted(_PJLINK_COMMANDS))))
    body = _PJLINK_COMMANDS[command]

    # (3) TARGET: a writable tcpipDAT the user created (not bridge infra, not another optype)
    n = server.assert_writable(server.resolve_op(op_path))
    if n.opType != "tcpipDAT":
        raise ValueError(
            "device_send target must be a tcpipDAT (PJLink over TCP); got %s (%s). Create a tcpipDAT, set its "
            "Network Address to the projector IP and port 4352, then target it." % (n.path, n.opType))

    # (3b) DESTINATION SERVICE: PJLink Class-1 is TCP/4352 by spec. Pin the target port so device_send can
    # only reach a PJLink service -- the closed command allowlist governs the BYTES; this governs WHERE they
    # go (the IP/address stays caller-set, since projectors sit at site-specific addresses). Fail closed on
    # any other port so the consent grant cannot be repurposed to speak PJLink frames at an unrelated service.
    try:
        tport = int(n.par.port.eval())
    except Exception:
        tport = None
    if tport != _PJLINK_TCP_PORT:
        raise ValueError(
            "device_send target %s Port is %r; PJLink Class-1 requires TCP port %d. Set the tcpipDAT's Port "
            "to %d before sending." % (n.path, tport, _PJLINK_TCP_PORT, _PJLINK_TCP_PORT))

    # (4) SEND the reviewed frame. TD's tcpipDAT.send(contents, terminator=...) appends the terminator.
    send = getattr(n, "send", None)
    if not callable(send):
        raise ValueError("target %s has no send() method -- device_send requires a tcpipDAT" % n.path)
    try:
        send(body, terminator=_PJLINK_TERMINATOR)
    except TypeError:
        # Older/other send() signature without a terminator kwarg: send the pre-terminated frame.
        send(body + _PJLINK_TERMINATOR)

    _audit({"ts": time.time(), "event": "sent", "op": n.path, "command": command, "body": body})
    return {
        "op": n.path,
        "command": command,
        "sent": body,
        "is_query": command in _QUERY_TOKENS,
        "note": ("PJLink Class-1 frame sent (CR-terminated). "
                 + ("Query reply arrives asynchronously in the tcpipDAT's rows -- read it with inspect."
                    if command in _QUERY_TOKENS else "Control command; confirm the projector state changed.")),
    }
