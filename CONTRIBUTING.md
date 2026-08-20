# Contributing — TouchDesigner Bridge MCP

Thanks for your interest in improving TouchDesigner Bridge MCP. This project is a
**data-only** control surface, and that boundary is the point of the whole design — so the
most important rule for any change is: **do not add a path that lets the AI run arbitrary
code.** Read [SECURITY.md](SECURITY.md) and [ARCHITECTURE.md](ARCHITECTURE.md) before making
non-trivial changes.

> `<repo>` below is your absolute path to this clone. Nothing in the codebase is hardcoded to
> a developer machine — keep it that way (see the data-only + no-hardcoded-paths rules below).

## Prerequisites

- **Windows** with a licensed TouchDesigner install (required only to *arm* and run live —
  the executor tests do not need TouchDesigner; see below).
- **Rust** (stable) with `cargo`, to build the gateway.
- **Python 3** (plain CPython — the executor tests use a fake TD scene and no third-party
  packages).

## Build the gateway

```sh
cargo build --release --manifest-path <repo>/gateway/Cargo.toml
# or, from <repo>/gateway/:
cargo build --release
```

The binary lands at `<repo>/gateway/target/release/touchdesigner-bridge-mcp.exe`. Launching
it with no args shows the GUI; embedded MCP-client launches set `TDMCP_GW_HEADLESS`.

Note: `gateway/src/tools.rs` is **generated** (from `reference/catalog.json`), not
hand-edited. Regenerate it with the catalog generator rather than editing it directly.

## Run the checks

Run all of these before opening a change. They are the gates the security model relies on.

```sh
# 1. Executor unit tests — offline, no TouchDesigner required (uses tests/_tdmock.py).
python <repo>/td_executor/tests/run_tests.py

# 2. Gateway tests — includes the build-time boundary fences (catalog_never_exposes_rce_tools,
#    code_named_params_are_the_known_reviewed_set, GLSL source-only-as-NodePath, reserved
#    placement args, unique names).
cargo test --manifest-path <repo>/gateway/Cargo.toml

# 3. Registry consistency — the gateway catalog and the executor endpoint set agree.
python <repo>/scripts/audit_registry_consistency.py

# 4. Recipe validation — every recipe maps to real, shipped tools.
python <repo>/scripts/validate_recipes.py
```

### After ANY executor edit: regenerate the integrity manifest

`td_executor/INTEGRITY.json` hash-pins every executor `.py` file, and arming/`dev_reload`
**verify before import** and fail closed on any mismatch or unpinned handler. If you edit
anything under `td_executor/` (including adding a handler module), regenerate the manifest or
the next arm/reload will refuse to load:

```sh
python <repo>/scripts/gen_integrity_manifest.py            # write/refresh the manifest
python <repo>/scripts/gen_integrity_manifest.py --check    # CI mode: nonzero if stale
```

The digest diff is meant to be reviewed — the manifest update is an intentional, reviewed
act, not an afterthought.

## The arm / reload loop

The two processes reload differently (full table in [ARCHITECTURE.md](ARCHITECTURE.md)):

- **Edited an executor handler (`td_executor/handlers/**`)** → regen `INTEGRITY.json`, then
  re-arm in the TouchDesigner Textport (it hot-reloads on-disk edits and purges stale
  bytecode), or call `dev_reload`. Both re-verify integrity first.
- **Edited `td_executor/server.py`** → one full re-arm (the `server` module is not
  hot-reloaded).
- **Edited the gateway (`gateway/src/*.rs`)** → `cargo build --release`, then restart the
  Claude Desktop / MCP client so it re-launches the new binary.
- **Edited `reference/*.json`** → picked up fresh on the next call; no rebuild, no re-arm.

Arm command (substitute your clone path):

```python
import os; os.environ['TDMCP_REPO']=r'<path-to-your-clone>'; exec(open(os.path.join(os.environ['TDMCP_REPO'],'arm.py')).read())
```

For local dev iteration you may set `TDMCP_INTEGRITY=0` to bypass the integrity gate — it is
loudly logged and must never be the default in any committed workflow.

## The data-only rule for new tools

Every new capability must stay on the data-only side of the boundary:

- **No code sinks.** A new operator or parameter is acceptable only if it moves *data*, not
  code. If a parameter's string *value* is evaluated by TouchDesigner as Python / Tscript /
  script / a shader expression, or if it references a DAT whose text TD executes, it must be
  **denied** — add it to the reviewed guard in `td_executor/server.py`
  (`_DENY_CODE_SINK_PARS` / `_DENY_CODE_SINK_PATTERNS` / `_DENY_PARAM_NAMES_UNIVERSAL` or
  `_DENY_OPTYPE_*`) and to the mirroring gateway fence. `test_code_sink_guard.py` cross-checks
  the two so they cannot drift.
- **New endpoints are typed and validated.** Register handlers with `@server.endpoint(...)`;
  never add a generic code-driver. `assert_no_rce_endpoints()` will refuse to arm if a
  handler has an RCE-shaped name, and the gateway fence `catalog_never_exposes_rce_tools`
  fails the build if a code-carrying tool reaches the catalog.
- **File I/O stays confined.** Use `confined_path()` for any path a tool reads or writes;
  keep extension whitelists on write tools.
- **Anything that admits code goes through a validated, consent-gated lane** — the pattern of
  `glsl.py` / `expr.py` (validate before write, default-off consent from `arm.json`,
  executor-authoritative, single write path, audited). Do not add a code lane without owner
  sign-off.

## No hardcoded developer paths

There must be **no** absolute developer path (e.g. `C:/dev/...`) in shipped code, generated
catalogs, or docs. Resolve paths at runtime from config / env / `arm.json` / the running
executable's location, and use a `<repo>` or `<path-to-your-clone>` placeholder in
documentation.

## Pull requests

- Keep changes focused; explain the boundary implications of anything touching `server.py`,
  the guards, the code lanes, or the catalog.
- Make sure all four checks above pass, and that `INTEGRITY.json` is regenerated if you
  touched the executor.
- Note any change to the security posture in [CHANGELOG.md](CHANGELOG.md) under
  `Unreleased`.

## Licensing of contributions

This project is dual-licensed (PolyForm Noncommercial 1.0.0 for noncommercial use; a separate
paid commercial license — see [LICENSE](LICENSE) and
[COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md)). By contributing you agree your contribution
may be distributed under both.
