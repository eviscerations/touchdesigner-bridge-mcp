# Parameter-expression reference

A shared lookup for the user and the AI assistant covering TouchDesigner **parameter expressions** — the small Python expression you put on a parameter so its value is *computed live* each cook instead of held as a constant. This is a scannable reference, not a tutorial, and it is scoped to exactly what the validated `set_expr` lane accepts.

> **This MCP is data-only, and also a learning tool.** Every capability is a fixed, typed handler. There is no raw-script / "run this Python" path. A parameter expression is the one place the surface touches live code, so it is delivered through a strict language sandbox (`validate_expr`) and is default-off. The tool never runs code on its own; it validates first and writes nothing until it passes.

---

## Why this doc exists

A TD parameter expression is evaluated as **CPython inside the TouchDesigner host process**. So the worst-case gap in the checker is not a recoverable GPU reset (as with the GLSL lane) — it is host remote code execution. That is why this lane is the **most gated** one in the whole MCP: it is default-off, and even when enabled every expression must pass a positive-allowlist AST sandbox before a single character reaches a parameter.

Expressions are not gone, though — they are **AI-assisted and human-gated**. The AI drives the network up to the parameter that needs a live value, states what it should compute, and then either applies a validated expression via `set_expr` (only if the user has enabled it) or hands the expression **text** to the user to type in by hand. The boundary stays intact either way.

**Prefer `bind_chop` first.** For animation driven by a CHOP channel — an LFO, audio level, a timer, a pattern — use `bind_chop` (or CHOP-export). That is a data *binding*, not code, and stays fully inside the data-only boundary. Reach for an expression only for the logic a binding cannot express: a conditional, a small computed constant, a value that reads another parameter with math, a per-cook time value with no CHOP behind it.

---

## The expression handoff

1. **AI builds the network** with typed tools up to the parameter that needs a live value, and names the node/parameter it reads from so the user can find it.
2. **AI states the intent** — what the parameter should compute, which node or parameter it reads, and whether a `bind_chop` binding would be the cleaner tool here.
3. **AI writes the expression** from this sheet, staying inside the allowed surface (below), and dry-runs it with `validate_expr` (needs no consent — it only says yes/no, writes nothing).
4. **Two consented paths to apply it:**
   - **Validated lane** — if (and only if) the user has enabled `allow_expr`, the AI may deliver the expression with `set_expr`, which re-validates it allowlist-first, refuses code-pointer parameters, switches the parameter to expression mode, and writes the verbatim `.expr`.
   - **Type-by-hand** — otherwise (or by preference), the AI hands the expression text to the user, who selects the parameter, switches it to expression mode, and types it in. The AI never executes it.
5. **Verify + teach** — read the parameter back (`scene_info` / `read_network`), confirm it computes what was intended, and explain what it does.

Rule of thumb: the AI proposes expression *text*; it only *applies* code through the one validated, consented, audited `set_expr` path.

---

## Surfacing an expression — when to offer, and the consent handshake

A parameter expression is a normal, first-class part of TouchDesigner — but here it is human-gated. The AI's job is to *surface* the opportunity and teach it, never to reach for live code on its own.

**When to surface one (offer it without being asked):** when a parameter must do something a static value or a CHOP binding cannot —
- **track another parameter/node with math** — e.g. a size that is always twice a sibling's `digits`, or a value read from another operator's channel/cell;
- **a time-based value** with no CHOP behind it — a phase from `absTime.seconds`, a frame counter via `me.time.frame`;
- **a conditional** — pick one value or another from a threshold (`1 if … else 0`);
- **a small computed constant** the typed create/`set_par` tools can't set statically — a normalized, clamped, or remapped scalar.

Recipe steps flag these with an `expr_opportunity` field (see `recipe_reference`) — treat it as a cue to offer. If the same motion could come from a CHOP, say so and prefer `bind_chop`.

**The consent handshake (never skip it):**
1. **Recognize + explain** — say an expression is the right tool here, and teach what it will compute and why. This MCP is also a TouchDesigner *learning* tool: explain, don't just act.
2. **Propose the expression** — build it from the allowed surface below, cite the roots it uses (`me` / `op(...)` / `math` / `tdu` / `absTime`), and state exactly what it reads.
3. **Two consented paths to run it** — the **validated lane** via `set_expr` *only if* `allow_expr` is enabled, else **type-by-hand** (the handoff above). The AI never executes it.
4. **Verify + teach the result** — read it back, and explain what happened.

**Why this shape:** a parameter expression is CPython in the host, and TD adds native code reaches a plain-Python allowlist would miss (`mod()` executes a DAT's text as Python; `run()` schedules a code string; `.ext` reaches user extension classes). So `validate_expr` is a true language-sandbox gate — a tiny declarative grammar over numbers, strings, and read-only navigation roots — and the lane ships default-off until that allowlist is signed off against the live API. Safe expressions are not "no expressions" — they are AI-assisted, human-gated expressions.

---

## The allowed expression surface

`validate_expr` (profile `expr_v1`) parses the source in `ast.parse(mode="eval")` — so **every statement is structurally impossible** (no `=`, `;`, `import`, `def`, `class`) — then walks a positive allowlist. Reject-by-omission is the default: anything not named below is refused, and nothing is sanitized or partially accepted.

### Raw bounds (checked before parse)
- **≤ 512 characters**, and a **single line** (no `\n` / `\r`).
- **ASCII-printable only** (kills unicode-homoglyph / zero-width identifier tricks by construction).
- Bracket nesting `([` capped (≤ 16), plus AST node-count / depth / call-count caps.

### Operators allowed
- **Arithmetic:** `+  -  *  /  //  %  **` and unary `+ - not ~`.
  `**` is special: the exponent must be a **small numeric literal** and nested `**` is refused (integer-exponent blow-up); use `math.pow` for anything dynamic or large.
- **Comparison:** `==  !=  <  <=  >  >=  in  not in`.
- **Boolean:** `and  or  not`.
- **Ternary:** `a if cond else b`.
- **Literal aggregates:** small `[list]` / `(tuple)` in read context, and subscript `x[...]` (index only — no `a:b` slices).

### Name roots allowed (a bare identifier must be one of these)
From `_ALLOWED_NAMES` — the TD navigation roots and safe modules/builtins:

```
me   op   ops   iop   parent   ipar        # TD navigation roots (read-only)
math   tdu   absTime                        # safe math module, TD utils, global time
True   False   None
abs   min   max   round   int   float   bool   str   len   pow   sorted   # safe builtins
```

### Calls allowed
- **Bare-name calls** (`_ALLOWED_CALL_NAMES`): `op ops iop parent ipar abs min max round int float bool str len pow sorted`.
- **Dotted calls** — the **full** dotted path must match `_ALLOWED_CALL_DOTTED` exactly (no free method calls):
  - `math.sin cos tan asin acos atan atan2 sqrt pow exp log log2 log10 floor ceil fabs radians degrees hypot fmod copysign`
  - `tdu.remap clamp Vector Position Matrix Quaternion rgb hsv`
- **No keyword arguments** (`op('x', y=1)` is refused). **No method calls on an `op(...)` result** — read a channel/cell/value with a **subscript** (`op('audio')['level']`), read a scalar with an allowlisted **attribute** (`me.time.frame`).

### Attributes allowed (read-only scalars / navigation)
An attribute is refused unless it is in `_ATTR_ALLOW`, is not underscore-prefixed, and is not in the deny set:

```
time seconds frame frames rate digits index fraction numSamples
start end width height aspect depth par name path
tx ty tz  rx ry rz  sx sy sz  x y z w  r g b a  u v  red green blue
```

### What is NOT allowed, and why
- **Statements of any kind** — assignment, `;`, `import`/`from`, `def`, `class`, `:=` walrus. `mode="eval"` makes them a parse error. *(An expression computes a value; it cannot run a program.)*
- **Comprehensions / generators / lambdas / f-strings / `*`-unpack / dict & set literals** — refused as `node.disallowed`. *(Each is a known CPython sandbox-escape shape.)*
- **Dunder / underscore attribute reach** — `__class__`, `__globals__`, `__subclasses__`, `__mro__`, … refused structurally (`attr.dunder`). *(This is the classic type-introspection escape.)*
- **TD-native code reaches** — the roots `mod` (executes a DAT's text as Python), `run` (schedules a code string), `ext`/`exts`/`extension` (user Python classes), and `root`/`project`/`app` (host-state reach) are **omitted from the roots** and the deny set; and attributes like `cook save store fetch create destroy copy run owner evalExpression pars` are denied. *(These are the TD-specific traps a Python-only allowlist would miss.)*
- **Code/introspection builtins** — `eval exec compile open __import__ getattr setattr globals locals vars type` are simply not in the allowed names, so a call to them fails `call.not_allowed`.
- **Bitwise / matmul operators** (`| & ^ << >> @`) and **slices** — refused.

If an expression stays inside the surface above, it passes `validate_expr`. If it strays outside, the validator names the failing rule and writes nothing.

---

## Common expressions

Each of these validates under `expr_v1` as written. What it does / why.

```python
me.time.frame                                  # this component's current frame — a running counter
me.time.seconds * 0.5                          # local time in seconds, half-speed — smooth phase
absTime.seconds                                # absolute wall-clock time (ignores timeline play state)
math.sin(absTime.seconds) * 0.5 + 0.5          # a 0..1 oscillation from a sine of time
op('lfo1')['tx']                               # read another node's channel/value by subscript
op('audio')['level']                           # live audio level pulled from an analysis CHOP
tdu.remap(op('audio')['level'], 0, 1, 0, 10)   # remap that 0..1 level into a 0..10 range
parent().digits * 2                            # twice the parent's replicated index — per-clone value
op('sections')[me.digits]                      # index a node by this clone's number
max(0, min(1, op('ctrl')['gain']))             # clamp a control value to 0..1
1 if op('sw')['on'] > 0.5 else 0               # a conditional switch driven by another value
math.floor(me.time.frame / 30)                 # integer seconds-count from frames (at 30 fps)
'sect_' + str(me.digits)                       # build a name string from a clone index
me.time.frame % 30 == 0                        # a once-per-second boolean pulse
```

Reach for these only where a `bind_chop` binding cannot do the job — a conditional, a computed constant, a name string, a read-with-math of another parameter. For continuous CHOP-channel animation, bind the channel instead.

---

## Beyond the lane — DAT / script Python (paste-handoff only)

Not everything is a parameter expression. A **Text DAT**, **Execute DAT**, **Script CHOP/SOP/DAT**, and a node's **`callbacks`** parameter all hold **full, unrestricted CPython** — they are the same category as the excluded raw-execution path. The MCP **never** writes to them and there is no tool that does; `set_expr` itself refuses code-pointer / code-sink parameters (`check_par_allowed`) even for pure arithmetic, because the parameter's *value* is interpreted as code.

When a task genuinely needs one of these — a callback that responds to an event, a Script CHOP that generates channels procedurally — the AI may **propose the code and teach it**, but it is delivered **by the user's own hands**:

1. The AI explains what the DAT/callback should do and why the expression lane can't cover it.
2. The AI writes the Python and states where it goes (which DAT, which callback).
3. **The user** creates the DAT / opens the callback and **pastes the code themselves**.
4. The user runs it and reports back; the AI resumes with typed tools.

This is the paste-handoff: the code enters the session through the user, never through a tool call. It mirrors the same posture the GLSL and expression lanes take at their edges — propose and teach, never auto-deliver full code.

---

## Gotchas

- **Expression vs `bind_chop`.** If the value comes from a CHOP channel, bind it — `bind_chop` is a data binding, cleaner, and inside the data-only boundary. Use an expression only for logic a binding can't express (conditional, computed constant, read-with-math). Don't reach for an expression to do a binding's job.
- **Single line only.** An expression is one logical line ≤ 512 chars — there is no room for a helper, a loop, or a temporary. If you're tempted to write `x = …; …`, it belongs in a DAT (paste-handoff), not a parameter.
- **Keep it declarative.** The validator accepts only arithmetic / compare / boolean / ternary over numbers, strings, and the read-only roots. Anything statement-shaped or introspective is refused by omission — build the value, don't build a program.
- **`me` is the node the expression lives on.** `me.time` is *that* component's Time COMP; `me.digits` is this clone's replicant index. It is not the node you're reading from — use `op('name')` (or `parent()`) to reach another node, and a subscript to read its channel/cell.
- **Read with a subscript, not a method.** `op('x')['ch']` reads a value; `op('x').eval()` / `.cook()` / any method call is refused. A scalar property comes from an allowlisted attribute (`me.time.frame`, `op('cam').tx`).
- **Cook dependencies.** An expression only re-evaluates when something it references changes (or on a time/frame reference each cook). Referencing `absTime.seconds` or `me.time.frame` makes the parameter cook every frame; referencing only a static value makes it cook only when that value changes. Reference a time root when you want continuous animation.
- **`**` is guarded.** `me.digits ** 2` is fine (small literal exponent); `2 ** op('x')['n']` and `9**9**9` are refused. For a dynamic or large power use `math.pow(base, exp)`.

---

## Quick index

- Delivery → `set_expr` (consent-gated by `allow_expr`, default off) · dry-run `validate_expr` (no consent)
- Prefer first → `bind_chop` for CHOP-channel animation (a binding, not code)
- Roots → `me  op  ops  iop  parent  ipar  math  tdu  absTime`
- Builtins → `abs min max round int float bool str len pow sorted`
- Operators → arithmetic `+ - * / // % **` · compare `== != < <= > >= in` · boolean `and or not` · ternary `a if c else b`
- `math.` → `sin cos tan asin acos atan atan2 sqrt pow exp log log2 log10 floor ceil fabs radians degrees hypot fmod copysign`
- `tdu.` → `remap clamp Vector Position Matrix Quaternion rgb hsv`
- Read attrs → `time seconds frame frames rate digits index fraction numSamples start end width height aspect depth par name path  tx ty tz rx ry rz sx sy sz  x y z w r g b a u v red green blue`
- Read a value → subscript `op('x')['ch']` (never a method call)
- Refused → statements · imports · comprehensions/lambdas/f-strings · dunder attrs · `mod`/`run`/`ext`/`app` · `eval`/`exec`/`open`/`getattr` · bitwise/matmul/slices
- Full CPython (DAT / callbacks) → paste-handoff only; the MCP never writes it
