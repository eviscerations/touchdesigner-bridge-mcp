# Runbook — touchdesigner-bridge-mcp

A quick operating reference for running the bridge day to day: arming the executor, pointing
the working directory, the consent flags, the reload model, and a safe-operation checklist.
For first-time setup see [INSTALL.md](INSTALL.md); for the security model see
[SECURITY.md](../SECURITY.md); for the two-process design see [ARCHITECTURE.md](../ARCHITECTURE.md).

## The two processes

- **The gateway** — one Rust binary. It is *both* the headless MCP server your AI client
  spawns (`TDMCP_GW_HEADLESS=1`) *and* the GUI you run yourself to set the working directory
  and watch the live audit log. Same executable; the env var picks the mode.
- **The executor** — data-only Python armed *inside* a running TouchDesigner session by
  pasting one line into the Textport. It uses only TD's built-in Python and the standard
  library.

They rendezvous through one file, `~/.touchdesigner-bridge-mcp/arm.json`, which both read
fresh per call.

## Arming the executor

Open TouchDesigner, open the **Textport** (`Alt+T`), and paste the arm line (substitute your
clone path; forward slashes even on Windows):

```python
import os; os.environ['TDMCP_REPO']=r'C:/path/to/touchdesigner-bridge-mcp'; exec(open(os.path.join(os.environ['TDMCP_REPO'],'arm.py')).read())
```

The GUI's arm pane shows this exact line with a **Copy arm command** button, and
`python scripts/setup.py` prints it too. Arming (`arm.py`):

- **Verifies** every `td_executor/*.py` against `td_executor/INTEGRITY.json` *before*
  importing (fail-closed tamper-evidence).
- **Refuses** to arm if any handler looks like a code-execution endpoint (the data-only
  canary, `assert_no_rce_endpoints`).
- **Mints** a 128-bit CSPRNG session token and writes `token` / `port` / `working_dir` (and
  preserves the consent flags) into `arm.json`, best-effort ACL-restricting the file to the
  current user.
- **Assembles** `/mcp_bridge` — a Web Server DAT on loopback `127.0.0.1:9980` plus a thin
  callbacks DAT that loads the on-disk `td_executor` package — and adds the GUI consent
  toggles.

`TDMCP_REPO` is injected because `arm.py` runs via `exec()` (where `__file__` is undefined);
it locates the executor package and the trust root, and is **not** the confinement working
dir. Remove the bridge with `op('/mcp_bridge').destroy()`.

## The working directory

The working directory is the single folder every executor file operation is `realpath`-confined
under — deliberately **not** the source tree. Set it in the GUI's **Working dir** pane and
click **Apply**; that updates the confinement root live for every future call and merge-writes
`working_dir` into `arm.json`. Both layers resolve it fresh per call, so changing it is just
**Apply** again — no restart, no re-arm. Reference assets by their path relative to this root;
subfolders are traversable (e.g. `assets/sections/section_00.obj`).

## The consent flags

Three human-gated flags live in `arm.json`, all **default-off**, read fresh per call:

| Flag | Enables | How to enable |
|---|---|---|
| `allow_glsl` | the GLSL shader lane (`set_glsl`, GPU-sandboxed) | flip **Allow GLSL Lane** on `/mcp_bridge` |
| `allow_expr` | the parameter-expression lane (`set_expr`, EXPERIMENTAL — host Python) | flip **Allow Expr Lane** on `/mcp_bridge` |
| `allow_highres` | bypass the enforced magnitude ceiling (res / instances / passes) | edit `arm.json` (no GUI toggle) |

Only a human at the GUI (or the arming bootstrap) can flip a consent flag — the `/mcp_bridge`
subtree is off-limits to MCP mutation, so the AI can never enable its own lane. Enable
`allow_glsl` when the art genuinely needs a bespoke shader; leave `allow_expr` off unless you
accept its EXPERIMENTAL host-code residual (prefer `bind_chop` for CHOP-driven animation);
enable `allow_highres` only for a deliverate above-ceiling render. A bare re-arm **preserves**
whatever consent was set — it never silently flips a lane.

## The reload model — dev_reload vs re-arm vs Desktop restart

| You edited… | To pick it up… |
|---|---|
| A `td_executor/**` handler (Python) | Re-arm in the Textport (or call `dev_reload`) — it hot-reloads on-disk edits, purging stale bytecode, and re-verifies integrity first. **After any executor edit, regenerate `td_executor/INTEGRITY.json`** (`python scripts/gen_integrity_manifest.py`) or the next arm/reload fails closed. |
| `td_executor/server.py` itself | One full re-arm (the `server` module is not hot-reloaded). |
| `gateway/src/*.rs` (incl. the generated `tools.rs`) | `cargo build --release` in `gateway/`, then fully restart the MCP client so it re-launches the new binary. |
| `reference/*.json` | Picked up fresh on the next call (read live) — no rebuild, no re-arm. |
| A consent flag / the working dir | Flip the GUI toggle or Apply the working dir — written to `arm.json`, read fresh on the next call. |

## Verifying a healthy setup

1. **Executor health** — `http://127.0.0.1:9980/health` responds. The GUI's **Status** pane
   also shows an "Armed" pill and the live TouchDesigner build once the executor is reachable.
2. **Full path live** — in a client chat, ask the assistant to run `scene_info`. A successful
   reply (current scene + TD build) confirms client → gateway → loopback → executor →
   TouchDesigner.
3. **Integrity clean** — arming printed the integrity pre-check OK line, not a failure.

## Safe-operation checklist

- **You fire the heavy work.** Output is wire-only by design — the bridge leaves
  `record`/`active` off (movie file out, DMX out, NDI/Spout, window open). Fire renders,
  records, and live sends yourself when ready.
- **Watch the audit log.** Keep the GUI open; every call streams into its live audit log so
  you see the network built node by node.
- **Build to the deliverable, not the ceiling.** Set `outputresolution=custom` +
  `resolutionw/h` on generators and composites (the 256px default trap); size for the real
  4K/8K deliverable.
- **Verify before trusting.** Use `scene_info` / `read_network` / `top_info` / `inspect` to
  confirm wiring, resolution, and driven state; `find_errors` reflects the last cook, so
  re-cook after a fix.
- **Keep the boundary intact.** Leave the code lanes off unless a human enabled them for a
  specific need; regenerate the integrity manifest after any executor edit; keep assets under
  the working directory.
