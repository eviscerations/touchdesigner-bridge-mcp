# `td_package/` — TouchDesigner-side arming aid

This folder is for the **optional** TouchDesigner-side helper that makes arming
the executor easier or persistent. Arming itself is described in
[`../docs/INSTALL.md`](../docs/INSTALL.md) step 5; nothing here is required to use
the bridge.

## What arming actually is (so you know what a helper would automate)

Arming = pasting one line into the TouchDesigner Textport (`Alt+T`):

```python
import os; os.environ['TDMCP_REPO']=r'C:/path/to/touchdesigner-bridge-mcp'; exec(open(os.path.join(os.environ['TDMCP_REPO'],'arm.py')).read())
```

That line sets `TDMCP_REPO` (so `arm.py`, run via `exec()`, can find the on-disk
`td_executor` package and its integrity trust root) and executes `arm.py`, which
builds the `/mcp_bridge` component and starts the loopback Web Server DAT on
`127.0.0.1:9980`. See `../arm.py` for the full sequence.

By default this is **manual and per-session** — you re-paste it each time you open
TouchDesigner (and re-paste to hot-reload executor edits). The helpers below make
it one click, or automatic on project open.

## Why there is no committed `.tox` here

A TouchDesigner `.tox` is a **binary component file** authored inside
TouchDesigner (build a COMP, then right-click → *Save Component .tox*). It cannot
be generated from a plain Python/Rust script in this repo, so none is checked in —
committing one would mean shipping a binary blob that this codebase can't
regenerate or diff. **Honest status: the `.tox` route is a manual, build-it-
yourself step.** The two manual alternatives below need no `.tox` at all and are
what most users should use.

## Option A — a Text DAT you run on demand (simplest)

Inside your project:

1. Create a **Text DAT** (name it e.g. `arm_bridge`).
2. Paste the arm command (above) into it, with **your** clone path.
3. To arm: right-click the Text DAT → **Run**, or middle-click → *Run*. This runs
   the same code as pasting into the Textport.

This keeps the command *in your project file*, so it travels with the `.toe` and
you never have to find the line again. It is still manual (you click Run each
session), but there's nothing to type.

## Option B — arm automatically when the project opens (persistent)

Use an **Execute DAT** so the bridge arms itself on project start:

1. Create an **Execute DAT**.
2. Enable its **Start** callback (turn on the `onStart` pulse/parameter).
3. Put the arming code in the `onStart` callback body, for example:

   ```python
   def onStart():
       import os
       os.environ['TDMCP_REPO'] = r'C:/path/to/touchdesigner-bridge-mcp'
       repo = os.environ['TDMCP_REPO']
       exec(open(os.path.join(repo, 'arm.py')).read(), {'op': op, 'root': root, 'app': app})
       return
   ```

   `arm.py` expects the TouchDesigner globals `op`, `root`, and `app` to be
   available; the Textport provides them implicitly, but an Execute DAT callback
   has its own scope, so pass them in via the `exec()` globals dict as shown.

4. Save the project. On the next open, the bridge arms with no interaction.

Notes and honest caveats:

- **The clone path is hardcoded per project.** If you move the clone, update the
  path in the DAT. (This mirrors the manual command — there's no auto-discovery of
  the repo from inside TouchDesigner.)
- **Re-arming still hot-reloads** executor edits: re-run the DAT (Option A) or
  reopen the project (Option B).
- **Integrity + data-only guards still run.** Auto-arming does not bypass the
  pre-import integrity check or the no-RCE canary in `arm.py`; a tampered executor
  still refuses to arm.
- The **consent toggles** (Allow Expr / Allow GLSL) that `arm.py` adds to
  `/mcp_bridge` persist to `arm.json` regardless of how you armed.

## If you do build a reusable `.tox`

A convenient component would be a Base COMP containing the Text DAT from Option A
plus the Execute DAT from Option B, saved as `arm_bridge.tox`, that you drag into
any project. Build it once in TouchDesigner and save it into this folder; keep the
clone path inside it pointing at wherever you cloned this repo. Because it embeds
an absolute path, treat any committed `.tox` as machine-specific — prefer the
script-driven Options A/B above for anything shared.
