# Operator Guide — touchdesigner-bridge-mcp

A day-to-day operating reference for driving this MCP from an AI chat (Claude Desktop or any MCP agent).
It covers how you drive it, the mental model that keeps it safe, the tool families, the working loop that
builds real façade content, the conventions that make that loop succeed, and the operating gotchas — so a
session can be productive immediately. Because every step is a typed, validated, data-only operation, it
also doubles as a low-stakes way to *learn* TouchDesigner with an AI at your side — you describe what you
want, it builds the operator network in your live session, and you watch it take shape. For first-time
install and setup see the [README](../README.md) and [docs/INSTALL.md](INSTALL.md); for any operator's exact
parameters ask the `operator_reference` tool live, or `help` for its facts plus the official docs link.

---

## The mental model

The bridge is **data-only by construction**. The assistant can only call a fixed registry of typed,
schema-validated tools — one create-and-configure tool per operator plus a handful of utility verbs. There
is deliberately no arbitrary-code tool, no raw-script path, and no free-form code sink; the set of things
the server can do *is* the enumerated tool list. Two consequences shape every session:

- **The AI builds the network; you fire the heavy work.** The assistant assembles and tunes the operator
  graph (TOPs, CHOPs, SOPs, COMPs, MATs, DATs, POPs) that produces mapped video. Renders, file bakes, and
  live network sends are **wire-only** — the bridge builds and wires the op with `record`/`active` left
  **off**, and *you* (or your media server) fire it. The assistant never triggers a render, a bake, or a
  send on its own.
- **Content authoring ends before the physical warp.** TouchDesigner's job here is *content creation*; the
  final warp/blend onto the real surface is done downstream by the media server (Pixera / disguise). The
  bridge authors the pixels and can wire the hand-off; the on-site calibration is a human step.

---

## How you drive it

You build a **live TouchDesigner network** by calling the MCP tools from chat. Useful habits:

- Call `td_capabilities` first to orient — it returns the whole surface (tool count by family), the
  data-only boundary, and how to route a question to the right lookup tool. Then `scene_info` to see the
  current scene and build.
- The tools can only read/write inside one **working directory** (set in the gateway GUI). Reference data
  files by their path **relative to that root** — subfolders are traversable (e.g.
  `assets/sections/section_00.obj`). Anything outside the root is refused, even through a symlink.
- Inspect before acting: `read_network` (a network's structure + wiring, `pars=true` for non-default
  params), `operator_reference` (any operator's exact typed schema; `search=<substring>` is the operator
  finder), `find_errors` (why a subtree is broken), `top_info` (cheap numeric TOP state — resolution, GPU
  memory, cook stats).
- To *see* results: `save_top` returns a TOP's actual pixels; `capture_ui` renders any operator's own node
  viewer (a CHOP graph, a SOP/geometryCOMP 3D view, a MAT preview) from TouchDesigner's own GPU buffer.

---

## Install & arm (one-time recap)

Full steps are in the [README](../README.md) / [docs/INSTALL.md](INSTALL.md). In short:

1. Build the gateway: `cargo build --release --manifest-path gateway/Cargo.toml`. This produces one file,
   `gateway/target/release/touchdesigner-bridge-mcp.exe`, which is both the GUI and the headless MCP server.
2. Run the gateway with no arguments to open the GUI, set your **Working dir** and click **Apply** (it
   merge-writes `working_dir` into `~/.touchdesigner-bridge-mcp/arm.json`). Leave it running to watch the
   live audit log.
3. Register the gateway with your MCP client, pointing at the built `.exe` with `TDMCP_GW_HEADLESS=1` and
   `TDMCP_REPO=<clone path>` in its env. Fully restart the client.

**Arm the executor:** open TouchDesigner, open the Textport (`Alt+T`), and paste the one arm line (the GUI
shows it with a **Copy arm command** button, and `python scripts/setup.py` prints it). Arming verifies the
executor files against `INTEGRITY.json`, refuses to arm if any handler looks like a code-execution endpoint,
mints a session token, and assembles the `/mcp_bridge` component (a loopback Web Server DAT on
`127.0.0.1:9980`) plus the GUI consent toggles. **Re-arm any time** to hot-reload on-disk edits.

**No firewall step is required** — the Web Server DAT binds `127.0.0.1` only, so nothing listens off-box.

**Change the working directory:** type a new root into the GUI's **Working dir** field and click **Apply**.
It rewrites `arm.json` and takes effect live for both the executor and the gateway — no restart, no re-arm.

---

## The working loop

Every real deliverable follows the same orient → discover → build → verify → deliver arc:

1. **Orient** — `td_capabilities` for the surface and boundary; `scene_info` / `read_network` for what
   already exists.
2. **Discover the route** — `recipe_reference classify=<what you have / want to do>` returns the routing
   table that maps your task to an entry recipe; then `recipe=<id>` for the ordered, tool-mapped steps.
   The 66 recipes are worked examples that carry the conventions (below), not black boxes.
3. **Build** — call each operator's own create-and-configure tool by name (`geometryCOMP`, `renderTOP`,
   `noiseCHOP`, …), which creates the node and sets its parameters in one call. Look up params first with
   `operator_reference optype=<name>`; run many ops in one round-trip with `batch`. Wire nodes with
   `connect`; adjust an existing node with `set_par` / `set_par_many`; set flags with `set_flags`.
4. **Verify each step** — recipes carry a cheap per-step check: `read_network` (structure), `find_errors`
   (cook errors), `top_info` (resolution/state), `capture_ui` / `save_top` (see it). Plant an `OUT_` null
   at each landmark so later steps target a stable name.
5. **Deliver** — wire the output op (`moviefileoutTOP`, `ndioutTOP`, `windowCOMP`, …) **wire-only**, with
   `record`/`active` off. You fire it.

---

## Tool families

The catalog is **544 tools total** — **509 operator tools** across the seven TouchDesigner families plus
**35 utility / introspection tools**. Every operator tool creates and configures one operator type in a
single call and accepts four reserved placement args (`op_name`, `parent_path`, `pos_x`, `pos_y`).

- **TOP — image / texture (106)** — the GPU raster family and the core of façade content: generators
  (`noiseTOP`, `rampTOP`, `constantTOP`, `textTOP`, `circleTOP`, `rectangleTOP`), compositing
  (`overTOP`, `addTOP`, `multiplyTOP`, `compositeTOP`, `crossTOP`, `switchTOP`), grade/FX (`levelTOP`,
  `hsvadjustTOP`, `blurTOP`, `lumablurTOP`, `edgeTOP`, `embossTOP`, `displaceTOP`, `remapTOP`), the
  `feedbackTOP` trail loop, media I/O (`moviefileinTOP`, `moviefileoutTOP`, `ndiinTOP`/`ndioutTOP`,
  `syphonspoutinTOP`/`syphonspoutoutTOP`, `videodeviceinTOP`), the `renderTOP` 3D-to-2D heart, and
  alignment ops (`cornerpinTOP`, `lensdistortTOP`, `scalabledisplayTOP`, `viosoTOP`).
- **CHOP — channels / signals / timing (137)** — the animation and control-data family: generators
  (`lfoCHOP`, `noiseCHOP`, `patternCHOP`, `waveCHOP`, `constantCHOP`), math/shape (`mathCHOP`,
  `filterCHOP`, `lagCHOP`, `limitCHOP`, `functionCHOP`), sequencing (`timerCHOP`, `beatCHOP`,
  `timecodeCHOP`, `countCHOP`, `triggerCHOP`), live input (`oscinCHOP`, `midiinCHOP`, `dmxinCHOP`,
  `audiodeviceinCHOP`, `audiospectrumCHOP`), and output (`dmxoutCHOP`, `oscoutCHOP`, `midioutCHOP`).
  `nullCHOP` is the recommended stable export tap.
- **SOP — geometry / surfaces (79)** — `fileinSOP` (loads `.obj`/`.tog`), `boxSOP`, `sphereSOP`,
  `gridSOP`, `circleSOP`, `textSOP`, transform/edit (`transformSOP`, `copySOP`, `mergeSOP`,
  `noiseSOP`, `extrudeSOP`), plus `nullSOP` as the endpoint tap.
- **COMP — components / 3D / containers (30)** — the 3D scene and UI family: `geometryCOMP` (the object a
  Render TOP draws), `cameraCOMP`, `lightCOMP`, `environmentlightCOMP`, `nullCOMP` (transform tap),
  `baseCOMP`/`containerCOMP` (grouping/UI), `windowCOMP` (output window), `usdCOMP`/`fbxCOMP` (scene
  import), and the Bullet physics COMPs.
- **MAT — materials / shading (10)** — `constantMAT` (flat, light-independent — the common projection
  choice), `phongMAT` / `pbrMAT` (lit), `pointspriteMAT` (per-point billboards), `lineMAT`, `depthMAT`,
  and the tap MATs.
- **DAT — data / tables / references (51)** — `tableDAT`, `fileinDAT`, `folderDAT`, `choptoDAT`,
  `oscinDAT`/`oscoutDAT`, `midiinDAT`, `artnetDAT`, `opfindDAT`, `infoDAT`, `errorDAT`, and the tap DATs.
- **POP — point operators (96)** — the point-cloud / point-instancing family (e.g. `pointfileinPOP`,
  `toptoPOP`, `soptoPOP`, `dmxfixturePOP`); newer than the TOP-instancing path, so validate params on your
  live build.
- **Utility / introspection (35)** — the shared verbs and lookups: build (`connect`, `set_par`,
  `set_par_many`, `set_flags`, `set_pos`, `delete_op`, `pulse`, `bind_chop`, `write_csv`, `import_scan`,
  `import_segmented_model`), see (`show`, `save_top`, `capture_ui`, `top_info`, `read_network`,
  `find_errors`, `inspect`, `mem`, `scene_info`), reference (`td_capabilities`, `help`,
  `operator_reference`, `recipe_reference`, `glsl_reference`, `expr_reference`, `code_reference`), the
  `batch` meta-tool, the two consent-gated code-lane tools (`set_glsl`/`validate_glsl`,
  `set_expr`/`validate_expr`), and the consent-gated `device_send` (closed PJLink projector control).

NVIDIA/CUDA-only operators are intentionally out of scope (untestable on the AMD-first target).

---

## Common workflows

Every path below is confined to the working directory, and every recipe id is retrievable in full via
`recipe_reference recipe=<id>`.

### Building model → framed, shaded render (`facade_3d_render`)

```
geometryCOMP(op_name="geo")                       # render flag on
fileinSOP(parent_path="/geo", file="assets/building.obj")
delete_op(op="/geo/torus1")                       # drop the default torus child
set_flags(op="<fileinSOP>", render=true, display=true)
constantMAT(op_name="mat", color=[1,1,1])         # a MAT is mandatory or geo renders black
set_par(op="/geo", pars={material:"/mat"})
cameraCOMP(op_name="cam", projection="ortho")
renderTOP(op_name="render", camera="/cam", geometry="geo*", outputresolution="custom", resolutionw=3840, resolutionh=2160)
constantTOP(op_name="bg", color=[0,0,0])          # render output is transparent
overTOP(op_name="beauty")                          # connect render over bg
nullTOP(op_name="OUT_render")
```

The Render TOP references camera/geometry/lights **by parameter**, not by wiring them into its inputs. Its
output is transparent — composite over a dark `constantTOP`. `constantMAT` needs no light; `phongMAT`/
`pbrMAT` render black without one.

### Generative 2D façade content (`generative_texture_fx`)

`noiseTOP` → `rampTOP` → `levelTOP` → `transformTOP` → `compositeTOP` → `feedbackTOP` → `blurTOP` →
`nullTOP (OUT_facade_tex)`. Animate `noiseTOP.tz` (or drive it from an `lfoCHOP`) for evolving noise;
close the trail loop by pointing `feedbackTOP.top` at the downstream composite; give it a fade
(`levelTOP.opacity < 1`) so it converges. This is also the post-process lane for `OUT_render`.

### Video on a segmented façade, one call (`segmented_facade_video_projection`)

Copy the section OBJs under the working dir (`assets/sections/`), then `import_segmented_model` builds the
whole `sec*`/`mat*` rig in one call (it forces the File In SOP to CONSTANT mode so the real OBJ loads, not
TD's default sample box), and `set_par_many` drives a `moviefileinTOP` onto every `matNN.emitmap` with
`emitmapcoord=uv0`. One material per section is mandatory — TD cannot scope a material to part of a merged
mesh.

### Align and hand off to a projector / file / media server (`projection_align_and_output`)

Apply a data-only 4-corner keystone (`cornerpinTOP` pin* corners, `mapping=perspective`), plant an aligned
null, then wire the output — a `windowCOMP` perform window, `moviefileoutTOP` (HAP is the media-server
default codec), `ndioutTOP`, or `syphonspoutoutTOP`. Wire-only: the window is not opened and `record`/
`active` stay off. The on-site 6-point calibration is a human step whose numbers get typed back via
`set_par`.

For the rest — per-section colour/light (`per_section_color_choreography`), height-sweep
(`height_sweep_choreography`), cue shows (`facade_cue_choreography`, `show_control_timecode`), window
masking (`facade_mask_atlas`), multi-projector blend (`multiprojector_edge_blend`), live input
(`realtime_input_driver`), DMX output (`dmx_artnet_output`), mesh warp (`mesh_freeform_warp`), point clouds
(`point_cloud_content`), and calibrated 3D projection (`projector_calibrated_3d`) — start at
`recipe_reference classify=<task>`.

---

## Conventions that make it work

- **Plant `OUT_` nulls.** Each recipe step ends at a landmark null (`OUT_render`, `OUT_facade_tex`,
  `OUT_geo`, …). Downstream FX and the output lane target that **name**, not a node that may move. A
  `nullTOP`/`nullCHOP`/`nullSOP` is the recommended stable tap at the end of every chain.
- **Create-tool tuplets vs `set_par` component names.** An operator's own create tool takes **vector**
  params — `color:[r,g,b]`, `resolution:[w,h]`, `t`/`r`/`s` — which the gateway expands into components.
  `set_par` instead takes the **raw** component names (`colorr`, `tx`, `resolutionw`). Passing raw
  component names or a `pars{}` wrapper *to a create tool* silently drops them (they are not create-tool
  params); passing a tuplet vector to `set_par` likewise won't land. Match the convention to the tool.
- **`bind_chop` for CHOP-driven animation.** To animate a parameter from a CHOP channel (an LFO, audio
  level, a timer), use `bind_chop` — a data *binding*, not code, fully inside the data-only boundary.
  Reach for the expression lane only for logic a binding cannot express.
- **The 256px resolution trap is real.** TOP generators (`noiseTOP`, `rampTOP`, `constantTOP`) default to
  256px, and the whole chain inherits it. For 4K delivery set `outputresolution=custom` +
  `resolutionw`/`resolutionh` on the **source**. (Non-commercial builds cap output at 1280.)
- **The governor is advisory.** `set_par` (and every create tool, which lowers to `set_par`) attaches a
  `{level: ok|caution|heavy, note}` flag when the requested resolution / instance count / render passes are
  sizeable for a realtime GPU — down-scale to the deliverable's budget, don't build to the tool ceiling.
  Only a genuinely catastrophic, driver-killing magnitude is hard-refused; call `mem` for the live band.

---

## Code lanes & teaching tools

The bridge is data-only, but TouchDesigner's art often needs code — a GPU shader or a self-computing
parameter. Those enter through two narrow, **default-off, consent-gated** lanes, and the assistant's job is
to *surface and teach* the opportunity, never to reach for raw code on its own:

- **GLSL** (`glsl_reference` → `set_glsl` / `validate_glsl`, flag `allow_glsl`) — custom per-pixel fields,
  FX, warp/displacement, and feedback the fixed TOPs can't express. Runs in the GPU sandbox; worst case is
  a recoverable driver reset.
- **Parameter expressions** (`expr_reference` → `set_expr` / `validate_expr`, flag `allow_expr`) — a
  single-line Python expression that computes a parameter live. This is the most-gated lane (it evaluates
  as host Python) and ships experimental; prefer `bind_chop` for anything a CHOP can drive.
- **DAT / callback Python** — full CPython, the excluded raw path. The AI proposes and teaches the code but
  **never delivers it through a tool**; you paste it into the DAT yourself.

`code_reference` is the front door — which lane carries what, the shared consent handshake, and the
`glsl_opportunity` / `expr_opportunity` cues that recipe steps flag. **The AI cannot enable its own lane;**
the consent flags live in the off-limits config dir and only a human at the GUI (or the arm bootstrap) can
flip them. When a lane is off, the AI proposes the text and explains it, and you paste it — the boundary
holds either way.

---

## Learn TouchDesigner with it

The typed surface is a learning scaffold: the AI can only reach *real TouchDesigner operators* organized
the way the application is, so the surface that bounds it also teaches you the structure. Every call streams
into the gateway GUI's live audit log, so you watch the network built node by node in your running session.
Mistakes are visible and cheap — nothing runs arbitrary code or touches files outside your project folder,
so a wrong node is something you catch and correct, not a disaster. Ask for something, watch how it wires
the graph, and use `recipe_reference` as the built-in tutor of how real façade content is built.

---

## Operating gotchas

- **Renders are wire-only.** `moviefileoutTOP`, `ndioutTOP`, and live sends are built with `record`/
  `active` **off** — the assistant never fires them. `save_top`, `write_csv`, and `capture_ui` *do* write
  files (into the working dir). `moviefileoutTOP.record` and `dmxoutCHOP.active` in particular are your
  gated actions.
- **A MAT is mandatory** or geometry renders black; `phongMAT`/`pbrMAT` also need a light. Delete the
  `geometryCOMP` default torus child before setting the File In SOP's render flag, or both compete to be
  rendered.
- **The Render TOP takes camera/geometry/lights by parameter**, not by input wire — its only input wiring
  is downstream compositing over a dark plate.
- **Numbers are clamped, not rejected** — an out-of-range value is pinned to the allowed min/max, so a
  "success" with an odd result may have been clamped.
- **`import_segmented_model` needs the OBJs under the working dir** — external paths are blanked by path
  confinement. Sections must carry UVs for `emitmapcoord=uv0` video mapping to register.
- **File paths must sit inside the working directory** — anything outside (or the config dir, or the
  executor package) is refused, even inside the working dir for the trust-root files.
