# How-to

Living document — grows as the tool is tested. Every step below is a typed, data-only, validated operation,
so these flows double as a safe way to *learn* TouchDesigner with an AI driving. Each task is a short index
into the real tools and the recipe that carries the full ordered steps — pull any recipe in full with
`recipe_reference recipe=<id>`, and any operator's exact parameters with `operator_reference optype=<name>`.

## Install (one-time)

1. Build the gateway: `cargo build --release --manifest-path gateway/Cargo.toml` (or `cargo build --release`
   from `gateway/`). One file results: `gateway/target/release/touchdesigner-bridge-mcp.exe`, both the GUI
   and the headless MCP server.
2. Run it with no arguments to open the GUI, set your **Working dir**, and click **Apply**.
3. Register the gateway with your MCP client, pointing at the `.exe` with `TDMCP_GW_HEADLESS=1` and
   `TDMCP_REPO=<clone path>` in its env, then fully restart the client.

> **Fast path:** `python scripts/setup.py` builds the gateway and prints your filled-in Claude Desktop
> config **and** the Textport arm command with real paths for this machine.

## Arm the executor

Open TouchDesigner, open the **Textport** (`Alt+T`), and paste the arm line (substitute your clone path):

```python
import os; os.environ['TDMCP_REPO']=r'C:/path/to/touchdesigner-bridge-mcp'; exec(open(os.path.join(os.environ['TDMCP_REPO'],'arm.py')).read())
```

The GUI shows this exact line with a **Copy arm command** button. The **Status** pane shows an "Armed" pill
and the live TouchDesigner build once the executor is reachable. **Re-arm any time** to hot-reload on-disk
edits. No firewall step is needed — the bridge binds `127.0.0.1` only.

## Change the working directory

Type a new root into the GUI's **Working dir** field and click **Apply**. It writes `arm.json` and takes
effect live for both the executor and the gateway — no restart, no re-arm.

## Verify your setup

Open `http://127.0.0.1:9980/health` in a browser, and ask the assistant to run `scene_info` in a fresh
chat. A successful reply confirms the whole path: client → gateway → loopback → executor → TouchDesigner.

---

## How do I…

Each entry lists the real tools plus the recipe id to open with `recipe_reference recipe=<id>` for the
ordered steps, per-step `OUT_` landmarks, verify checks, and gotchas.

### …render a building model onto a dark plate?

`geometryCOMP` + `fileinSOP` (loads `.obj`/`.tog`; use `fbxCOMP`/`alembicSOP`/`usdCOMP` for other formats)
→ delete the default torus, `set_flags(render,display)` → `constantMAT` (mandatory, or it renders black) →
`cameraCOMP` → `renderTOP` (references camera/geometry **by parameter**) → `constantTOP` dark plate →
`overTOP` → `nullTOP OUT_render`. Recipe: **`facade_3d_render`**.

### …make generative content (patterns, energy, light sweeps, trails)?

`noiseTOP` → `rampTOP` → `levelTOP` → `transformTOP` → `compositeTOP` → `feedbackTOP` (point its `top` at
the downstream composite to close the trail loop) → `blurTOP` → `nullTOP OUT_facade_tex`. Set
`outputresolution=custom` + `resolutionw/h` on the source to escape the 256px default. Recipe:
**`generative_texture_fx`**.

### …project video onto a building, in one call?

Copy the section OBJs under `assets/sections/`, then `import_segmented_model` builds the whole `sec*`/`mat*`
rig at once, and `set_par_many` drives a `moviefileinTOP` onto every `matNN.emitmap` with
`emitmapcoord=uv0`. Sections must carry UVs. Recipe: **`segmented_facade_video_projection`** (for an
existing hand-built rig, **`segmented_facade_video_content`**).

### …build a per-section rig to address each façade part independently?

`import_segmented_model` (or hand-build one `geometryCOMP` + one MAT per section — TD cannot scope a
material to part of a merged mesh). This is the foundation the choreography, colour, video, and masking
lanes all animate. Recipe: **`per_section_material_rig`**.

### …animate a parameter without writing code?

Use **`bind_chop`** to bind the parameter to a CHOP channel (an `lfoCHOP`, `audiospectrumCHOP`, `timerCHOP`,
`patternCHOP`) — a data binding, not code, fully inside the data-only boundary. For a travelling light
reveal driven by one LFO, recipe: **`height_sweep_choreography`**.

### …drive per-section colour, brightness, or fades (no video)?

Each section's emit is three exportable scalars, so brightness × per-section RGB gives arbitrary per-section
colour. Keyframe colour from a `write_csv` cue table, or multiply constant colours by an animated
brightness. Recipe: **`per_section_color_choreography`**.

### …run a cued show with a timeline, GO buttons, and timecode?

A `tableDAT` cue table (from a working-dir CSV) feeds a `timerCHOP` (Ease In/Out, On Done = Re-Start) that
glides each section through its cues; GO is the allowlisted `pulse` actuator; a `timecodeCHOP` locks to
LTC/MTC. Recipes: **`facade_cue_choreography`** and **`show_control_timecode`**.

### …react to live OSC / audio / DMX / MIDI input?

Swap only the source node — `oscinCHOP`, `audiodeviceinCHOP`, `dmxinCHOP`, or `midiinCHOP` — into the shared
rename → merge → null → CHOP-export apply block; the rest is identical to the sweep. Recipe:
**`realtime_input_driver`**.

### …keep projected content off the real windows/recesses?

Multiply a grayscale UV-space mask (windows = 0 → black texels) into the emit content in TOP space, then
assign the product to `emitmap (uv0)`. An irregular window-aligned mask is an upstream-baked asset. Recipe:
**`facade_mask_atlas`**.

### …align/keystone the composite and send it to a projector?

Apply a data-only 4-corner keystone with `cornerpinTOP` (pin* corners, `mapping=perspective`), plant an
aligned null, then wire a `windowCOMP` perform window, `moviefileoutTOP`, `ndioutTOP`, or
`syphonspoutoutTOP` — **wire-only**, window not opened, `record`/`active` off. The on-site calibration
numbers get typed back via `set_par`. Recipe: **`projection_align_and_output`**.

### …split the composite across two overlapping projectors with a blended seam?

Per projector: `cropTOP` slice + `cornerpinTOP` keystone + `rampTOP` blend mask + `levelTOP` gamma +
`multiplyTOP` → per-projector `windowCOMP`, with the masks summing to 1. Overlap width/gamma are measured
on-site and typed back. Recipe: **`multiprojector_edge_blend`**.

### …warp content onto a curved or irregular surface?

Build an identity UV field (`rampTOP` + `reorderTOP`), add a control-grid offset
(`write_csv` → `tableDAT` → `dattoCHOP` → `choptoTOP` → `blurTOP`), and apply it with a `remapTOP` — the
data-only analog of kantanMapper. The interactive per-vertex drag is the human step. Recipe:
**`mesh_freeform_warp`**.

### …hand the finished content off to a media server or a 4K file?

Tap `OUT_render` / `OUT_facade_tex` into a `moviefileoutTOP` (HAP / HAP Q is the media-server default codec)
or a `nullTOP` feeding NDI/Spout — **wire-only**, `record`/`active` left off. You press record. Recipe:
**`output_handoff`**.

### …drive DMX / Art-Net / sACN fixtures from TouchDesigner?

Route A: a CHOP channel-set → `dmxoutCHOP` (its `active` defaults **on**, so the bridge holds it off).
Route B: a coloured POP → `dmxfixturePOP` → `dmxoutPOP` for pixel-mapping. Going live is your wire-only
step. Recipe: **`dmx_artnet_output`**.

### …render a drone point cloud as content?

`pointfileinTOP` (ingests a finished `.ply`/`.exr`/`.xyz` cloud; photogrammetry itself is external) →
normalize → instance geometry to the points via a `geometryCOMP` (instancing on, fed by the cloud TOP) with
a `pointspriteMAT` → frame and render. Recipe: **`point_cloud_content`**.

### …match the virtual camera to the real façade?

`cameraCOMP` with `projection=ortho` + `orthowidth` for a flat 1:1 map, or `projection=perspective` +
`viewanglemethod=focalaperture` to match a real lens; aim it at a `nullCOMP` target at façade centre.
Recipe: **`camera_match_facade`**. For casting content from a calibrated projector frustum onto 3D geometry,
recipe: **`projector_calibrated_3d`**.

### …write a GLSL shader?

Ask `glsl_reference` (topic=`patterns`/`environment`/`functions`, or search=<substring>) for the shader
text. If you have enabled `allow_glsl`, apply it with `set_glsl` (validated before TouchDesigner compiles
it); otherwise paste the text into a Text DAT wired to the `glslTOP`'s `pixeldat`. The AI never runs it on
its own.

### …compute a parameter with an expression?

Prefer `bind_chop` for anything CHOP-driven. For logic a binding can't express, ask `expr_reference`
(topic=`common`/`allowed`) for the single-line expression. If you have enabled `allow_expr`, apply it with
`set_expr` (validated allowlist-first); otherwise type it into the parameter by hand. This is the most-gated
lane. See `code_reference` for how the whole code surface and consent handshake fit together.

---

## Inspect & navigate

- `td_capabilities` — start-here orientation: the whole surface, the data-only boundary, and how to route a
  question.
- `scene_info`, `read_network` (`pars=true` for non-default params) — what's in the scene / a network's
  structure and wiring.
- `operator_reference` (`optype=<name>`, or `search=<substring>` as the operator finder), `help` — a node's
  exact typed schema, or its facts plus the official Derivative docs link.
- `find_errors` — scan a subtree for operators reporting errors, so you can self-correct.
- `top_info` — cheap numeric TOP state (resolution, GPU memory, cook stats); `mem` — the live resource band.
- `save_top` — a TOP's actual pixels; `capture_ui` — any operator's own node viewer (CHOP graph, 3D view,
  MAT preview) from TouchDesigner's own GPU buffer.
