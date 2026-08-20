# Security Model — TouchDesigner Bridge MCP

TouchDesigner Bridge MCP is a **data-only** control surface: an AI assistant builds and
tunes TouchDesigner operator networks through a fixed, typed tool surface, and a human at
the machine fires the cooks and renders. This document describes the security model, the
threat model it defends against, its honest limits, and how to report a vulnerability.

## 1. The core guarantee — and its honest boundary

**Goal:** the assistant can build operator networks but cannot execute arbitrary code on
the host.

There is no `execute_python`-style tool, no `run`, `eval`, `shell`, or `node_op` in the
catalog. Every capability is a named, schema-validated tool. Two build-time and one
runtime canary enforce this:

- `assert_no_rce_endpoints()` (executor) refuses to arm if any registered handler has an
  RCE-shaped name (`exec`, `eval`, `wrangle`, `hscript`, `run_code`, `os_system`, …).
- `catalog_never_exposes_rce_tools` (gateway `cargo test`) fails the build if the shipped
  tool surface ever contains a generic code-driver tool or a code-carrying operator family
  (`script` / `execute` / `cplusplus`).
- `check_optype_allowed()` (executor) refuses at runtime to create any operator whose type
  name carries a `script` / `execute` / `cplusplus` marker, plus an exact-name denylist
  (currently `evaluateDAT`) for operators that evaluate their *data* as code.

**Honest boundary (this is the load-bearing residual risk, stated plainly).** In
TouchDesigner, a value set on the *wrong* parameter can be evaluated as host Python by TD
itself. The typed gateway surface is an *allowlist*, but it lowers to a generic,
open-keyed `set_par` on the executor, and the executor's loopback port is reachable by any
local process holding the token. So the true boundary is the **completeness of the
executor's parameter guard** (`check_par_allowed` + `check_optype_allowed`) over
TouchDesigner's ~17,000-parameter surface — not the gateway. This is a denylist over a
closed-source third-party surface; it cannot be *proven* complete, and it is brittle across
TouchDesigner versions. The independent red-team review found **no working RCE bypass**,
but this remains a residual rather than a closed boundary.

The parameter guard has several layers, in order:

1. **Universal code-pointer deny** — parameter *names* that reference a DAT whose text TD
   executes as host code (`callbacks`, `dragscript`, `dropscript`, `datexpr`, …) are
   refused on **every** operator. This closes the single largest RCE class structurally.
2. **Reviewed inline code-sink deny** — an audited list of exact `(optype, param)` pairs
   whose settable string *value* TD evaluates as code (e.g. `groupSOP.filter`,
   `tableDAT.cellexpr`, `replicatorCOMP.tscript`), plus regex generalizations that cover
   every Sequence-block index of those families.
3. **Fail-closed allowlist** — a driver-supplied `(optype, param)` is accepted only if it
   is a *known* parameter of a catalogued operator (present in `reference/catalog.json`),
   a legitimate Sequence-block index of one, or a live custom parameter. Anything unknown
   or newer than the shipped probe fails **closed** instead of being waved through.

These guards are mirrored between the executor's runtime checks and the gateway's
build-time fences, and cross-checked by tests (`test_code_sink_guard.py` against the
gateway fence `code_named_params_are_the_known_reviewed_set`) so the two artifacts cannot
drift apart silently. The reviewed sink list lives in the executor source (`server.py`) as the single auditable place.

## 2. Transport and authentication

- **Loopback only.** The in-TouchDesigner Web Server DAT binds `127.0.0.1` on port `9980`.
  Nothing listens off-box; no firewall rule is required.
- **Auto-minted session token.** Arming mints a 128-bit CSPRNG token
  (`secrets.token_hex(16)`), written to `~/.touchdesigner-bridge-mcp/arm.json`. The gateway
  reads it and presents it as `X-TDMCP-Token`; the executor compares with
  `secrets.compare_digest`. The user never types or sees the token. This neutralizes the
  loopback-CSRF / DNS-rebinding class (a malicious local web page POSTing to
  `127.0.0.1:9980`) at effectively zero UX cost.
- **Cross-origin refusal.** Every request — including the unauthenticated `health` /
  `validate_*` endpoints — is refused if it carries a non-loopback `Origin` or `Host`
  header, closing the DNS-rebind path even on the auth-exempt endpoints.
- **No traceback leakage.** Handler errors return a short message; Python tracebacks are
  logged locally to the Textport, never returned over the wire (host-recon hardening).
- **Body caps.** The executor enforces a 1 MB request-body cap; the gateway drops any
  inbound JSON-RPC frame over 8 MB (memory-DoS guard).
- **arm.json ACLs.** On arming, the config dir and `arm.json` are best-effort restricted to
  the current user via `icacls` so the plaintext token is not world-readable.

## 3. Filesystem confinement

- **Single working directory.** Every file operation is `realpath`-confined under one
  configured working directory (`confined_path` in the executor, `confine_path` in the
  gateway). Both layers read the **same** source of truth — `working_dir` in `arm.json`,
  set by the GUI's "Working dir" Apply — resolved fresh per call, so there is no restart
  and no hardcoded path.
- **Config dir off-limits.** `~/.touchdesigner-bridge-mcp/` (token + consent flags) is
  excluded from confinement, so no file-writing tool can read the token or flip a consent
  flag by writing there.
- **Trust root off-limits.** The executor package (`td_executor/*.py`, `INTEGRITY.json`)
  and the `arm.py` bootstrap are refused **even inside** the working directory, so a render
  or CSV write cannot land on a trusted file and corrupt the bridge on the next arm.
- **Extension whitelists.** `save_top` / `capture_ui` write only images; `write_csv` writes
  only `.csv`/`.dat`/`.txt`. The reference catalog (`.json`) is neither, so no tool can
  corrupt the allowlist that the parameter guard depends on.

## 4. Integrity pinning — and its honest ceiling

`td_executor/INTEGRITY.json` pins the SHA-256 of every executor `.py` file. Both trust
establishing moments — arming (`arm.py`) and `dev_reload` — **verify before import** and
fail closed on any mismatch or on an unpinned handler module appearing on disk. The arm-time
check is a self-contained stdlib bootstrap that hashes the files *before* `td_executor` is
imported, so a tampered `server.py` is caught before its module body can run.

**Honest ceiling — do not overstate this.** INTEGRITY.json is **tamper-evidence /
defense-in-depth, not a boundary** against an attacker who can already write the install
directory: such an attacker can rewrite the files *and* the manifest together (or set the
loudly-logged `TDMCP_INTEGRITY=0` dev bypass). Verify-before-import raises the bar — a
tampered verifier can no longer simply return success — but the **real root of trust is the
OS file permissions on the install directory**. Integrity pinning is a supplement to those
permissions, never a substitute.

## 5. The two validated code lanes (default-off, consent-gated)

The only paths that admit any code are two narrow lanes, each **off by default** and gated
on a flag in `arm.json` read fresh on every call. Both validate **before** any write, are
executor-authoritative (the gateway does only a cheap structural pre-check and defers to the
Python validator), have a single write path, and append to an audit log in the working dir.

| Lane | Tools | Consent flag | Sandbox | Worst case if validation misses |
|------|-------|--------------|---------|---------------------------------|
| `glsl_v1` | `set_glsl`, `validate_glsl` | `allow_glsl` | GPU | Recoverable driver timeout (TDR) |
| `expr_v1` | `set_expr`, `validate_expr` | `allow_expr` | none (host) | Host code — ships EXPERIMENTAL |

- **`set_glsl`** validates a GLSL fragment shader (loop / texture-fetch / size caps and a
  preprocessor allowlist), then writes it only into a Text DAT it creates and owns under the
  target `glslTOP`, wired only to that op's `pixeldat`. Build resolution/pass are clamped
  low (1280×720, ≤4 passes) so a shader cannot itself become a TDR vector. Delivering
  `pixeldat` on the raw driver path (`set_par`) is refused — it must go through `set_glsl`.
- **`set_expr`** validates a TouchDesigner parameter expression with an AST positive-allowlist
  sandbox and is the single place in the entire MCP that writes a parameter's `.expr`. It
  ships EXPERIMENTAL because a validator gap here would be host code, not a recoverable GPU
  reset; its honest residual (live-API reachability of allowlisted attributes) is disclosed
  in-code and the lane stays off until that is signed off.

**The AI cannot enable its own lane.** The consent flags live in the off-limits config dir,
and the `/mcp_bridge` component (and the GUI toggles that mirror the flags) is refused by
`assert_writable` on every mutating tool — so only a human at the GUI, or the arming
bootstrap, can flip consent. A bare re-arm preserves whatever consent was set (it never
silently disables or enables a lane).

## 6. Availability: the enforced magnitude ceiling (F-DOS-1)

The resource governor is **advisory-first** — heavy requests surface a magnitude flag so
the watching operator can down-scale, but the governor does not block routine work (and a
telemetry failure never blocks). TouchDesigner's Python API exposes no whole-card VRAM
figure, so the advisory classifies on system RAM and per-request magnitude, not live VRAM.

On top of the advisory, an **enforced ceiling** hard-refuses a genuinely catastrophic,
driver-killing magnitude on known parameters (`governor.py`): per-dimension resolution
> 16384 px, instance/particle counts > 5,000,000, render passes > 256. This stops a generic
`set_par {resolutionw: 100000}` from exhausting VRAM and hanging the display driver. It is
overridable only by the human-gated `allow_highres` flag in `arm.json`, so the capability is
guarded, never permanently amputated. Legitimate 4K/8K delivery passes comfortably.

## 7. Threat model

Defended:

- **A fully adversarial / prompt-injected AI** driving the gateway (or hitting the executor
  directly with the token): confined to the typed data surface + the parameter guard; cannot
  run host code, escape the working directory, read the token, or flip its own consent.
- **A network-local attacker** (a browser page, another local process): loopback bind +
  cross-origin refusal + token auth close the CSRF / DNS-rebind path; the token file is
  ACL-restricted to the user.
- **A malicious-input attacker**: body caps, JSON validation, the code-lane validators, and
  the enforced magnitude ceiling constrain resource and parse abuse.

Explicitly **not** in scope:

- An attacker who already has **write access to the install directory** or the user account
  running TouchDesigner. Integrity pinning is tamper-evidence here, not a boundary; OS file
  permissions are the root of trust.
- Any local process that already holds the `arm.json` token — it can reach the executor
  exactly as the gateway does. Loopback + the parameter guard, not client identity, are the
  boundary.
- Vulnerabilities in TouchDesigner, the OS, the GPU driver, or the AI client itself.
- The residual completeness risk of the parameter denylist described in §1.

## 8. Reporting a vulnerability

**Do not open a public issue for security vulnerabilities.**

Use GitHub's private vulnerability reporting:
1. Go to the [Security tab](https://github.com/eviscerations/touchdesigner-bridge-mcp/security)
2. Click **Report a vulnerability** to open a private security advisory
3. Describe the issue with enough detail to reproduce it — affected component(s), reproduction steps or a proof of concept, and the impact you observed

A confirmed parameter that TouchDesigner evaluates as code but that the guard does **not** deny (a `check_par_allowed` / `check_optype_allowed` bypass) is the highest-value report — see §1.

You'll receive a response within 7 days. If the vulnerability is confirmed, a fix will be released as soon as practical and you'll be credited in the release notes if you wish.
## 9. Version

This document describes version **0.1.0**. Security-relevant changes are tracked in
[CHANGELOG.md](CHANGELOG.md).
