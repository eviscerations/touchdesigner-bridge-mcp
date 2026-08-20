# TouchDesigner code — orientation & handoff (reference only)

This is the front door for CODE in touchdesigner-bridge-mcp. The bridge is **data-only**: every tool is a
fixed, typed, validated operation, and there is no arbitrary-code path. But TouchDesigner's art often needs
code — a GPU shader, a parameter that computes itself. This reference teaches **where code fits, which lane
carries it, and how to offer it to a human safely**. It never runs code on its own; it proposes code *text*
and, for the two validated lanes, applies it only with explicit consent.

**This MCP is also a learning tool.** When you surface code, teach it — explain what it does and why, so a
person who doesn't yet know TouchDesigner learns the workflow by watching, not just gets an answer.

## TouchDesigner's code surface — three kinds, three postures

| Surface | What it is | Posture here | Learn it via |
|---|---|---|---|
| **GLSL shaders** | Per-pixel GPU code in a `glslTOP` (and `glslMAT`/`glslPOP`) — generative fields, custom FX, displacement, feedback. Runs in the GPU sandbox; worst case is a recoverable GPU stall, not host access. | **Validated lane** `set_glsl` (default-OFF, `allow_glsl`) **or** paste-by-hand. | `glsl_reference` |
| **Python parameter expressions** | A single-line expression on a parameter, evaluated each cook (`me`, `op(...)`, `parent(...)`, `tdu`, `math`, `absTime`). | **Validated lane** `set_expr` (default-OFF, `allow_expr`; the most-gated lane — it evaluates as host Python) **or** type-by-hand. | `expr_reference` |
| **DAT / Execute / Script-CHOP Python, node `callbacks`** | Full, unrestricted CPython in a DAT or callback. | **Paste-handoff only.** No tool ever delivers this — it is the excluded raw path. Propose the code, teach it, and let the user paste it into a DAT themselves. | (propose text; user pastes) |

## Which lane for what

- Need a **generative texture, custom per-pixel effect, warp/displacement, or feedback look** the typed TOPs
  can't express → a GLSL shader → **`glsl_reference`**.
- Need a **parameter to track another parameter/node with math, a time-based value, or a small conditional**
  → a parameter expression → **`expr_reference`**. (For CHOP-channel-driven animation, prefer the typed
  `bind_chop` data binding — no code at all — and use an expression only for logic it can't express.)
- Need **event logic, stateful scripting, or a custom callback** → that is DAT/callback Python, the excluded
  raw path → **propose the code and hand it to the user to paste**; never deliver it through a tool.

## The consent handshake (never skip it)

Every code surface uses the same four steps — the safety gate is an *opportunity point*, not a dead end:

1. **Recognize + explain.** Say code is the right tool here, and teach what it will do and why. Explain along
   the way — this is a learning tool, not just an actuator.
2. **Propose the code.** Build the snippet from `glsl_reference` / `expr_reference` (cite the functions/roots),
   and state what it reads, what it writes/outputs, and where it goes (which node/parameter).
3. **Two consented paths to run it:**
   - **Validated lane** — for GLSL/expression only, and only if the user enabled the matching consent
     (`allow_glsl` / `allow_expr`): apply via `set_glsl` / `set_expr`, which validate the text before
     TouchDesigner ever sees it (fail-closed, allowlist-first).
   - **By hand** — otherwise, or for DAT/callback Python (always): hand the text over for the user to paste
     into the node themselves. The MCP never executes it.
4. **Verify + teach the result.** Read it back / look at it, and explain what happened.

Why this shape: a TD parameter expression and DAT script are evaluated as full host CPython (an RCE path if
handed to an agent), and raw GLSL can stall the GPU — so code is **AI-assisted and human-gated**, never
arbitrary. "No arbitrary code execution" stays literally true: the validated lanes accept only a provably
bounded subset, and everything else enters through the user's own hands.

## `*_opportunity` cues in recipes

Recipe steps flag where code is the real tool with a `glsl_opportunity` / `expr_opportunity` field (surfaced
by `recipe_reference`; search `opportunity`). Each carries `why` (the capability gap), `propose_via` (e.g.
`glsl_reference topic=patterns`), and `consent` (the handshake posture). Treat one as a cue to proactively
offer the shader/expression and teach it — don't silently wall the capability off.

## Sibling references
- `glsl_reference` — GLSL shader builtins, the TD glslTOP environment, patterns, and the GLSL handoff.
- `expr_reference` — the allowed parameter-expression surface, common expressions, and the expression handoff.
- `operator_reference` — any operator's full typed parameter schema. `recipe_reference` — tool-mapped
  workflows. `td_capabilities` — start-here orientation for the whole surface.
