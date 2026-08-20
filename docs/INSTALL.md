# Install & run TouchDesigner Bridge MCP

This guide takes you from a fresh clone to an AI client (Claude Desktop) building
TouchDesigner networks through the bridge. Every step below is verified against
the actual code (`gateway/src/main.rs`, `gui.rs`, `config.rs`, and `arm.py`).

The bridge has **two processes** that talk over loopback:

- **The gateway** — one Rust binary, `touchdesigner-bridge-mcp[.exe]`. It is
  **both** the MCP server *and* a GUI. Which one it becomes is decided at launch
  (see below). Your AI client spawns it as an MCP server; you run it yourself as
  a GUI to set the working directory.
- **The executor** — Python that runs **inside** a running TouchDesigner session.
  You arm it by pasting one line into the TD Textport. It uses only TouchDesigner's
  built-in Python and the standard library — nothing to `pip install`.

They rendezvous through a single file: `~/.touchdesigner-bridge-mcp/arm.json`.

> **Fast path:** `python scripts/setup.py` builds the gateway and prints your
> filled-in Claude Desktop config **and** your Textport arm command with real
> paths for this machine. The manual steps below explain what it produces.

---

## 1. Prerequisites

| Requirement | Notes |
|---|---|
| **Rust toolchain** | To build the gateway. Install from <https://rustup.rs>. Provides `cargo`. |
| **TouchDesigner** | A licensed install, build **2023.11k+** or **2025.30k+**. The executor runs inside TD's own Python. |
| **Python 3.10+** | Only needed to run the `scripts/setup.py` helper. The *executor* runs in TouchDesigner's embedded Python, not this one. |
| **Claude Desktop** | Or any other MCP client that can launch a stdio MCP server. |

The target pipeline is **AMD-first**; NVIDIA/CUDA-only operators are intentionally
out of scope. Windows is the primary platform.

---

## 2. Clone and build the gateway

```sh
git clone <your-fork-or-source-url> touchdesigner-bridge-mcp
cd touchdesigner-bridge-mcp
cargo build --release --manifest-path gateway/Cargo.toml
```

This produces the binary at:

```
gateway/target/release/touchdesigner-bridge-mcp.exe      (Windows)
gateway/target/release/touchdesigner-bridge-mcp          (macOS/Linux)
```

**How the one binary picks its mode** (`gateway/src/main.rs`): on launch it checks
the environment variable `TDMCP_GW_HEADLESS`.

- **Set** → it serves the MCP protocol over stdin/stdout (headless). This is how
  an MCP client must launch it.
- **Unset** (the default) → it opens the GUI window.

So the *same executable* is both server and GUI; the env var is the switch.

---

## 3. Configure your MCP client (Claude Desktop)

Add the gateway to your Claude Desktop config so Claude launches it as an MCP
server. The config file lives at:

- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

Use the template at [`claude_desktop_config.example.json`](claude_desktop_config.example.json).
Copy the `touchdesigner` block into your real config and replace both
`<ABSOLUTE_PATH_TO_CLONE>` placeholders with your clone path (forward slashes,
even on Windows):

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

Two env vars, both load-bearing:

- **`TDMCP_GW_HEADLESS=1`** — required, or the client would spawn a GUI window and
  the MCP handshake would never complete.
- **`TDMCP_REPO`** — the clone path, so the gateway finds its bundled reference
  data (`reference/recipes.json`, `reference/catalog.json`) deterministically and
  arm.py resolution matches. (Without it the gateway falls back to walking the
  binary's parent directories, which usually works from the `gateway/target/...`
  layout — but setting it is the reliable choice.)

Restart Claude Desktop after editing the config.

---

## 4. Set the working directory (run the GUI)

The **working directory** is the single folder the tool may read from and write to
— every executor file operation is confined under it. It is deliberately **not**
the source tree.

Run the gateway binary **with no arguments** to open the GUI:

```sh
gateway/target/release/touchdesigner-bridge-mcp.exe
```

In the GUI, open the **Working dir** pane, enter (or paste) an existing folder,
and click **Apply**. This does two things (`gateway/src/gui.rs`):

1. Updates the confinement root live for every future call.
2. Merge-writes `working_dir` into `~/.touchdesigner-bridge-mcp/arm.json`.

### `arm.json` — the single source of truth

`~/.touchdesigner-bridge-mcp/arm.json` is where the gateway and the executor
rendezvous. Both sides read it; the GUI and `arm.py` write it. It holds:

| Key | Meaning |
|---|---|
| `working_dir` | The confinement root. Read **fresh per call** by both layers. |
| `token` | A CSPRNG session token the executor mints on arm; the gateway presents it on every loopback call. You never type or see it. |
| `port` | Loopback port the executor listens on (default `9980`). |
| `allow_expr`, `allow_glsl` | Consent toggles for the code/expression lanes (default off). |
| `allow_highres` | Bypass the render magnitude ceiling (default off). |
| `enabled`, `min_action_interval_ms` | Auto-arm flag and the destructive-call throttle. |

A **re-arm preserves** the consent flags and working dir — a bare re-arm never
silently resets the jail or disables a lane.

---

## 5. Arm the executor inside TouchDesigner

Open TouchDesigner, open the **Textport** (`Alt+T`), and paste the arm command.
It looks like this (substitute your clone path):

```python
import os; os.environ['TDMCP_REPO']=r'C:/path/to/touchdesigner-bridge-mcp'; exec(open(os.path.join(os.environ['TDMCP_REPO'],'arm.py')).read())
```

You do not have to type it by hand: the GUI's **Settings → Arm bridge (Textport)**
pane shows this exact line with a **Copy arm command** button, and
`python scripts/setup.py` prints it too.

What arming does (`arm.py`):

- Verifies the on-disk executor files against `td_executor/INTEGRITY.json`
  **before** importing them (fail-closed tamper-evidence).
- Refuses to arm if any handler looks like a raw code-execution endpoint
  (the data-only canary, `assert_no_rce_endpoints`).
- Mints the session token and writes `token`/`port`/`working_dir` (and preserves
  consent flags) into `arm.json`.
- Assembles a `/mcp_bridge` component: a **Web Server DAT** on loopback
  `127.0.0.1:9980` plus a thin callbacks DAT that loads the on-disk `td_executor`
  package, and adds GUI **consent toggles** (Allow Expr Lane / Allow GLSL Lane)
  that persist to `arm.json`.

**Re-arm any time** to hot-reload on-disk executor edits — re-running the line
purges the module cache first. **Remove** the bridge with:

```python
op('/mcp_bridge').destroy()
```

> `TDMCP_REPO` is injected because `arm.py` is run via `exec()`, where `__file__`
> is undefined — the env var is how arm.py locates the `td_executor` package and
> the integrity trust root. It is **not** the confinement working dir.

---

## 6. Verify

1. **Executor health** — with TouchDesigner armed, open in a browser or curl:

   ```
   http://127.0.0.1:9980/health
   ```

   The GUI's **Status** pane also shows an "Armed" pill and the live TouchDesigner
   build once the executor is reachable.

2. **Tools appear in the client** — restart Claude Desktop; the `touchdesigner`
   MCP server should connect and expose the tool surface (operator tools plus
   utilities like `scene_info`, `read_network`, `connect`, `set_par`). Ask the
   assistant to call `scene_info` — a successful reply means the full path
   (client → gateway → loopback → executor → TouchDesigner) is live.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Client shows the server but no tools / it hangs | `TDMCP_GW_HEADLESS` not set in the config `env`. The binary launched its GUI instead of serving stdio. |
| Tools error with a connection/403 | TouchDesigner isn't armed, or `arm.json` is stale. Re-run the arm command; check `http://127.0.0.1:9980/health`. |
| "integrity pre-check FAILED, refusing to arm" | An executor file changed without regenerating the manifest. Run `python scripts/gen_integrity_manifest.py`, or set `TDMCP_INTEGRITY=0` for local dev only. |
| File tools refuse a path | The path is outside the working directory. Set the right folder in the GUI's Working-dir pane (Apply). |
| Reference/recipe lookups fail | `TDMCP_REPO` doesn't point at the clone (the one containing `reference/recipes.json`). Fix it in the client config `env`. |
| Expr/GLSL lane rejected | Those lanes are off by default. Flip the consent toggle on `/mcp_bridge` in TouchDesigner (persists to `arm.json`). |

See the top-level [README.md](../README.md) for the security model and the tool
surface, and [`td_package/README.md`](../td_package/README.md) for making TD-side
arming persistent per project.
