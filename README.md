# touchdesigner-bridge-mcp

**Drive TouchDesigner from an AI chat — a security-first, *data-only* control surface for building projection-mapping content, with no arbitrary-code path.**

A Windows-native MCP ([Model Context Protocol](https://modelcontextprotocol.io)) server that lets an AI
chat client build [TouchDesigner](https://derivative.ca) operator networks — through a fixed catalog of
**typed, schema-validated tools with no `execute_python`-style path**. The assistant assembles and tunes the
operator graph (TOPs, CHOPs, SOPs, COMPs, MATs, DATs, POPs) that produces mapped video for a physical
surface; **you** — the human at the machine — fire the cooks and renders and decide when the bridge is
armed. It is built for one job: **exterior building-surface projection-mapping content**, from 3D façade
renders and generative texture FX through per-section choreography, projector keystone/edge-blend, DMX/show
control, and the wire-only hand-off to a media server (Pixera / disguise). One binary runs alongside
TouchDesigner and your AI client; that, plus one line pasted into TouchDesigner, is the whole install.

> **Status:** **v0.1.0** — a Rust MCP gateway plus an in-TouchDesigner Python executor, modeled on the
> author's Houdini bridge. **Target:** a licensed TouchDesigner install, build **2023.11k+** or
> **2025.30k+**. The pipeline is **AMD-first** — NVIDIA/CUDA-only operators are intentionally out of scope
> because they can't be tested on the target hardware. Windows-first.

---

## Quickstart

**New to this / not a coder?** You do not need to live in a terminal. Getting running is: build (or obtain)
one program, click a couple of buttons in its window, launch TouchDesigner and paste one small line into its
Textport, then paste one JSON block into your AI client. That's the whole job. The numbered **Steps 1–5**
below walk each part in detail.

What you'll need on hand: a licensed **TouchDesigner** (2023.11k+ or 2025.30k+), a **Windows** PC, and an AI
client that speaks MCP (Claude Desktop, etc.). One folder on your disk becomes the **working directory** the
AI is allowed to read and write — you pick it in Step 2.

Three moves to a working setup (full detail in Steps 1–5):

1. **Get the gateway** — the one program that connects your AI to TouchDesigner. Either **download** the
   prebuilt `touchdesigner-bridge-mcp.exe` from Releases (no coding), or **build** it from source
   (`cargo build --release --manifest-path gateway/Cargo.toml`, needs the Rust toolchain). It's a single
   file that is both a small GUI and the headless MCP server.
2. **Configure & arm** — run the gateway (it opens a GUI), set your **working directory** and click
   **Apply**, then in TouchDesigner paste one arm line into the **Textport** (`Alt+T`) to arm the executor.
3. **Connect your AI client** — paste one small JSON block into its config pointing at the gateway binary,
   fully restart the client, then run the Step 5 check to confirm the AI can see your TouchDesigner scene.

> **Fast path:** `python scripts/setup.py` builds the gateway and prints your filled-in Claude Desktop
> config **and** your Textport arm command with real paths for this machine.

---

## Why it's different

Most TouchDesigner/Blender/Houdini MCP servers control the application by shipping an `execute_python`-style
tool — arbitrary code execution by design. This one is the inverse:

- **Data-only by construction.** The assistant can only call a fixed registry of **544** typed, validated
  tools. There is deliberately **no** arbitrary-code tool, **no** raw-script path, and **no** free-form
  code sink — those simply do not exist in the catalog, so the boundary cannot be talked past. The set of
  things the server can do *is* the enumerated tool list. A runtime canary (`assert_no_rce_endpoints`)
  refuses to arm if any handler even *looks* like a code-execution endpoint, and a build-time fence
  (`catalog_never_exposes_rce_tools`) fails the build if a code-carrying tool ever reaches the catalog.
- **One create-and-configure tool per operator.** Every TouchDesigner operator type has its own typed tool
  (e.g. `blurTOP`, `noiseCHOP`, `gridSOP`) that creates the node and sets its parameters in a single call.
  Ranges are clamped, menu parameters are fixed to their token set, and file paths are confined to the
  working directory before anything is written. Values only — never expressions or code.
- **You fire the heavy work.** The bridge builds and wires render, record, and network-send graphs; output
  is **wire-only** by design — the export op is left with `record`/`active` **off**, and you (or your media
  server) fire it. The assistant never triggers a render, a file bake, or a live send on its own.
- **One working directory.** Every file read and write is `realpath`-confined to a single project folder you
  choose. Nothing outside it is reachable, even through a symlink or junction.
- **Validated code lanes are the exception, not an escape hatch.** The only paths that admit any code are two
  narrow, **default-off**, consent-gated lanes (a GPU-sandboxed GLSL lane and an experimental parameter-
  expression lane), each validated *before* any write and each off unless a human explicitly enables it. The
  AI cannot enable its own lane. See [Security](#security).

---

## Learn TouchDesigner with an AI

TouchDesigner is deep, and the blank-network moment is where most people bounce off. This bridge is a
guided, low-stakes way to actually *learn* it — you describe what you want, the AI builds it in your live
session, and you watch the operator network take shape.

- **The typed tools are the learning scaffold.** Every capability is a fixed, typed, validated operation, so
  the AI can only reach *real TouchDesigner operators* — it cannot wander outside what the software does or
  invent a step that isn't there. The tool list mirrors how TouchDesigner is organized (the TOP/CHOP/SOP/
  COMP/MAT/DAT/POP families), so the surface that bounds the AI also teaches you how the application is
  structured.
- **You watch it, you don't run it blind.** Every operation streams into the gateway GUI's live audit log,
  so you *see* the AI work step by step in a running TouchDesigner session — the network is built in front
  of you, node by node.
- **Mistakes are visible and cheap.** The typed, inspectable surface makes any wrong node or misjudged
  parameter easy to see, undo, and correct. Nothing it does runs arbitrary code or touches files outside
  your project folder, so a bad step is something you catch and learn from, not a disaster.
- **The recipe layer is a built-in tutor.** `recipe_reference` carries 66 tool-mapped workflow recipes with
  ordered steps, the landmark tap to plant at each stage, and the cheap read to verify it — worked examples
  of how real façade content is built, not a black box.
- **Learn to write code, safely.** When the art needs a GPU shader or a self-computing parameter, the AI
  *surfaces* the opportunity and teaches it — it never reaches for raw code on its own. `glsl_reference`
  teaches GLSL shaders, `expr_reference` the parameter-expression surface, and `code_reference` explains
  which lane carries what and the consent handshake. Code enters only through a consented, validated lane
  (`set_glsl` / `set_expr`, default-off) or your own hands — recipe steps flag these spots with a
  `glsl_opportunity` / `expr_opportunity` cue.

**Honest coverage — what's in scope, and what isn't.** No inflation:

- **Operators:** the catalog exposes **509 operator tools** — a near-complete slice of TouchDesigner's
  operator set on the target rig (TOP 106 / CHOP 137 / SOP 79 / COMP 30 / MAT 10 / DAT 51 / POP 96).
  NVIDIA/CUDA-only operators are intentionally out of scope (untestable on the AMD-first target).
- **Code:** two validated lanes — GLSL shaders (`set_glsl`) and single-line Python parameter expressions
  (`set_expr`) — ship **default-OFF** behind explicit consent; the `*_reference` tools teach them and
  propose text. DAT/callback Python is paste-handoff only — proposed and taught, never executed by the bridge.
- **Workflows:** **66 tool-mapped recipes** across 10 projection-mapping domains. The core façade lane
  (per-section rig, video-on-surfaces, choreography, masking, real-time input, alignment, output hand-off)
  is live-proven; several recipes (multi-projector blend, and parts of the generative / point-cloud / camera
  lanes) are built and mechanism-verified but not yet exhaustively live-swept — flagged here rather than
  overstated.
- **Deliberately out of scope:** any arbitrary-code path (there is none, by design), and TouchDesigner
  domains outside projection-mapping content creation.

---

## What you can do with it

Once the gateway is running and TouchDesigner is armed, you can say things like this directly in your AI
chat client (paths are relative to the one working directory you configure):

- *"Render this building `.obj` through an ortho camera onto a dark plate at 4K."*
- *"Build a generative noise-and-feedback field and tap it as façade content."*
- *"Import `section_00.obj … section_07.obj` and blast this clip onto each façade surface."*
- *"Animate a bottom-to-top light sweep revealing each section over the timeline."*
- *"Load this drone point cloud and instance sprites onto the scanned points."*
- *"Add a 4-corner keystone and wire a HAP file output — I'll press record."*
- *"Split the finished composite across two overlapping projectors with a blended seam."*
- *"Drive Art-Net fixtures from a CHOP in sync with the projection (leave it wire-only)."*
- *"Wire an OSC input to drive per-section emission in real time."*
- *"Set up a timer-driven cue show with GO, looping, and LTC timecode sync."*
- *"Warp the composite to follow a curved surface with a control-grid remap."*
- *"What operators are in the scene right now, and how much memory is TouchDesigner using?"*

---

## How it works

```
  AI / MCP client  ──stdio──▶  touchdesigner-bridge-mcp  (one binary: GUI + headless MCP gateway)
                                        │  loopback HTTP  (127.0.0.1:9980, X-TDMCP-Token)
                                        ▼
                               a data-only executor armed inside your live TouchDesigner session
```

Two processes make up the bridge, and they rendezvous through a single file,
`~/.touchdesigner-bridge-mcp/arm.json`:

- **The gateway** (`gateway/`, a Rust binary) — the sole AI entry point and the whole client-side install.
  It is **both** a small GUI (set the working directory, watch a live audit log of every call) and the
  headless MCP server your AI client talks to over stdio. Which one it becomes is chosen at launch by the
  `TDMCP_GW_HEADLESS` environment variable. It owns the typed schema, input validation
  (clamp / enum / required-key / path-confine), and lowers each operator tool to a generic `create_op` +
  `set_par` before relaying it to the executor over loopback HTTP. This is the single security choke point
  on the way in.
- **The executor** (`td_executor/`, Python) — a small, data-only handler registry armed *inside* a running
  TouchDesigner session by `arm.py`. It applies validated requests to the operator graph on TouchDesigner's
  main thread. It uses only the Python standard library and TouchDesigner's own built-in `td` module — **no
  third-party packages are bundled or redistributed**, nothing to `pip install`.

Because the gateway exposes 509 typed operator tools but *lowers* every one of them onto the same two
executor verbs (`create_op` + `set_par`), the entire surface funnels through one validated parameter guard.
See [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Requirements

1. **TouchDesigner** — a licensed install, build **2023.11k+** or **2025.30k+**. The executor runs inside
   TouchDesigner's own embedded Python.
2. **A Rust toolchain** (stable) — only if you **build** the gateway yourself; skip it if you download the prebuilt `.exe` from Releases. [rustup.rs](https://rustup.rs) provides `cargo`.
3. **Python 3.10+** — only to run the optional `scripts/setup.py` helper (and the executor test suite). The
   executor itself runs in TouchDesigner's embedded Python, not this one.
4. **An MCP client** — Claude Desktop, or any client that can launch a stdio MCP server.
5. **Windows** — the primary platform. The target pipeline is **AMD-first**; NVIDIA/CUDA-only operators are
   intentionally out of scope.

---

## Step 1 — Get the gateway

**Prefer no coding?** Download the prebuilt `touchdesigner-bridge-mcp.exe` from Releases and skip straight to
Step 2 — it is the same single binary the build produces. **Want to build from source instead** (or there's
no prebuilt binary for your setup yet)? Build it from source:

```sh
cargo build --release --manifest-path gateway/Cargo.toml
# or, from gateway/:
cargo build --release
```

This produces one file:

```
gateway/target/release/touchdesigner-bridge-mcp.exe      (Windows)
```

The gateway is both the GUI and the headless MCP server. Which mode it runs in is selected at launch by one
environment variable, `TDMCP_GW_HEADLESS` — **unset** opens the GUI window; **`1`** runs the headless stdio
server. (You won't normally set this by hand: double-clicking the file opens the GUI, and the config block in
Step 3 sets the headless flag for your AI client.)

`gateway/src/tools.rs` is **generated** from `reference/catalog.json`, never hand-edited.

---

## Step 2 — Configure via the GUI

The **working directory** is the single folder the tool may read from and write to — every executor file
operation is confined under it. It is deliberately **not** the source tree.

Run the gateway binary **with no arguments** to open the GUI, then:

1. Open the **Working dir** pane, enter (or paste) an existing folder, and click **Apply**. This updates the
   confinement root live for every future call and merge-writes `working_dir` into
   `~/.touchdesigner-bridge-mcp/arm.json`.
2. Leave the GUI running — its **Status** pane shows an "Armed" pill and the live TouchDesigner build once
   the executor (Step 4) is reachable, and its live audit log lets you watch every call.

Changing the working directory later is just **Apply** again — no restart, no re-arm.

> **No firewall step is required.** The in-TouchDesigner Web Server DAT binds `127.0.0.1` only — nothing
> listens off-box, so there is no inbound rule to add.

---

## Step 3 — Register with your MCP client

Point your client (e.g. Claude Desktop — edit `%APPDATA%\Claude\claude_desktop_config.json`) at the gateway
binary in **headless** mode. Copy the template at
[`claude_desktop_config.example.json`](docs/claude_desktop_config.example.json) and replace both placeholders with
your clone path (forward slashes, even on Windows):

```json
{
  "mcpServers": {
    "touchdesigner": {
      "command": "C:/path/to/touchdesigner-bridge-mcp/gateway/target/release/touchdesigner-bridge-mcp.exe",
      "env": {
        "TDMCP_GW_HEADLESS": "1",
        "TDMCP_REPO": "C:/path/to/touchdesigner-bridge-mcp"
      }
    }
  }
}
```

Both env vars are load-bearing:

- **`TDMCP_GW_HEADLESS=1`** — required, or the client would spawn a GUI window and the MCP handshake would
  never complete.
- **`TDMCP_REPO`** — the clone path, so the gateway finds its bundled reference data
  (`reference/recipes.json`, `reference/catalog.json`) deterministically.

Fully quit and reopen the client after editing the config.

---

## Step 4 — Arm the executor inside TouchDesigner

Open TouchDesigner, open the **Textport** (`Alt+T`), and paste the arm command, substituting your clone path:

```python
import os; os.environ['TDMCP_REPO']=r'C:/path/to/touchdesigner-bridge-mcp'; exec(open(os.path.join(os.environ['TDMCP_REPO'],'arm.py')).read())
```

You don't have to type it by hand — the GUI shows this exact line with a **Copy arm command** button, and
`python scripts/setup.py` prints it too.

What arming does (`arm.py`):

- **Verifies** the on-disk executor files against `td_executor/INTEGRITY.json` **before** importing them
  (fail-closed tamper-evidence).
- **Refuses** to arm if any handler looks like a raw code-execution endpoint (the data-only canary).
- **Mints** a 128-bit CSPRNG session token and writes `token` / `port` / `working_dir` (and preserves the
  consent flags) into `arm.json`.
- **Assembles** a `/mcp_bridge` component — a **Web Server DAT** on loopback `127.0.0.1:9980` plus a thin
  callbacks DAT that loads the on-disk `td_executor` package — and adds GUI **consent toggles** (Allow Expr
  Lane / Allow GLSL Lane) that persist to `arm.json`.

**Re-arm any time** to hot-reload on-disk executor edits (it purges the module cache first). **Remove** the
bridge with `op('/mcp_bridge').destroy()`. To make arming persistent per project, see
[`td_package/README.md`](td_package/README.md).

---

## Step 5 — Verify your setup

1. **Executor health** — with TouchDesigner armed, open in a browser or curl:

   ```
   http://127.0.0.1:9980/health
   ```

   The GUI's **Status** pane also shows the "Armed" pill and the live TouchDesigner build once the executor
   is reachable.
2. **Tools appear in the client** — in a new client chat, ask the assistant to run `scene_info`. A
   successful reply (the current scene, TouchDesigner build) confirms the whole path is live:
   client → gateway → loopback → executor → TouchDesigner.

If both return, you are wired. See [docs/INSTALL.md](docs/INSTALL.md) for the full walkthrough, including
enabling the code lanes and troubleshooting.

---

## Available tools

**544 tools total** — **509 operator tools** across 7 TouchDesigner families plus **35 utility tools**, listed
in full below (also in [docs/TOOL_CATALOG.md](docs/TOOL_CATALOG.md)). Every tool is a validated handler; no
free-form code path exists. For any operator's full typed parameter schema, ask the `operator_reference` tool
live in-session (`optype=<name>`).

Every operator tool creates and configures one operator type in a single call, and also accepts four reserved placement args: `op_name`, `parent_path` (default `/`), `pos_x`, `pos_y`. The tables below are the exact current catalog, generated from `reference/catalog.json`.

### TOP — image / texture (GPU raster)  (106)

| Tool | Parameters | Description |
|---|---:|---|
| `addTOP` | 26 | Composites two input images by summing their pixel values, clamping channels that overflow; a simple additive blend often used to brighten or combine light passes. |
| `analyzeTOP` | 19 | Reduces an image to a single value, row, or column by taking a statistic such as minimum, maximum, or average across the pixels. |
| `antialiasTOP` | 21 | Smooths jagged, stair-stepped edges in an image with a post-process anti-aliasing filter. |
| `blobtrackTOP` | 37 | Detects bright blobs in an image and reports their positions and sizes, a lightweight optical tracker for interactive installations. |
| `blurTOP` | 23 | Applies a Gaussian or box blur; the filter width parameter sets how many pixels are averaged together. |
| `cacheTOP` | 26 | Stores a rolling buffer of recent frames on the GPU so earlier frames can be replayed or sampled by a Cache Select. |
| `cacheselectTOP` | 16 | Reads a chosen frame out of a Cache TOP's stored buffer by index. |
| `channelmixTOP` | 19 | Rebuilds each output channel as a weighted mix of the input channels, useful for channel swaps and custom color matrices. |
| `choptoTOP` | 19 | Converts CHOP channels into an image, writing sample values into pixels so numeric data can be visualized or fed to shaders. |
| `chromakeyTOP` | 29 | Keys out a chosen background color (green/blue screen) to produce an alpha matte for compositing. |
| `circleTOP` | 42 | Draws a filled or outlined circle or ellipse directly as an image. |
| `compositeTOP` | 34 | Layers many inputs together with a selectable blend operation, the multi-input equivalent of the two-input Over/Add TOPs. |
| `constantTOP` | 23 | Outputs a flat image of one solid color and alpha at the chosen resolution. |
| `convolveTOP` | 20 | Filters the image with a user-defined convolution kernel supplied as a small matrix, for custom sharpen/blur/edge effects. |
| `cornerpinTOP` | 36 | Warps the image by dragging its four corners to arbitrary positions, the standard keystone/quad-warp for projection alignment. |
| `cropTOP` | 23 | Extracts a rectangular sub-region of the image, changing the output to that crop. |
| `crossTOP` | 27 | Cross-dissolves between two inputs by a single blend amount. |
| `cubemapTOP` | 15 | Assembles or rearranges the six faces of a cubemap for environment mapping and reflections. |
| `depthTOP` | 23 | Extracts the depth buffer from a Render TOP so scene distance can be used for fog, depth-of-field, or masking. |
| `differenceTOP` | 26 | Outputs the absolute per-pixel difference between two inputs, handy for change detection. |
| `directdisplayoutTOP` | 20 | Sends the image to a directly attached display bypassing the desktop compositor for low-latency output. |
| `directxinTOP` | 15 | Receives a shared GPU texture from another application via DirectX shared surfaces. |
| `directxoutTOP` | 16 | Publishes the image as a shared DirectX texture for another application to read. |
| `displaceTOP` | 25 | Offsets each pixel's lookup position using a second image as a displacement map, warping the picture. |
| `edgeTOP` | 25 | Detects edges by measuring local contrast, producing an outline image. |
| `embossTOP` | 21 | Produces a raised, embossed relief look by shading from local intensity gradients. |
| `feedbackTOP` | 16 | Feeds a downstream result back into the graph one frame later, the building block for trails, accumulation, and reaction-diffusion loops. |
| `fitTOP` | 27 | Resizes and fits the input into the output resolution with a chosen fit/stretch mode. |
| `flipTOP` | 18 | Flips or mirrors the image horizontally and/or vertically. |
| `glslTOP` | 59 | Runs a custom GLSL fragment shader over the output pixels, with the shader source supplied through a referenced Text DAT rather than an inline code value. |
| `glslmultiTOP` | 59 | A GLSL fragment shader TOP that exposes multiple image inputs for multi-texture effects. |
| `hsvadjustTOP` | 24 | Shifts hue and scales saturation and value, the go-to color-grade for tint and vibrance. |
| `hsvtorgbTOP` | 14 | Interprets the input channels as HSV and converts them to RGB. |
| `inTOP` | 16 | The input tap of a TOP component, exposing an external image inside the subnetwork. |
| `insideTOP` | 26 | Keeps the first input only where the second input's matte is opaque (source-in compositing). |
| `kinectazureTOP` | 46 | Captures depth, color, and infrared image streams from an Azure Kinect sensor. |
| `layermixTOP` | 34 | Blends stacked layers with per-layer opacity and blend modes. |
| `layoutTOP` | 36 | Tiles multiple inputs into a single grid image, sized by rows and columns. |
| `lensdistortTOP` | 38 | Adds or removes barrel/pincushion lens distortion. |
| `levelTOP` | 45 | Adjusts brightness, contrast, gamma, black/white levels, and opacity, the workhorse tone control. |
| `lookupTOP` | 25 | Remaps each pixel through a lookup table supplied as a second image, driving color grades and gradient mapping. |
| `lumablurTOP` | 21 | Blurs by an amount that varies with local luminance, so bright or dark regions smear more. |
| `lumalevelTOP` | 32 | Adjusts levels based on luminance, useful for isolating highlights or shadows. |
| `mathTOP` | 37 | Performs per-pixel arithmetic (add, multiply, difference, and more) between inputs and constants. |
| `matteTOP` | 16 | Applies one input as the alpha matte of another to cut out a shape. |
| `mirrorTOP` | 20 | Reflects the image about a chosen axis to build kaleidoscopic symmetry. |
| `monochromeTOP` | 18 | Collapses color to a single grayscale channel using a chosen luminance weighting. |
| `moviefileinTOP` | 63 | Loads and plays back a movie or still-image file, with parameters for the file path, playback rate, and trim. |
| `moviefileoutTOP` | 70 | Records the incoming image stream to a movie or image-sequence file; this bridge only wires it up, the user triggers the actual recording. |
| `multiplyTOP` | 26 | Multiplies the pixel values of its inputs, a modulate/darken blend. |
| `ndiinTOP` | 24 | Receives video over the network as an NDI source. |
| `ndioutTOP` | 26 | Publishes the image on the network as an NDI stream. |
| `noiseTOP` | 41 | Generates procedural noise images (Perlin, simplex, and others) with animatable transform and harmonics. |
| `normalmapTOP` | 20 | Derives a tangent-space normal map from a height or grayscale image for surface detail lighting. |
| `notchTOP` | 29 | Plays and controls a Notch effects block (.dfxdll), exposing its exposed parameters. |
| `nullTOP` | 14 | A pass-through placeholder used as a stable tap point at the end of an image chain. |
| `opencolorioTOP` | 40 | Applies an OpenColorIO color-space transform for color-managed pipelines. |
| `opviewerTOP` | 17 | Renders another operator's node viewer into an image so any operator can be seen as a TOP. |
| `orbbecTOP` | 31 | Captures depth and color image streams from an Orbbec depth camera. |
| `orbbecselectTOP` | 19 | Selects and extracts one image stream, such as depth or color, from an Orbbec TOP. |
| `outTOP` | 17 | The output tap of a TOP component, exporting an image out of the subnetwork. |
| `outsideTOP` | 26 | Keeps the first input only where the second input's matte is transparent (source-out compositing). |
| `overTOP` | 26 | Composites the first input over the second using standard alpha-over blending. |
| `packTOP` | 15 | Packs pixel data into a specific layout or bit format for transport or GPU readback. |
| `photoshopinTOP` | 21 | Links live to a Photoshop document, bringing its layers in as an image. |
| `pointfileinTOP` | 56 | Loads point-cloud file data into a texture where each pixel encodes a point's position or attribute. |
| `pointtransformTOP` | 61 | Transforms 3D point data stored in a texture, applying translation, rotation, scale, or an alignment matrix to each point. |
| `prefiltermapTOP` | 15 | Pre-convolves an environment map into the mip levels a PBR material needs for glossy reflections. |
| `projectionTOP` | 18 | Converts between projection layouts such as equirectangular, cubemap, and fisheye. |
| `rampTOP` | 32 | Generates linear, radial, or circular gradients from an editable color ramp. |
| `realsenseTOP` | 40 | Captures depth, color, and infrared image streams from an Intel RealSense depth camera. |
| `rectangleTOP` | 39 | Draws a filled or outlined rectangle with adjustable size, position, and corner rounding. |
| `remapTOP` | 24 | Warps the first input by looking up coordinates stored in a second input's pixels. |
| `renderTOP` | 67 | Renders 3D geometry from a camera with lights and materials into an image, the heart of the 3D-to-2D lane. |
| `renderpassTOP` | 56 | Adds an extra render pass (such as a separate layer or buffer) to a Render TOP. |
| `renderselectTOP` | 20 | Selects one output buffer or pass from a multi-output Render TOP. |
| `rendersimpleTOP` | 31 | A one-node render that bundles a camera, light, and geometry for quick 3D previews. |
| `renderstreaminTOP` | 18 | Receives frames from a disguise RenderStream host, bringing an externally rendered image in over the network. |
| `renderstreamoutTOP` | 19 | Sends the image out to a disguise RenderStream host as a rendered output stream. |
| `reorderTOP` | 22 | Rearranges, duplicates, or fills the RGBA channels from the inputs. |
| `resolutionTOP` | 15 | Resamples the image to a new resolution using a chosen filter. |
| `scalabledisplayTOP` | 20 | Applies a Scalable Display Technologies warp-and-blend calibration for multi-projector setups. |
| `screenTOP` | 26 | Composites two inputs with the Screen blend mode for a brightening effect. |
| `screengrabTOP` | 27 | Captures the desktop or a display region into an image. |
| `sharedmeminTOP` | 18 | Reads an image from a shared-memory block written by another process. |
| `sharedmemoutTOP` | 20 | Writes the image into a shared-memory block for another process to read. |
| `slopeTOP` | 24 | Computes the local gradient (slope) of the image, often a precursor to normal maps or edge shading. |
| `spectrumTOP` | 18 | Transforms the image to and from its frequency spectrum via FFT for frequency-domain filtering. |
| `st2110inTOP` | 31 | Receives uncompressed video over IP following the SMPTE ST 2110 standard. |
| `st2110outTOP` | 47 | Transmits uncompressed video over IP following the SMPTE ST 2110 standard. |
| `substanceTOP` | 19 | Renders a Substance (.sbsar) procedural material, exposing its published parameters. |
| `subtractTOP` | 26 | Subtracts the second input's pixels from the first. |
| `switchTOP` | 17 | Passes through one of several inputs chosen by an index, for A/B switching and sequencing. |
| `syphonspoutinTOP` | 16 | Receives a shared GPU texture via Syphon (macOS) or Spout (Windows). |
| `syphonspoutoutTOP` | 16 | Publishes the image as a shared GPU texture via Syphon (macOS) or Spout (Windows). |
| `textTOP` | 78 | Renders text into an image with control over font, size, alignment, and color. |
| `tileTOP` | 36 | Tiles and repeats the input across the output, with adjustable repeat counts, offset, overlap, and flip for seamless patterns. |
| `touchinTOP` | 20 | Receives an image from another TouchDesigner instance over the network. |
| `touchoutTOP` | 20 | Sends the image to another TouchDesigner instance over the network. |
| `transformTOP` | 30 | Translates, rotates, scales, and tiles the image within its frame. |
| `underTOP` | 26 | Composites the second input over the first (the reverse of Over). |
| `videodeviceinTOP` | 55 | Captures live video from a camera or capture card. |
| `videodeviceoutTOP` | 35 | Outputs the image to an SDI/HDMI or other hardware video device. |
| `videostreaminTOP` | 31 | Receives a compressed video stream such as RTSP, RTMP, or SRT. |
| `viosoTOP` | 21 | Applies a VIOSO projection warp-and-blend calibration for multi-projector alignment. |
| `webrenderTOP` | 34 | Renders a web page or URL into an image using an embedded browser. |

### CHOP — channels / signals / timing  (137)

| Tool | Parameters | Description |
|---|---:|---|
| `abletonlinkCHOP` | 28 | Synchronizes tempo, phase, and beat with peers on the network over Ableton Link. |
| `analyzeCHOP` | 14 | Reduces channels to a single statistic per channel such as average, maximum, or length. |
| `angleCHOP` | 13 | Converts between angle representations, for example degrees, radians, quaternions, and direction vectors. |
| `attributeCHOP` | 11 | Tags channels with attributes such as rotation order or quaternion type that downstream operators respect. |
| `audiobandeqCHOP` | 26 | A multi-band graphic equalizer that boosts or cuts fixed frequency bands of an audio signal. |
| `audiobinauralCHOP` | 14 | Spatializes audio into a binaural (headphone 3D) mix from source and listener positions. |
| `audiodeviceinCHOP` | 40 | Captures audio samples from an input device such as a microphone or interface. |
| `audiodeviceoutCHOP` | 41 | Plays channels as audio out to an output device. |
| `audiodynamicsCHOP` | 25 | Applies compression, limiting, and gating dynamics to an audio signal. |
| `audiofileinCHOP` | 31 | Reads audio samples from a file for playback or analysis. |
| `audiofileoutCHOP` | 18 | Writes audio channels to a sound file. |
| `audiofilterCHOP` | 16 | Filters an audio signal with low-pass, high-pass, band-pass, or band-reject response and an adjustable cutoff. |
| `audiomovieCHOP` | 19 | Extracts the audio track that accompanies a movie played by a Movie File In TOP. |
| `audiondiCHOP` | 11 | Sends or receives audio embedded in an NDI stream. |
| `audiooscillatorCHOP` | 20 | Generates audio-rate tones and waveforms from a frequency input. |
| `audioparaeqCHOP` | 26 | A parametric equalizer with adjustable center frequency, gain, and Q per band. |
| `audioplayCHOP` | 37 | Plays back a sound file on demand, often triggered by an event. |
| `audiorenderCHOP` | 48 | Renders a spatial audio scene from sound sources and a listener into output channels. |
| `audiospectrumCHOP` | 15 | Computes the frequency spectrum of an audio signal via FFT for visualization or reactivity. |
| `audiostreaminCHOP` | 21 | Receives streamed audio from the network. |
| `audiostreamoutCHOP` | 16 | Streams audio channels out over the network. |
| `audiovstCHOP` | 27 | Hosts a VST audio plug-in and exposes its parameters. |
| `audiowebrenderCHOP` | 11 | Captures the audio produced by a Web Render TOP. |
| `beatCHOP` | 34 | Runs a musical clock locked to a tempo, emitting beat ramps, counts, and pulse triggers. |
| `blacktraxCHOP` | 22 | Receives real-time position and orientation data from a BlackTrax tracking system. |
| `blendCHOP` | 12 | Blends multiple input channel sets using weighting channels, for weighted pose or value mixing. |
| `blobtrackCHOP` | 25 | Reports tracked blob positions and sizes as channels. |
| `clipCHOP` | 24 | Plays back recorded channel clips with control over rate and range. |
| `clipblenderCHOP` | 38 | Blends and sequences animation clips into a continuous motion stream. |
| `clockCHOP` | 36 | Outputs wall-clock time components such as hours, minutes, seconds, and frame. |
| `compositeCHOP` | 27 | Overlays and combines channels from multiple inputs, aligning them in time. |
| `constantCHOP` | 22 | Produces channels that hold constant values you type in. |
| `copyCHOP` | 17 | Repeats or copies the first input's channels once per sample of a second input. |
| `countCHOP` | 25 | Counts threshold crossings or triggers on its input and outputs the running total. |
| `crossCHOP` | 10 | Cross-fades between two channel sets by a blend amount. |
| `cycleCHOP` | 20 | Repeats a channel a number of times, optionally blending the seams into a seamless loop. |
| `dattoCHOP` | 31 | Reads a DAT table and turns its rows or columns into channels. |
| `delayCHOP` | 12 | Delays channels by a fixed time offset. |
| `deleteCHOP` | 32 | Removes selected channels or trims samples by name pattern or range. |
| `dmxinCHOP` | 31 | Receives lighting-control data (DMX over Art-Net or sACN) as channels. |
| `dmxoutCHOP` | 34 | Sends channels out as DMX lighting-control data. |
| `envelopeCHOP` | 17 | Follows the moving amplitude envelope (peak or RMS) of a signal. |
| `eventCHOP` | 33 | Turns discrete events into channels with lifespans and shapes. |
| `expressionCHOP` | 13 | Applies a per-channel math expression; note that the code-carrying expression value is withheld from this data-only surface, leaving its scope and naming parameters. |
| `extendCHOP` | 12 | Sets how each channel behaves before its first and after its last sample (hold, cycle, mirror, or default). |
| `fanCHOP` | 14 | Fans a single channel out to many, or folds many channels into one, by index. |
| `feedbackCHOP` | 12 | Feeds its own prior output back for recursive, frame-delayed channel processing. |
| `fileinCHOP` | 20 | Reads channel data from a file or URL. |
| `fileoutCHOP` | 12 | Appends or writes channels to a file. |
| `filterCHOP` | 26 | Low-pass smooths channels over time to remove jitter, with an adjustable filter width. |
| `freedinCHOP` | 15 | Receives camera tracking data over the FreeD protocol as pan, tilt, zoom, and position channels. |
| `functionCHOP` | 19 | Applies a mathematical function (trigonometric, logarithmic, power, and so on) to each channel. |
| `gestureCHOP` | 21 | Records a motion and replays it as a reusable gesture channel. |
| `handleCHOP` | 15 | Solves handle-based inverse kinematics for character rigs. |
| `hogCHOP` | 13 | Deliberately consumes cook time to profile and stress-test performance. |
| `hokuyoCHOP` | 19 | Reads distance samples from a Hokuyo laser range scanner. |
| `holdCHOP` | 12 | Samples the first input and holds that value whenever a second trigger input fires. |
| `inCHOP` | 14 | The input tap of a CHOP component. |
| `infoCHOP` | 16 | Exposes another operator's numeric info (cook time, sample counts, and status) as channels. |
| `interpolateCHOP` | 12 | Interpolates smoothly between successive input channel sets or keyframes. |
| `inversecurveCHOP` | 17 | Solves an inverse-kinematics curve for smooth chain bending. |
| `inversekinCHOP` | 17 | Solves two-bone inverse kinematics from a goal position. |
| `joinCHOP` | 27 | Joins channels end to end in time to build a longer sequence. |
| `joystickCHOP` | 31 | Reads axes and buttons from a joystick or game controller. |
| `keyboardinCHOP` | 18 | Reports the pressed state of keyboard keys as channels. |
| `keyframeCHOP` | 14 | Holds an editable keyframe animation and evaluates it into channels. |
| `kinectazureCHOP` | 23 | Tracks skeletal body joints from an Azure Kinect sensor, outputting per-joint position and orientation channels. |
| `lagCHOP` | 22 | Adds inertia, lag, and slew-rate limits so channels ease toward new values. |
| `laserCHOP` | 42 | Prepares path data for laser projection output. |
| `laserdeviceCHOP` | 23 | Drives a laser projector DAC with prepared laser channels. |
| `leuzerod4CHOP` | 25 | Reads distance data from a Leuze ROD4 laser scanner. |
| `lfoCHOP` | 20 | A low-frequency oscillator generating sine, ramp, square, and pulse waves for animation. |
| `limitCHOP` | 24 | Clamps channel values to a range and optionally quantizes them to a step. |
| `logicCHOP` | 16 | Performs boolean logic and comparisons across channels, outputting on/off results. |
| `lookupCHOP` | 17 | Uses the first input as an index into the second input's lookup table. |
| `ltcinCHOP` | 16 | Decodes SMPTE linear timecode from audio into time channels. |
| `ltcoutCHOP` | 29 | Encodes time channels as SMPTE linear timecode audio. |
| `mathCHOP` | 22 | Performs arithmetic and range remapping on channels (add, multiply, from/to ranges). |
| `mergeCHOP` | 11 | Combines the channels of several inputs into one output. |
| `midiinCHOP` | 64 | Brings MIDI notes and controllers in as channels. |
| `midiinmapCHOP` | 19 | Maps incoming MIDI messages to named channels via a mapping table. |
| `midioutCHOP` | 32 | Sends channels out as MIDI notes and controllers. |
| `mosysCHOP` | 16 | Receives camera tracking data from a Mo-Sys system as position, orientation, and lens channels. |
| `mouseinCHOP` | 24 | Reports mouse position and button state as channels. |
| `mouseoutCHOP` | 15 | Drives the system mouse position from channels. |
| `ncamCHOP` | 17 | Receives camera tracking data from an Ncam system as position, orientation, and lens channels. |
| `noiseCHOP` | 41 | Generates coherent procedural noise channels over time. |
| `nullCHOP` | 13 | A pass-through tap, the recommended stable endpoint for exports and references. |
| `objectCHOP` | 42 | Outputs the transform or relationship (position, rotation, distance) between two 3D objects. |
| `optitrackinCHOP` | 19 | Receives rigid-body and marker data from an OptiTrack motion-capture system. |
| `oscinCHOP` | 27 | Receives OSC messages and maps their values to channels. |
| `oscoutCHOP` | 22 | Sends channel values out as OSC messages. |
| `outCHOP` | 12 | The output tap of a CHOP component. |
| `overrideCHOP` | 13 | Passes through whichever input changed most recently, letting several controllers share one output. |
| `panelCHOP` | 14 | Exposes a panel component's interaction values (state, click, roll) as channels. |
| `pangolinCHOP` | 20 | Controls Pangolin laser software from channels. |
| `pantiltCHOP` | 15 | Computes pan and tilt angles to aim a device at a target. |
| `parameterCHOP` | 18 | Reads the evaluated values of an operator's parameters into channels. |
| `patternCHOP` | 32 | Generates a shaped pattern across samples (ramp, Gaussian, sine, and more). |
| `performCHOP` | 36 | Exposes real-time performance statistics such as frame time and cook counts as channels. |
| `phaserCHOP` | 13 | Produces a phase-offset animation signal, shifting channel phase over time. |
| `pipeinCHOP` | 25 | Receives channels over a TCP pipe from another process. |
| `pipeoutCHOP` | 21 | Sends channels over a TCP pipe to another process; its script-carrying parameter is withheld from this data-only surface. |
| `poptoCHOP` | 25 | Reads POP point attributes into channels. |
| `posistagenetCHOP` | 19 | Receives PosiStageNet stage-tracking positions as channels. |
| `pulseCHOP` | 33 | Emits pulse spikes at a set interval or count. |
| `recordCHOP` | 16 | Records incoming channels into a buffer that can be replayed. |
| `renameCHOP` | 9 | Renames channels using from/to name patterns. |
| `renderpickCHOP` | 48 | Picks 3D geometry under given coordinates in a render and returns the hit position and info as channels. |
| `renderstreaminCHOP` | 18 | Receives control data from a disguise RenderStream session. |
| `reorderCHOP` | 18 | Reorders the channels within the stream. |
| `replaceCHOP` | 11 | Replaces channels in the first input with matching-named channels from the second. |
| `resampleCHOP` | 22 | Resamples channels to a new sample rate or time range. |
| `selectCHOP` | 16 | References channels by name from another CHOP anywhere in the project. |
| `serialCHOP` | 17 | Reads and writes a serial (RS-232/USB) device as channels. |
| `shiftCHOP` | 17 | Shifts a channel forward or backward in time. |
| `shuffleCHOP` | 12 | Reshapes the layout between channels and samples (transpose-like operations). |
| `soptoCHOP` | 24 | Reads SOP point or primitive attributes into channels. |
| `sortCHOP` | 16 | Sorts channels or their samples by value or name. |
| `speedCHOP` | 22 | Integrates a speed channel into position, or scales the flow of time. |
| `st2110deviceCHOP` | 31 | Exposes audio and ancillary data of an ST 2110 IP video device as channels. |
| `stretchCHOP` | 18 | Stretches or compresses channels to a new length while preserving their shape. |
| `stypeinCHOP` | 16 | Receives camera tracking data from a Stype system as position, orientation, and lens channels. |
| `switchCHOP` | 12 | Selects one of several inputs by index. |
| `syncinCHOP` | 16 | Receives synchronization timing from a Sync Out CHOP over the network to keep multiple machines frame-locked. |
| `syncoutCHOP` | 20 | Coordinates frame synchronization across multiple machines. |
| `tabletCHOP` | 40 | Reads pen pressure, tilt, and position from a graphics tablet. |
| `timecodeCHOP` | 40 | Represents a timecode value as hour/minute/second/frame channels. |
| `timelineCHOP` | 25 | Outputs the current timeline position in frames and seconds. |
| `timerCHOP` | 73 | A programmable timer with segments, cycles, and done pulses for sequencing. |
| `toptoCHOP` | 39 | Samples pixels from a TOP into channels. |
| `touchinCHOP` | 23 | Receives channels from another TouchDesigner instance over the network. |
| `touchoutCHOP` | 18 | Sends channels to another TouchDesigner instance over the network. |
| `trailCHOP` | 19 | Displays a scrolling history graph of its input channels for monitoring. |
| `triggerCHOP` | 46 | Generates an attack-decay-sustain-release envelope each time the input crosses a threshold. |
| `trimCHOP` | 16 | Trims channels to a start and end time. |
| `waveCHOP` | 30 | Generates periodic waveforms defined by shape and period; its expression-carrying parameter is withheld from this data-only surface. |

### SOP — geometry (surfaces)  (79)

| Tool | Parameters | Description |
|---|---:|---|
| `addSOP` | 17 | Creates individual points and polygons, or connects existing points into new primitives. |
| `alembicSOP` | 11 | Loads geometry from an Alembic (.abc) cache file. |
| `alignSOP` | 17 | Aligns input geometries to one another by bounding box or transform. |
| `armSOP` | 34 | Builds or edits an articulated arm/chain of geometry. |
| `attributeSOP` | 13 | Creates, renames, deletes, or edits point, vertex, primitive, and detail attributes. |
| `attributecreateSOP` | 4 | Adds new attributes to geometry and initializes their values. |
| `basisSOP` | 30 | Edits the parametric basis (knot vector and order) of NURBS curves and surfaces. |
| `blendSOP` | 9 | Blends point positions between topologically matching inputs by weight, for shape interpolation. |
| `bonegroupSOP` | 3 | Creates point groups based on skeletal bone capture regions. |
| `booleanSOP` | 6 | Computes boolean union, intersection, or difference between solid meshes. |
| `boxSOP` | 18 | Generates a box or cube with adjustable size and divisions. |
| `bridgeSOP` | 15 | Builds a skin surface bridging between edge loops or profiles. |
| `cacheSOP` | 9 | Holds a buffer of geometry frames in memory for replay. |
| `capSOP` | 16 | Caps the open ends of tubes and surfaces with flat or rounded faces. |
| `captureSOP` | 9 | Assigns capture weights binding geometry points to a skeleton for skinning. |
| `captureregionSOP` | 9 | Defines the capture influence region of a bone. |
| `carveSOP` | 20 | Cuts, slices, or extracts portions of curves and surfaces along their parametric coordinates. |
| `choptoSOP` | 10 | Creates or drives geometry from CHOP channels, for example animating points from channel data. |
| `circleSOP` | 16 | Generates a circle or arc as a curve or polygon. |
| `claySOP` | 24 | Deforms a surface by pushing and pulling its control points like modeling clay. |
| `clipSOP` | 8 | Clips geometry against a plane, keeping one side or splitting at the cut. |
| `convertSOP` | 17 | Converts geometry between types such as polygon, mesh, NURBS, and Bezier. |
| `copySOP` | 35 | Copies geometry onto template points or with a stack of transforms, the core instancing/stamping SOP. |
| `creepSOP` | 5 | Slides and deforms geometry so it crawls along the surface of another. |
| `curveclaySOP` | 13 | Sculpts curves by pulling their control points. |
| `curvesectSOP` | 11 | Finds intersections between curves and surfaces, outputting the crossing points. |
| `dattoSOP` | 15 | Builds geometry (points and primitives) from the rows of a DAT table. |
| `deformSOP` | 6 | Deforms captured geometry to follow its skeleton (the skinning deform step). |
| `deleteSOP` | 21 | Deletes points or primitives selected by group, number, or bounding volume; its per-element filter expression is withheld from this data-only surface. |
| `divideSOP` | 14 | Subdivides, triangulates, or bricks polygons to change their tessellation. |
| `extrudeSOP` | 21 | Extrudes faces or edges to add depth and beveling. |
| `facetSOP` | 14 | Controls normals and faceting, uniquing points and cusping edges for flat or smooth shading. |
| `fileinSOP` | 5 | Loads geometry from a file such as OBJ. |
| `filletSOP` | 16 | Creates rounded fillet surfaces or curves between two inputs. |
| `fitSOP` | 17 | Fits a NURBS curve or surface through a set of points. |
| `forceSOP` | 8 | Defines a force field that particle and metaball systems respond to. |
| `fractalSOP` | 9 | Displaces points with fractal noise to roughen a surface. |
| `gridSOP` | 18 | Generates a flat grid of points or polygons with adjustable rows and columns. |
| `groupSOP` | 46 | Creates named point or primitive groups by pattern, number, or bounding region; its per-element filter expression is withheld from this data-only surface. |
| `holeSOP` | 6 | Turns enclosed faces into holes in their surrounding face. |
| `inSOP` | 3 | The input tap of a SOP component. |
| `inversecurveSOP` | 2 | Computes an inverse curve solution for chained geometry. |
| `isosurfaceSOP` | 6 | Builds a surface at a constant value of an implicit 3D function. |
| `joinSOP` | 12 | Joins multiple curves or surfaces into single continuous primitives. |
| `jointSOP` | 12 | Creates a skeleton of joints and bones for rigging. |
| `latticeSOP` | 6 | Deforms geometry by moving the points of a surrounding lattice cage. |
| `limitSOP` | 39 | Places geometry (spheres, boxes, or templates) at data points or value limits. |
| `lineSOP` | 5 | Creates a straight polyline between endpoints with a chosen number of points. |
| `linethickSOP` | 9 | Gives polylines thickness, converting them to ribbons or tubes. |
| `lodSOP` | 9 | Switches between level-of-detail versions of geometry based on viewing distance. |
| `lsystemSOP` | 35 | Grows procedural plants and fractals from an L-system rule set. |
| `magnetSOP` | 13 | Deforms geometry within a falloff region using a metaball-shaped magnet. |
| `materialSOP` | 2 | Assigns a material to geometry primitives. |
| `mergeSOP` | 2 | Merges several geometries into one. |
| `metaballSOP` | 9 | Creates metaballs that blend into smooth blobby surfaces. |
| `modelSOP` | 1 | Holds hand-editable model geometry. |
| `noiseSOP` | 20 | Displaces points with animated coherent noise. |
| `nullSOP` | 1 | A pass-through tap, the recommended stable endpoint of a geometry chain. |
| `objectmergeSOP` | 3 | Pulls in geometry from other SOPs by path, optionally applying their object transforms. |
| `outSOP` | 4 | The output tap of a SOP component. |
| `particleSOP` | 39 | A legacy CPU particle system driven by forces and collisions. |
| `pointSOP` | 43 | Edits point positions and attributes directly, including creating standard attributes. |
| `polyloftSOP` | 13 | Lofts polygon surfaces across a series of cross-section curves. |
| `polypatchSOP` | 12 | Builds a smooth spline patch from a polygon control mesh. |
| `polyreduceSOP` | 16 | Reduces polygon count while preserving overall shape. |
| `polysplineSOP` | 11 | Fits smooth splines through polygon edges to round them off. |
| `polystitchSOP` | 7 | Stitches together seams and cracks between polygon surfaces. |
| `primitiveSOP` | 34 | Edits primitive-level attributes and transforms. |
| `profileSOP` | 14 | Extracts and edits profile curves lying on surfaces. |
| `projectSOP` | 21 | Projects curves onto a surface to create profile curves. |
| `railsSOP` | 16 | Sweeps cross-section curves along one or two rail curves. |
| `raySOP` | 16 | Projects points onto a target surface along a ray direction, a shrink-wrap. |
| `rectangleSOP` | 16 | Creates a rectangle curve or polygon. |
| `refineSOP` | 18 | Refines curves and surfaces by adding points or raising their order without changing shape. |
| `resampleSOP` | 11 | Resamples curves into evenly spaced points or segments. |
| `skinSOP` | 12 | Builds a skin surface across a set of profile curves. |
| `sphereSOP` | 23 | Generates a sphere as polygons, mesh, or NURBS. |
| `textSOP` | 30 | Creates 3D text geometry from a font and string. |
| `transformSOP` | 27 | Translates, rotates, and scales geometry. |

### COMP — components / 3D / containers  (30)

| Tool | Parameters | Description |
|---|---:|---|
| `actorCOMP` | 153 | A rigid or soft body actor participating in a Bullet physics simulation. |
| `ambientlightCOMP` | 86 | Adds uniform ambient light to a 3D scene. |
| `animationCOMP` | 43 | A component that holds and edits keyframe animation channels. |
| `annotateCOMP` | 55 | A resizable comment box for annotating and organizing the network. |
| `baseCOMP` | 22 | A general-purpose container with no panel, used to group and modularize operators. |
| `blendCOMP` | 138 | Blends the transforms of several object components by weight, a weighted parent. |
| `boneCOMP` | 140 | A single bone within a skeletal hierarchy for character rigging. |
| `bulletsolverCOMP` | 137 | The Bullet dynamics solver that advances a rigid-body physics simulation. |
| `buttonCOMP` | 132 | A clickable button panel widget that emits a state value. |
| `cameraCOMP` | 84 | A 3D camera defining the view and projection used to render a scene. |
| `camerablendCOMP` | 100 | Blends smoothly between several cameras. |
| `containerCOMP` | 122 | A panel container that lays out other panels for building user interfaces. |
| `engineCOMP` | 43 | Runs an exported .tox component in a separate process via TouchEngine for isolation and scaling. |
| `environmentlightCOMP` | 92 | Provides image-based environment lighting for physically based rendering. |
| `fbxCOMP` | 161 | Imports an FBX scene, bringing in its geometry, materials, and hierarchy. |
| `fieldCOMP` | 136 | A text-entry field widget for user input. |
| `geometryCOMP` | 124 | Places SOP geometry into the 3D scene with a transform, material, and render flags, the object node that a Render TOP draws. |
| `geotextCOMP` | 163 | Renders 3D text as scene geometry. |
| `handleCOMP` | 134 | An interactive manipulation handle in the 3D viewport. |
| `lightCOMP` | 117 | A 3D light source (point, cone, or distant) illuminating a rendered scene. |
| `listCOMP` | 130 | A scriptable list or grid panel widget for rows of items. |
| `nullCOMP` | 124 | A pass-through object transform used as a stable parent or tap in the object hierarchy. |
| `opviewerCOMP` | 127 | Embeds another operator's viewer inside a panel. |
| `parameterCOMP` | 137 | A component that presents custom parameters as a user-interface panel. |
| `replicatorCOMP` | 41 | Creates and maintains one copy of a template operator per row of a table; its script-carrying parameter is withheld from this data-only surface. |
| `sliderCOMP` | 139 | A one- or two-dimensional slider panel widget. |
| `textCOMP` | 175 | A text-display panel widget. |
| `timeCOMP` | 33 | Defines an independent local timeline (frame rate and range) for a subnetwork. |
| `usdCOMP` | 158 | Imports a Universal Scene Description (USD) scene. |
| `windowCOMP` | 53 | Defines an output window or fullscreen display on a chosen monitor. |

### MAT — materials / shading  (10)

| Tool | Parameters | Description |
|---|---:|---|
| `constantMAT` | 48 | An unlit material that shades surfaces with a single flat color and alpha. |
| `depthMAT` | 34 | Shades surfaces by their depth for use in depth passes and effects. |
| `glslMAT` | 78 | A fully custom material driven by GLSL vertex and pixel shaders, with the shader source supplied through referenced DATs. |
| `inMAT` | 36 | The input tap of a material component. |
| `lineMAT` | 109 | A material for rendering lines and wireframes with width and color control. |
| `nullMAT` | 34 | A pass-through material tap. |
| `outMAT` | 37 | The output tap of a material component. |
| `pbrMAT` | 185 | A physically based material using metalness, roughness, and texture maps for realistic lighting. |
| `phongMAT` | 213 | A classic Phong-shaded material with diffuse, specular, and emission; its GLSL multi-texture expression parameter is withheld from this data-only surface. |
| `pointspriteMAT` | 55 | Renders points as camera-facing textured sprites. |

### DAT — data / tables / references  (51)

| Tool | Parameters | Description |
|---|---:|---|
| `artnetDAT` | 9 | Receives Art-Net DMX universes into a table. |
| `audiodevicesDAT` | 10 | Lists the available audio input and output devices. |
| `choptoDAT` | 9 | Writes CHOP channels into a table of samples. |
| `clipDAT` | 13 | Holds clip metadata in table form. |
| `convertDAT` | 9 | Converts between table and text representations such as CSV, TSV, and free text. |
| `dmxmapDAT` | 13 | Defines a mapping of DMX channels to named slots. |
| `errorDAT` | 15 | Collects the errors and warnings reported by operators in the project. |
| `etherdreamDAT` | 8 | Controls an Ether Dream laser DAC. |
| `evaluateDAT` | 30 | Evaluates an expression for each cell of a table; its expression-carrying parameters are withheld from this data-only surface, leaving the non-code controls. |
| `examineDAT` | 21 | Inspects variables and objects for debugging; its expression parameter is withheld from this data-only surface. |
| `fifoDAT` | 11 | A first-in-first-out table that drops the oldest rows as new ones arrive. |
| `fileinDAT` | 9 | Reads text or a table from a file or URL. |
| `fileoutDAT` | 8 | Writes the contents of a DAT to a file. |
| `folderDAT` | 38 | Lists the files and folders of a directory as a table. |
| `inDAT` | 7 | The input tap of a DAT component. |
| `infoDAT` | 8 | Reports another operator's metadata and info as a table. |
| `insertDAT` | 13 | Inserts rows or columns into a table; its replace expression parameter is withheld from this data-only surface. |
| `jsonDAT` | 12 | Parses JSON and extracts values by path (the JSONPath filter is kept as data); its Python expression parameter is withheld from this data-only surface. |
| `keyboardinDAT` | 17 | Logs keyboard key events into a table. |
| `mediafileinfoDAT` | 10 | Reports metadata (codec, resolution, duration) about a media file. |
| `mergeDAT` | 10 | Merges tables or text from several inputs, by rows or columns. |
| `midieventDAT` | 21 | Logs incoming MIDI events as table rows. |
| `midiinDAT` | 22 | Logs incoming MIDI messages into a table. |
| `monitorsDAT` | 9 | Lists the connected display monitors and their properties. |
| `mqttclientDAT` | 21 | An MQTT client that publishes and subscribes to topics. |
| `multitouchinDAT` | 23 | Reports multi-touch contact events as a table. |
| `ndiDAT` | 8 | Lists the NDI video sources available on the network. |
| `nullDAT` | 5 | A pass-through tap for tables. |
| `opfindDAT` | 65 | Searches the network and lists operators matching name, type, or property criteria. |
| `oscinDAT` | 22 | Receives OSC messages as table rows. |
| `oscoutDAT` | 22 | Sends table rows out as OSC messages. |
| `outDAT` | 8 | The output tap of a DAT component. |
| `parameterDAT` | 45 | Exposes an operator's parameters as an editable table of names and values. |
| `performDAT` | 22 | Reports frame-by-frame performance data as a table. |
| `poptoDAT` | 23 | Writes POP attributes into a table. |
| `renderpickDAT` | 38 | Reports 3D pick results (hit geometry and position) as a table. |
| `reorderDAT` | 11 | Reorders the rows or columns of a table. |
| `serialDAT` | 20 | Reads and writes text to a serial device. |
| `serialdevicesDAT` | 9 | Lists the available serial ports. |
| `socketioDAT` | 15 | A Socket.IO client for real-time web messaging. |
| `soptoDAT` | 10 | Writes SOP attributes into a table. |
| `tableDAT` | 18 | A static, editable grid of cells; its cell and fill expression parameters are withheld from this data-only surface. |
| `tcpipDAT` | 19 | A TCP/IP client or server exchanging text messages. |
| `textDAT` | 10 | Holds free-form text, often used to store shader or script source referenced by other operators. |
| `udpinDAT` | 19 | Receives UDP datagrams as table rows. |
| `videodevicesDAT` | 10 | Lists the available video capture devices. |
| `webclientDAT` | 24 | Issues HTTP requests and captures the responses. |
| `webrtcDAT` | 14 | Handles WebRTC signaling and data-channel messaging. |
| `webserverDAT` | 15 | Hosts an HTTP and WebSocket server for external clients. |
| `websocketDAT` | 16 | A WebSocket client or server for bidirectional messaging. |
| `xmlDAT` | 23 | Parses XML or HTML into a navigable table. |

### POP — points / particles (GPU)  (96)

| Tool | Parameters | Description |
|---|---:|---|
| `accumulatePOP` | 13 | Accumulates or integrates point attributes across frames. |
| `alembicoutPOP` | 28 | Writes point geometry out to an Alembic cache. |
| `analyzePOP` | 25 | Reduces point attributes to summary statistics. |
| `attributePOP` | 30 | Creates, edits, or removes point attributes. |
| `attributecombinePOP` | 12 | Combines matching attributes from multiple point inputs. |
| `attributeconvertPOP` | 11 | Converts an attribute's type or numeric precision. |
| `blendPOP` | 18 | Blends point attributes between inputs by weight. |
| `boxPOP` | 23 | Generates points arranged as a box. |
| `cachePOP` | 14 | Buffers frames of point data for replay. |
| `cacheblendPOP` | 14 | Blends between cached frames of point data. |
| `cacheselectPOP` | 9 | Selects a specific cached frame of point data. |
| `choptoPOP` | 24 | Creates points from CHOP channel data. |
| `circlePOP` | 22 | Generates points arranged on a circle. |
| `connectivityPOP` | 11 | Labels points by which connected component they belong to. |
| `convertPOP` | 6 | Converts point geometry between representations. |
| `copyPOP` | 46 | Copies points onto other points or with transforms, for instancing. |
| `curvePOP` | 36 | Generates a curve described by points. |
| `dattoPOP` | 47 | Builds points from the rows of a DAT. |
| `deletePOP` | 36 | Deletes points selected by group or condition. |
| `dimensionPOP` | 8 | Measures or sets the bounding dimensions of the point set. |
| `dmxfixturePOP` | 25 | Maps points to DMX lighting fixtures. |
| `dmxoutPOP` | 31 | Sends point data out as DMX. |
| `extrudePOP` | 17 | Extrudes point geometry to add depth. |
| `facetPOP` | 18 | Adjusts normals and faceting of point geometry. |
| `feedbackPOP` | 11 | Feeds point output back for recursive, frame-delayed processing. |
| `fieldPOP` | 47 | Creates or samples a spatial field over points. |
| `fileinPOP` | 12 | Loads points from a file. |
| `fileoutPOP` | 23 | Writes points to a file. |
| `forceradialPOP` | 34 | Applies a radial (attract/repel) force to points. |
| `glslPOP` | 62 | Runs a GLSL compute program over points, with the program source supplied through a referenced DAT. |
| `glsladvancedPOP` | 119 | A multi-buffer GLSL point operator for advanced compute workflows, sourced from referenced DATs. |
| `glslcopyPOP` | 57 | Uses a GLSL program to drive copying or instancing of points. |
| `glslselectPOP` | 7 | Selects points using a GLSL program. |
| `gridPOP` | 26 | Generates points arranged on a grid. |
| `groupPOP` | 42 | Groups points by condition or region. |
| `histogramPOP` | 14 | Computes a histogram of an attribute's values. |
| `importselectPOP` | 28 | Selects and imports a subset of point data. |
| `inPOP` | 7 | The input tap of a POP component. |
| `limitPOP` | 23 | Clamps point attributes to a range. |
| `linePOP` | 31 | Generates points arranged on a line. |
| `linebreakPOP` | 20 | Breaks polylines into separate segments. |
| `linedividePOP` | 34 | Divides polylines into more segments. |
| `linemetricsPOP` | 59 | Measures line length and related metrics per point. |
| `lineresamplePOP` | 18 | Resamples polylines into evenly spaced points. |
| `linesmoothPOP` | 30 | Smooths polylines to reduce sharp variation. |
| `lookupattributePOP` | 23 | Looks up attribute values using an index attribute. |
| `lookupchannelPOP` | 24 | Samples a CHOP channel per point as a lookup. |
| `lookuptexturePOP` | 28 | Samples a texture (TOP) per point to read colors or data into attributes. |
| `mathPOP` | 26 | Performs arithmetic and range remapping on point attributes. |
| `mathcombinePOP` | 52 | Combines attributes together with a math operation. |
| `mathmixPOP` | 25 | Mixes attributes by a blend factor. |
| `mergePOP` | 11 | Merges several point sets into one. |
| `neighborPOP` | 25 | Finds neighboring points within a radius or count. |
| `noisePOP` | 49 | Applies coherent noise to point positions or attributes. |
| `normalPOP` | 27 | Computes point normals. |
| `normalizePOP` | 22 | Normalizes vector attributes or rescales values to a range. |
| `nullPOP` | 5 | A pass-through tap for point chains. |
| `outPOP` | 8 | The output tap of a POP component. |
| `particlePOP` | 41 | A GPU particle simulation advancing points under forces and rules. |
| `patternPOP` | 35 | Generates a shaped pattern of values across points. |
| `phaserPOP` | 23 | Applies a phase-based offset that animates values across points. |
| `planePOP` | 19 | Generates points arranged on a plane. |
| `pointPOP` | 11 | Edits point positions and attributes directly. |
| `pointfileinPOP` | 33 | Loads a point-cloud file (such as PLY) into points, the entry point for scanned data. |
| `pointgeneratorPOP` | 26 | Generates a specified number of points to seed a system. |
| `polygonizePOP` | 22 | Builds polygonal surface geometry from points. |
| `primitivePOP` | 21 | Edits primitive-level attributes of point geometry. |
| `projectionPOP` | 22 | Projects points using a projection mapping. |
| `proximityPOP` | 21 | Computes proximity and nearest-neighbor relationships between points. |
| `quantizePOP` | 19 | Snaps point attributes to a grid or step size. |
| `randomPOP` | 36 | Assigns random values to point attributes. |
| `rayPOP` | 37 | Projects points onto a target surface along rays. |
| `rectanglePOP` | 23 | Generates points arranged as a rectangle. |
| `rerangePOP` | 19 | Remaps an attribute from one value range to another. |
| `revolvePOP` | 12 | Revolves a profile of points around an axis to form a surface. |
| `selectPOP` | 9 | References points from another POP by path. |
| `skinPOP` | 9 | Skins a surface across point curves. |
| `skindeformPOP` | 18 | Deforms points to follow a skeleton (skinning). |
| `soptoPOP` | 10 | Converts SOP geometry into points. |
| `sortPOP` | 27 | Sorts points by value, position, or attribute. |
| `spherePOP` | 33 | Generates points arranged on a sphere. |
| `sprinklePOP` | 16 | Scatters points across a surface or through a volume. |
| `subdividePOP` | 9 | Subdivides point geometry for higher resolution. |
| `switchPOP` | 14 | Selects one of several point inputs by index. |
| `textPOP` | 37 | Generates points describing text. |
| `texturemapPOP` | 32 | Assigns or computes texture coordinates for points. |
| `topologyPOP` | 61 | Builds or edits the connectivity/topology of point geometry. |
| `toptoPOP` | 45 | Creates points from a TOP, turning pixels into positioned points. |
| `torusPOP` | 24 | Generates points arranged on a torus. |
| `tracePOP` | 30 | Traces an image's shapes into points or curves. |
| `trailPOP` | 28 | Records the motion trails of points over time. |
| `transformPOP` | 48 | Translates, rotates, and scales points. |
| `triangulatePOP` | 16 | Triangulates points into a mesh (Delaunay-style). |
| `trigPOP` | 17 | Applies trigonometric functions to point attributes. |
| `tubePOP` | 24 | Generates points arranged as a tube. |
| `twistPOP` | 19 | Applies twist, bend, or taper deformations to points. |

### Utility tools — the data plane, drive layer, and control plane (35)

- **Read the scene:** `scene_info`, `read_network`, `find_errors`, `inspect`, `top_info`, `probe_optype`,
  `mem`.
- **Build & wire:** `connect`, `set_par`, `set_par_many`, `set_flags`, `set_pos`, `delete_op`, `bind_chop`,
  `batch` (runs many ops in one round-trip; grants no capability a direct call lacks, and cannot nest).
- **Import:** `import_scan`, `import_segmented_model` (builds a whole per-section `sec*`/`mat*` rig from
  per-part OBJs in one call).
- **Deliver (wire-only):** `save_top` (writes a TOP's image to the working directory), `capture_ui`,
  `write_csv`, `pulse`, `show`.
- **Look things up:** `td_capabilities` (start-here index of the surface + boundary), `help`,
  `operator_reference` (any operator's full typed schema), `recipe_reference` (the workflow recipes).
- **Learn to write code:** `glsl_reference` (GLSL shaders), `expr_reference` (parameter expressions),
  `code_reference` (which lane carries what + the consent handshake) — read-only teachers that propose code
  text for the validated lanes or paste-by-hand; they never run code.
- **Code lanes (default-off, consent-gated):** `set_glsl` / `validate_glsl`, `set_expr` / `validate_expr`.
- **Device control (default-off, consent-gated):** `device_send` — sends a command to a projector over the
  closed PJLink Class-1 allowlist; off unless a human explicitly enables `allow_device_control`.
- **Maintenance:** `dev_reload`.

---

## References

Several discoverability surfaces back the tool catalog. `td_capabilities` is the start-here index — call it
first to orient on the surface, the boundary, and where to look things up.

- **Tool catalog** — [docs/TOOL_CATALOG.md](docs/TOOL_CATALOG.md), generated from `reference/catalog.json`
  (the authoritative count of every typed operation, across roughly 17,000 typed parameters in the operator
  families). This *is* the security boundary: if it isn't in the catalog, the server can't do it.
- **Operator reference** — the `operator_reference` MCP tool answers "what parameters does operator X take?"
  from live-probed ground truth (name, kind, range/tokens per parameter).
- **`help`** — an operator's facts (family, input count, parameter names) plus a deep link to the official
  Derivative documentation. No Derivative prose is bundled; shipped operator descriptions are original.
- **`recipe_reference`** — the drive layer: 66 tool-mapped workflow recipes carrying ordered steps and
  conventions (see below).
- **Code teachers** — read-only guides for TD's code surfaces: `glsl_reference` (GLSL shaders),
  `expr_reference` (parameter expressions), `code_reference` (which lane + the consent handshake). They
  propose code text for the validated lanes or paste-by-hand; they never run code.

## Documentation

- [docs/INSTALL.md](docs/INSTALL.md) — full setup, arming, and MCP-client configuration.
- [docs/GUIDE.md](docs/GUIDE.md) — the deeper how-to-use manual: the mental model, the working loop, and the
  conventions that make it work.
- [docs/HOWTO.md](docs/HOWTO.md) — short "how do I…" task recipes into the workflow library.
- [docs/RUNBOOK.md](docs/RUNBOOK.md) — day-to-day operation: arming, working dir, consent flags, the reload model.
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — symptom → cause → fix for the common failure modes.
- [docs/TESTING.md](docs/TESTING.md) — how to run the executor, gateway, and consistency test layers.
- [docs/TOOL_CATALOG.md](docs/TOOL_CATALOG.md) — the full per-tool catalog.
- [ARCHITECTURE.md](ARCHITECTURE.md) · [SECURITY.md](SECURITY.md) · [CONTRIBUTING.md](CONTRIBUTING.md) ·
  [CHANGELOG.md](CHANGELOG.md).

---

## Projection-mapping recipes

`recipe_reference` classifies your task and returns one proven, tool-mapped way to build it — an ordered
sequence of real tools, the parameters that actually move the result, a landmark `OUT_` null to plant at each
step, and the cheap read to verify it. TouchDesigner offers many valid approaches, so a recipe is a worked
example to adapt, never gospel. The 66 recipes span these domains:

- **3D render lane** (`facade_3d_render`) — a building `.obj`/`.abc`/`.fbx`/`.usd` into a Geometry COMP,
  framed by a camera, shaded with a MAT, rendered to a Render TOP over a dark plate.
- **Generative texture FX** (`generative_texture_fx`) — the bread-and-butter façade content lane: noise →
  ramp → tone → transform → composite → feedback trail, a pure evolving TOP network.
- **Point-cloud content** (`point_cloud_content`) — ingest a finished drone/photogrammetry scan (position
  data in a TOP) and instance sprites or meshes onto the points.
- **Camera / framing** (`camera_match_facade`) — match the virtual camera to the real façade
  (orthographic flat map or perspective lens-match).
- **Per-section rig & choreography** (`per_section_material_rig`, `height_sweep_choreography`,
  `per_section_color_choreography`, `facade_cue_choreography`) — one material per architectural section,
  driven data-only via CHOP-export (a travelling light sweep, per-section color, or a cued show) — no
  expressions, no code.
- **Video mapping** (`segmented_facade_video_projection`, `segmented_facade_video_content`,
  `facade_mask_atlas`) — drive actual video onto each UV-registered section via `emitmap`, with a grayscale
  mask keeping windows black.
- **Freeform / mesh warp** (`mesh_freeform_warp`) — the data-only analog of a mesh-warp mapper: a `remapTOP`
  UV field with a control-grid offset bends content onto a curved surface.
- **Projector mapping & alignment** (`projector_calibrated_3d`, `projection_align_and_output`,
  `multiprojector_edge_blend`) — a calibrated projector frustum, a 4-corner keystone, and an edge-blended
  seam across overlapping projectors.
- **Show control & DMX** (`realtime_input_driver`, `show_control_timecode`, `dmx_artnet_output`) — a live
  OSC/audio/DMX/MIDI input driving the façade, a timer/timecode transport running the cue show, and DMX /
  Art-Net / sACN output for lights and fixtures.
- **Output / hand-off** (`output_handoff`) — expose the finished content to a media server (Pixera /
  disguise) or bake a HAP 4K file — **wire-only**, with `record`/`active` left off for the operator to fire.
- **2D multi-surface mapping** (`multi_quad_mapper`) — the data-only kantanMapper: N sources, each on its own
  corner-warped quad, composited over black — map a different video onto every window / flat / sign face.
- **Projector calibration intake** (`camera_calibration_intake`) — build the target Camera COMP + render rig
  so on-site camSchnappr / OpenCV / survey calibration (pose + projection matrix) plugs straight back in.
- **Multi-projector depth** (`multiprojector_blacklevel_mask`, `projector_stack_converge`) — black-level
  uplift so the blend seam vanishes on dark content, and projector stacking/convergence for brightness &
  redundancy (distinct from edge-blend).
- **Spill / garbage masking** (`output_spill_garbage_mask`) — screen-space holdout that clips content to the
  surface silhouette (keep light off the ground / sky / neighbors) plus a soft projector-frame feather.
- **Test patterns & rig-and-focus** (`test_pattern_generator`) — data-only alignment grids, crosshairs, color
  bars, focus/1:1-pixel fields, per-projector labels, convergence & overlap markers at native resolution.
- **Media playback & compositing** (`media_clip_player`, `layer_compositor`) — robust 4K clip playback +
  gapless playlist, and a media-server-style layer stack (per-layer opacity/blend/transform) with transitions.
- **Live feed & direct output** (`live_media_server_feed`, `direct_projector_output`) — live Spout / NDI /
  Touch / RenderStream handoff, and a direct-to-projector perform-window / Direct-Display front-of-house lane.
- **Immersive / dome / curved** (`dome_fisheye_master`, `curved_screen_warp_blend`) — fisheye/equirect dome
  and 360 masters (cubemap reprojection) and a cylindrical warp + blend for a single wide curved screen.
- **Interactive content** (`vision_interactive_mask`, `audio_reactive_content`) — a live camera driving a
  presence/motion mask, and generative visuals built from the music (FFT / band energy / onset) — data-only.
- **Text & titling** (`text_title_content`) — data-only typography: show titles, lower-thirds, rolling
  credits, and a code-free live countdown/clock via a `timerCHOP → choptoDAT → textTOP` bridge (no expressions).
- **Particles & kinetic 3D** (`particle_system_content`, `instanced_kinetic_content`) — a live GPU POP
  particle system (embers / sparks / snow / flow) and animated instance arrays / kinetic motion-graphics scenes.
- **Seamless loops** (`seamless_loop_authoring`) — bake a seam-free generative loop (periodic-phase or
  head/tail crossfade), wire-only to a movie file.
- **Tracked / moving surface** (`tracked_surface_content`) — project onto a moving prop or performer: a
  tracking CHOP (BlackTrax / PosiStageNet / OptiTrack) drives a digital-twin transform code-free.
- **LED wall / pixel-map** (`led_wall_pixelmap_feed`) — carve a canvas into per-panel tiles at the exact
  total resolution a Novastar / Brompton / Linsn LED processor ingests.
- **Immersive room** (`immersive_room_mapping`) — a fully enclosing multi-wall + floor/ceiling room, one
  calibrated camera per surface fanned from a shared 3D scene (floor / forced-perspective anamorphic mode).
- **Sheer surfaces** (`sheer_surface_mapping`) — scrim / gauze / HoloGauze / fog / water-screen content prep
  with mandatory black-crush, a rear-projection mirror, and an actor-safe holdout.
- **Signal ingest** (`signal_ingest_remap`) — capture a live SDI / HDMI / NDI / ST-2110 / Syphon-Spout source
  and remap it onto surfaces — the ingest twin of the output lanes.
- **Colour delivery & uniformity** (`color_grade_lut_delivery`, `projector_color_uniformity`) — a LUT / OCIO
  delivery-colour pipeline, and white-point / gamma matching across a multi-projector array.
- **Multi-zone output** (`multizone_independent_outputs`) — N independent output zones, each with its own
  content, resolution, and destination (distinct from one spanning edge-blended window).
- **Show automation & safety** (`scheduled_playback_dayparting`, `timecode_chase_slave`,
  `emergency_blackout_and_standby`, `confidence_monitor_foldback`) — unattended time-of-day dayparting,
  chasing external LTC/SMPTE timecode, instant DBO / standby, and operator confidence / foldback taps.
- **Live-event graphics** (`corporate_stage_graphics`) — an IMAG + lower-thirds + holding-slide + countdown
  package for a conference / keynote stage.
- **Operator control & show-ops** (`operator_control_panel`, `show_health_watchdog`) — build the operator's own
  on-screen/TouchOSC control surface, and a system-health watchdog (fps / dropped-frames / GPU-temp) with an alarm overlay.
- **Naked-eye-3D corner LED** (`anamorphic_corner_led_3d`) — forced-perspective 3D across a 90° corner LED wall via a
  single off-axis hero camera, split to the two faces.
- **Lidar & contour** (`lidar_presence_interaction`, `line_contour_mapping`) — interactive floor/wall from a 2D laser
  scanner, and animated edge/outline light-lines tracing a building's real architecture.
- **Show audio** (`show_audio_playback`) — soundtrack playback + multichannel/spatial speaker routing + A/V timecode lock
  (the audio-OUT lane; every other audio recipe is input-only).
- **Broadcast & IoT** (`st2110_ip_video_out`, `mqtt_iot_input`) — SMPTE ST 2110 video-over-IP delivery (PTP), and MQTT /
  building-automation triggers extending the input-driver family.
- **Content pipeline** (`projector_plan_tables`, `uv_template_export`, `structured_light_calibration_patterns`,
  `notch_block_playback`) — throw/lens planning sheets, a UV registration template for content artists, gray-code /
  phase-shift capture patterns, and a Notch `.dfx` block-playback scaffold.

TouchDesigner's job is content creation; the physical warp/blend onto the real surface is done downstream by
the media server. See [ARCHITECTURE.md](ARCHITECTURE.md) for how the recipe layer is wired.

---

## Gotchas

A few TouchDesigner-specific traps the recipes encode:

- **Create tools take tuplet vectors; `set_par` takes raw components.** A create tool exposes `color`:[r,g,b]
  and `resolution` tuplets; passing raw component names (`colorr`) or a `pars{}` wrapper to a *create* tool
  silently drops them. Use `set_par` for raw `colorr`/`tx`.
- **A fresh `geometryCOMP` ships a default torus child** carrying the render/display flags. Delete it (with
  `delete_op`) and turn on the render flag of your imported `fileinSOP`, or both compete to be rendered.
- **The Render TOP references camera / geometry / lights by *parameter*, not by wiring them into its
  inputs.** Its output is transparent — composite it over a dark `constantTOP` for a readable beauty pass.
- **The 256/1280 resolution trap is real.** Generators (`noiseTOP`, `rampTOP`) inherit a small default; set
  `outputresolution=custom` + `resolutionw/h` for 4K delivery. Non-commercial builds cap output at 1280.
- **CHOP-export drives a parameter only with the exporter's viewer active and a forced cook** — the Export
  flag alone does nothing. `bind_chop` handles this; a manual export still needs it.
- **`import_segmented_model` forces the File In SOP to CONSTANT mode** so the real OBJ loads, not
  TouchDesigner's default sample box; models must live under the working directory.

---

## Configuration

Most configuration happens in the GUI and is written to `~/.touchdesigner-bridge-mcp/arm.json`, the single
file both processes read fresh per call. Only the MCP client config needs environment variables.

| Setting | Where | Description |
|---|---|---|
| `TDMCP_GW_HEADLESS` | env (client config) | `1` = run the binary as the headless stdio MCP gateway; unset = open the GUI window. |
| `TDMCP_REPO` | env (client config) | The clone path, so the gateway resolves its bundled `reference/` data deterministically. |
| `working_dir` | GUI → Working dir → Apply | The confinement root. Every file read/write is `realpath`-confined under it. Read fresh per call by both layers; **Apply** takes effect live, no restart. |
| `token` / `port` | `arm.json` (minted on arm) | The CSPRNG session token and loopback port (default `9980`). You never type or see the token. |
| `allow_expr` / `allow_glsl` | GUI consent toggle → `arm.json` | Consent flags for the two code lanes (default off). |
| `allow_highres` | `arm.json` | Bypass the enforced render magnitude ceiling (default off). |
| `min_action_interval_ms` | `arm.json` | The destructive-call throttle. |

A bare re-arm **preserves** the consent flags and working directory — it never silently resets the jail or
flips a lane.

---

## Security

The security model is **the boundary itself**, not a sandbox. Full threat model, honest limits, and
disclosure contact in [SECURITY.md](SECURITY.md).

- **Data-only by construction.** No `execute_python` / `eval` / `run` / shell tool exists. A runtime canary
  (`assert_no_rce_endpoints`), a runtime optype guard (`check_optype_allowed`, which denies `script` /
  `execute` / `cplusplus` operators and `evaluateDAT`), and a build-time fence
  (`catalog_never_exposes_rce_tools`) enforce it.
- **Layered parameter guard.** Because the typed surface lowers to a generic `set_par`, the real boundary is
  the executor's `check_par_allowed` over TouchDesigner's ~17,000-parameter surface: a universal deny of
  code-pointer parameter names (`callbacks` / `*script` / `datexpr`), a reviewed inline code-sink denylist
  with Sequence-block-index regex generalization, and a **fail-closed allowlist** so unknown or newer
  parameters are refused instead of waved through. This is a denylist over a closed-source third-party
  surface: an independent red-team review found no working RCE bypass, but it is a residual, not a proven-
  complete boundary — stated plainly in [SECURITY.md](SECURITY.md).
- **Loopback + auto-minted token.** The Web Server DAT binds `127.0.0.1` only; arming mints a 128-bit CSPRNG
  token presented as `X-TDMCP-Token` and compared with `secrets.compare_digest`. Cross-origin
  (non-loopback `Origin`/`Host`) requests are refused on every endpoint, closing the loopback-CSRF /
  DNS-rebind class. Body caps guard against memory-DoS.
- **`realpath`-confined working directory.** Every file operation resolves and re-checks against one root,
  with symlink/junction escapes closed. The config dir (token + consent) and the executor trust root
  (`td_executor/*.py`, `INTEGRITY.json`, `arm.py`) are off-limits even inside the working dir, and write
  tools enforce extension whitelists.
- **Integrity pinning.** `INTEGRITY.json` hash-pins every executor `.py`; arming and `dev_reload` **verify
  before import** and fail closed on any mismatch or unpinned handler. Honest ceiling: this is
  tamper-evidence, not a boundary against an attacker who can already write the install directory — OS file
  permissions are the root of trust.
- **Output is wire-only.** Record/live-send graphs are built with `record`/`active` off; you fire them.
- **The two validated code lanes are default-off and consent-gated.** `glsl_v1` (`set_glsl`/`validate_glsl`,
  GPU-sandboxed — worst case a recoverable driver timeout) and `expr_v1` (`set_expr`/`validate_expr`, an AST
  positive-allowlist, shipped **EXPERIMENTAL** because a validator gap here would be host code). Both
  validate before write, are executor-authoritative, audited, and single-write-path. The AI cannot flip its
  own consent — the `/mcp_bridge` component and its GUI toggles are refused by `assert_writable` on every
  mutating tool, and the flags live in the off-limits config dir.
- **Enforced magnitude ceiling.** The advisory governor is advisory-first, but a hard ceiling refuses
  catastrophic, driver-killing magnitudes (per-dimension resolution > 16384 px, instance/particle counts
  > 5,000,000, render passes > 256), overridable only by the human-gated `allow_highres` flag — so
  legitimate 4K/8K delivery passes but a runaway `set_par` cannot hang the display driver.
- **Intended posture: loopback, single trusted user, trusted machine.** The transport is meant to stay on
  the local host. Treat the AI as semi-trusted input.

Read [SECURITY.md](SECURITY.md) before running this anywhere other than a single trusted machine.

---

## Troubleshooting

Full table in [docs/INSTALL.md](docs/INSTALL.md). Quick checklist:

- The GUI **Status** pane reads **Armed** with your TouchDesigner build, and
  `http://127.0.0.1:9980/health` responds.
- The MCP client config points at the built `touchdesigner-bridge-mcp.exe` with
  `TDMCP_GW_HEADLESS` set to `1` (without it, the binary launches its GUI and the handshake never
  completes).
- `TDMCP_REPO` points at the clone (the folder containing `reference/recipes.json`), or reference/recipe
  lookups fail.
- The client was fully restarted after editing its config.
- File paths you pass to tools live **inside** the configured working directory.
- If arming reports "integrity pre-check FAILED, refusing to arm", an executor file changed without
  regenerating the manifest — run `python scripts/gen_integrity_manifest.py`.
- If an Expr/GLSL-lane call is rejected, those lanes are off by default — flip the consent toggle on
  `/mcp_bridge` (it persists to `arm.json`).

---

## License

This project is **dual-licensed**:

- **Noncommercial use** — personal, educational, research, and evaluation — is free under the
  [PolyForm Noncommercial License 1.0.0](LICENSE).
- **Commercial, business, and production use** requires a separate paid commercial license — see
  [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md) to obtain one.

The bridge bundles no third-party binaries or data — the executor uses only the Python standard library and
TouchDesigner's own built-in `td` module, and shipped operator descriptions are original.

---

## Support

If this saved you time and you're using it noncommercially, a tip is always appreciated —
[ko-fi.com/eviscerations](https://ko-fi.com/eviscerations). It's voluntary and grants no license; commercial
use is covered by [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md).

---

## Development & tests

The executor tests need **no** TouchDesigner license — they run against a fake TD scene
(`td_executor/tests/_tdmock.py`) — so the security invariants are provable on any machine. Run all four
checks before opening a change:

```sh
# 1. Executor unit tests — offline, no TouchDesigner required.
python td_executor/tests/run_tests.py

# 2. Gateway tests — includes the build-time boundary fences (catalog_never_exposes_rce_tools,
#    code_named_params_are_the_known_reviewed_set, reserved placement args, unique names).
cargo test --manifest-path gateway/Cargo.toml

# 3. Registry consistency — the gateway catalog and the executor endpoint set agree.
python scripts/audit_registry_consistency.py

# 4. Recipe validation — every recipe maps to real, shipped tools.
python scripts/validate_recipes.py
```

After **any** executor edit, regenerate the integrity manifest or the next arm/reload fails closed:

```sh
python scripts/gen_integrity_manifest.py            # write/refresh the manifest
python scripts/gen_integrity_manifest.py --check    # CI mode: nonzero if stale
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full build / test / arm loop and the reload lifecycles.

---

## Contributing

Pull requests welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). This project is a data-only control surface,
and that boundary is the point of the whole design, so the most important rule for any change is: **do not
add a path that lets the AI run arbitrary code.** Read [SECURITY.md](SECURITY.md) and
[ARCHITECTURE.md](ARCHITECTURE.md) before making non-trivial changes, and note any security-posture change in
[CHANGELOG.md](CHANGELOG.md).
