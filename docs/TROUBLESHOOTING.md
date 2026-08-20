# Troubleshooting

Living document — grows as the bridge is exercised. Each entry: **symptom → cause → fix**.

## Connection & arming

**The client shows the `touchdesigner` server but exposes no tools, or the handshake hangs**
- The gateway launched its **GUI** instead of the headless MCP server: `TDMCP_GW_HEADLESS`
  is not set in the client config's `env`.
- Fix: add `"TDMCP_GW_HEADLESS": "1"` to the `touchdesigner` block in
  `claude_desktop_config.example.json` / your real config, and fully quit and reopen the
  client so it re-launches the binary.

**Tool calls fail with a connection error or HTTP 403**
- TouchDesigner is not armed, or `arm.json` is stale (the executor isn't listening, or the
  token the gateway presents no longer matches the one the executor minted).
- Fix: re-run the arm line in the TD Textport (`Alt+T`), then confirm
  `http://127.0.0.1:9980/health` responds. Re-arming mints a fresh token and rewrites
  `~/.touchdesigner-bridge-mcp/arm.json`, which both layers read fresh per call — no gateway
  restart needed.

**"integrity pre-check FAILED, refusing to arm"**
- An executor file under `td_executor/` changed without regenerating the pinned manifest, so
  the SHA-256 check in `arm.py` fails closed *before* importing the package.
- Fix: regenerate the manifest with `python scripts/gen_integrity_manifest.py`, then re-arm.
  For local dev only, `TDMCP_INTEGRITY=0` bypasses the check (loudly logged).

**A `gateway/src/*.rs` change (or a rebuilt binary) doesn't take effect**
- The MCP client holds the previously spawned binary for the life of the session; a Rust
  rebuild is not hot-reloaded.
- Fix: `cargo build --release --manifest-path gateway/Cargo.toml`, then fully restart the
  Claude Desktop / MCP client so it re-launches the new binary. (Executor Python edits are
  different — those hot-reload on re-arm; see the RUNBOOK load model.)

## Paths / confinement

**A file tool refuses a path that looks correct**
- Every file read and write is `realpath`-confined under the single **working directory**
  (`arm.json`'s `working_dir`). Anything outside it — even via a symlink or junction — is
  refused, as are the config dir and the executor trust root even when they sit inside it.
- Fix: put the asset under the working directory (subfolders are traversable, e.g.
  `assets/sections/section_00.obj`), and set the right folder in the GUI's **Working dir**
  pane and click **Apply** (no restart, no re-arm).

**A movie/table file path set at create time reads as empty, or is rejected as "outside working directory"**
- A path passed as a create-time parameter can normalize to a Windows `\\?\`
  extended-length form that does not read, and a relative path is rejected by confinement.
- Fix: create the node first, then `set_par` the file path as an **absolute, forward-slash**
  value under the working dir. For a `tableDAT`, also set `loadonstart` on so the table
  actually loads.

## Building operators

**A create tool "ignored" the color / resolution / transform I passed**
- Create-and-configure tools take **tuplet vectors** (`color: [r,g,b]`, `resolution: [w,h]`,
  `t`/`r`/`s`), not raw component names. Passing raw components (`colorr`, `resolutionw`) or
  a `pars{}` wrapper to a create tool silently drops them — they are not create-tool params.
- Fix: pass the tuplet vector to the create tool, **or** set raw components (`colorr`,
  `resolutionw`, `tx`) afterward with `set_par`. The two surfaces use different names by
  design.

**A request is refused with a magnitude error (resolution / instances / passes)**
- The enforced ceiling (`governor.py`) hard-refuses a driver-killing magnitude: per-dimension
  resolution > 16384 px, instance/particle counts > 5,000,000, or render passes > 256.
- Fix: build to the real deliverable size (4K/8K pass comfortably). A genuinely larger
  magnitude needs the human-gated `allow_highres` flag in `arm.json`.

## Rendering / black output

**The render is black or empty**
- One of the render prerequisites is missing: no material assigned (geometry renders black
  without a MAT — and `phongMAT`/`pbrMAT` also need a light, while `constantMAT` needs none),
  the render flag is off on the SOP that should render, or the camera isn't framing the geo.
  A fresh `geometryCOMP` also ships a default child (a torus) that competes to be rendered.
- Fix: assign a MAT (via `geometryCOMP.material`), `set_flags render=true` on the intended
  SOP, delete the default torus child, and confirm the camera frames the model. The
  `renderTOP` references camera/geometry/lights **by parameter path**, not by wiring.

**The composite or plate is 256×256 and crushes the render (the 256px trap)**
- Generators and compositing TOPs (`constantTOP`, `overTOP`, `noiseTOP`, `rampTOP`, …)
  default to 256×256. The chain silently inherits that resolution.
- Fix: set `outputresolution=custom` + `resolutionw`/`resolutionh` on the source generators
  **and** on the plate/composite to match the render. (Non-commercial builds cap output at
  1280.)

**The Render TOP output looks transparent when composited**
- The `renderTOP` background is transparent by design.
- Fix: composite it over a dark `constantTOP` (render = input 0, plate = input 1) for a
  readable, solid-background plate.

## Animation & export

**A CHOP animation doesn't drive the target parameter**
- Setting the export node's Export flag **alone** does not drive the target. TD derives the
  channel→parameter mapping only as a cook-time side effect, and the exporter's Viewer must
  be active. A manually built export block that never activated the viewer / force-cooked
  leaves the target stuck in Constant mode.
- Fix: prefer **`bind_chop`** — it builds the dedicated renameCHOP, sets
  `exportmethod=autoname` + `autoexportroot`, turns the exporter's Viewer Active flag on,
  force-cooks so the mapping resolves, and reports `driven` honestly (never writing an
  expression on the target). If building the block by hand, keep the renameCHOP's viewer
  active and ensure the source CHOP is cooking (an animated source cooks each frame).

**An autoname export doesn't resolve onto the target material/parameter**
- autoname resolves the channel name (`matNN:emitr`, `secLevelNN:opacity`) against
  **siblings**; the exporter and the targets must share a parent, and the channel name must
  match the target `op:par` exactly.
- Fix: place the export null under the same parent as the targets and check the `renameto`
  spelling; `inspect` a target param — a driven one reads `ParMode.EXPORT` with the export
  null as its source.

## Output / hand-off

**An output op is running (recording or sending) when it shouldn't be**
- Output ops default `active`/`record` **on** (e.g. `dmxoutCHOP.active`), but this bridge
  wires output **wire-only** — the bridge sets them off and the operator fires them.
- Fix: this is expected — the bridge leaves `moviefileoutTOP.record`, `dmxoutCHOP.active`,
  NDI/Spout sends, and window opening **off**. Firing the render/record/live-send is the
  human's gated step, done in TouchDesigner.

## Code lanes

**`set_glsl` / `set_expr` (or delivering `pixeldat` / a parameter expression) is refused**
- The two code lanes are **default-off** and consent-gated: `allow_glsl` / `allow_expr` in
  `arm.json`. The AI cannot enable its own lane, and `pixeldat` on the raw `set_par` path is
  refused — it must go through `set_glsl`.
- Fix: a human flips the **Allow GLSL Lane** / **Allow Expr Lane** toggle on `/mcp_bridge` in
  TouchDesigner (persists to `arm.json`, read fresh — no re-arm). Until then, the `*_reference`
  tools propose the shader/expression text to paste by hand.

**A `set_par` of a `callbacks` / `*script` / `datexpr`-style param, or an `evaluateDAT`, is refused**
- These are code-pointer/code-sink parameters and code-evaluating operators denied by the
  parameter guard (`check_par_allowed` / `check_optype_allowed`) on every operator — the
  data-only boundary, not a bug.
- Fix: there is no data-only override; use the intended non-code path (e.g. `bind_chop` for
  animation, `set_glsl` for shaders behind consent).

## Diagnostics

**`find_errors` reports a stale or already-fixed error**
- Error state reflects the last cook. A node that hasn't re-cooked since the fix still carries
  its previous error, and the executor reads whatever TD currently holds.
- Fix: cook the node (or advance a frame / re-run the read) and re-check. Use `scene_info` /
  `read_network` to confirm current wiring and `inspect` for a single node's live parameter
  state before trusting a stale line.

---

## References

When a tool's exact parameters, an operator type, or a TouchDesigner behavior is unclear,
these are the authoritative sources — all offline, no network needed:

- **The `operator_reference` MCP tool** — the exact typed parameter schema for one operator
  (`optype=<name>`), answered live from the bundled catalog. The fastest way to confirm a
  parameter name from inside a session.
- **The `recipe_reference` MCP tool** — the tool-mapped workflow recipes (`classify=<task>`)
  with ordered steps, the landmark `OUT_` null to plant at each stage, and the cheap read to
  verify it. Worked examples of how façade content is built.
- **The `help` and `td_capabilities` MCP tools** — an operator's facts (family, input count,
  parameter names) plus a deep link to the official Derivative documentation, and the
  data-only boundary / governor orientation.
- **`scene_info` / `read_network` / `top_info` / `inspect`** — read live scene state, map a
  network, read a TOP's resolution, and inspect one node's parameters before and after a
  change.
