# GLSL reference

A shared GLSL lookup for the user and the AI assistant, plus the workflow it supports. This is generic fragment-shader GLSL (public knowledge) organized as a scannable reference for writing pixel shaders on a TouchDesigner **GLSL TOP**, rather than a tutorial.

> **Curated vs exhaustive.** This sheet is a curated set of verified idioms that stay inside what the `set_glsl` lane's validator accepts. For the *complete* GLSL API — every builtin's full signature — consult the OpenGL/GLSL spec and your TouchDesigner build's own GLSL TOP documentation. Use this cookbook for the patterns and the handoff; the offline help for the exhaustive reference.

---

## Why this doc exists

The `touchdesigner-bridge-mcp` executor is **data-only**. Every capability is a fixed, typed handler; there is deliberately **no raw-script tool** and no "run this Python" path. The one place source text ever enters is the **validated, consent-gated GLSL lane** (`set_glsl`), and even there the text is GLSL bound to the GPU — never host Python, never an op callback. The tool never runs code on its own; it **proposes shader text** for the validated lane or for paste-by-hand.

GLSL is not an escape hatch here — it is **surfaced and gated**. The AI drives the network with typed tools up to the point a custom per-pixel shader is needed (a `glslTOP`), then proposes the shader text. The text runs only through the consented validated lane, or the user pastes it themselves. This sheet is the reference that makes that handoff fast and correct.

---

## The GLSL handoff

1. **AI builds the network** with typed tools up to the `glslTOP` where a custom pixel shader is needed, wiring the input TOPs the shader will sample and naming the node so the user can find it.
2. **AI states the intent** — what the shader computes per pixel, which input TOPs feed it (input 0, 1, ...), which uniforms it reads, and what it writes to the output.
3. **AI proposes the shader text** from this sheet (citing the functions), sized to pass the validator (see *Gotchas* for the limits).
4. **One of two consented paths runs it:**
   - **Validated lane** — if (and only if) the user has enabled `allow_glsl`, the AI may apply the source via `set_glsl`, which runs `validate_glsl` **before** TouchDesigner ever sees it, then creates and owns a Text DAT wired to the glslTOP's `pixeldat`.
   - **Paste-by-hand** — otherwise (or by preference), the AI hands the shader text to the user, who pastes it into a Text DAT and wires it to the glslTOP's `pixeldat` parameter (or types it into the glslTOP's pixel shader field). The AI never executes it.
5. **Verify + teach** — read the result back / look at it, and explain what happened.

Rule of thumb: the AI never runs GLSL on its own; it only proposes text, and that text runs only through the consented validated lane or the user's own paste.

---

## Surfacing a GLSL shader — when to offer, and the consent handshake

A custom pixel shader is a first-class part of TouchDesigner content, not an escape hatch — but here it is CONSENT-GATED. The AI's job is to *surface* it proactively and teach, never to reach for raw code on its own.

**When to surface one (offer it without being asked):** when the content needs per-pixel logic the typed TOPs can't express — a **generative texture field** (procedural noise/gradient math with no source clip), **custom per-pixel FX** (color/warp math beyond the fixed TOPs), **displacement / UV-warp math** driven by a formula, **feedback effects** (sampling last frame with a custom decay/advection), or **masks** a `rampTOP`/`compositeTOP` chain can't shape. Recipe steps flag these with a `glsl_opportunity` field (see `recipe_reference`) — treat it as a cue to offer. The recipes most often enhanced this way are `generative_texture_fx` (texture fields / FX), `mesh_freeform_warp` (displacement / UV warp), and the masking atlas (`facade_mask_atlas`).

**The consent handshake (never skip it):**
1. **Recognize + explain** — say a GLSL TOP is the right tool here, and teach what it will do and why. This MCP is also a TouchDesigner *learning* tool: explain along the way, don't just act.
2. **Propose the GLSL** — build the source from this reference (cite the functions), and state which input TOPs it samples, which uniforms it reads, and what it writes to the output.
3. **Two consented paths to run it:**
   - **Validated lane** — if (and only if) the user has enabled `allow_glsl`, the AI may apply the source via `set_glsl`, which validates it allowlist-first before TouchDesigner ever compiles it.
   - **Paste-by-hand** — otherwise (or by preference), hand the GLSL text over for the user to paste into a Text DAT wired to the glslTOP's `pixeldat` (the handoff above). The AI never executes it.
4. **Verify + teach the result** — read it back / look at it, and explain what happened.

Why this shape: GLSL runs on the GPU inside the driver's sandbox — there is **no host RCE, no file, no network reach** in the language. The residual risk is **availability only** (a heavy shader can provoke a recoverable GPU denial-of-service / driver reset). So the lane is not a language sandbox; it is a DoS-constrainer plus a delivery-hygiene gate, and it is consent-gated and fail-closed. Safe GLSL is not "no GLSL" — it is **AI-assisted, validated, consent-gated GLSL**.

---

## The TouchDesigner GLSL TOP environment

A GLSL TOP runs a **fragment (pixel) shader** once per output pixel and writes one color. The validated lane accepts the **pixel** stage only. The contract, with TD-specific names flagged:

- **Output.** Declare an output and write the final color through TouchDesigner's swizzle helper:
  ```glsl
  out vec4 fragColor;                       // the color this pixel writes
  // ... compute color ...
  fragColor = TDOutputSwizzle(color);       // TDOutputSwizzle: TD-specific (verify against your TD build)
  ```
  `TDOutputSwizzle()` maps your RGBA into the TOP's actual channel order/format; write through it rather than assigning `fragColor` raw.
- **Pixel coordinate input.** `vUV.st` is the 0..1 texture coordinate of the current pixel (`vUV`: TD-specific varying — verify against your TD build). Origin is bottom-left.
- **Sampling input TOPs.** Input TOPs arrive as a sampler array; sample with the standard `texture()` builtin:
  ```glsl
  uniform sampler2D sTD2DInputs[1];         // sTD2DInputs: TD-specific input array (verify against your TD build)
  vec4 src = texture(sTD2DInputs[0], vUV.st);   // input 0 at this pixel
  ```
- **Resolution / info uniforms.** TouchDesigner supplies per-input info; for example resolution is exposed through an info uniform (`uTD2DInfos[].res` — TD-specific; `.res.zw` = pixel resolution, `.res.xy` = 1/resolution on typical builds — verify against your TD build). When in doubt, pass what you need as your own uniform instead.
- **Your own uniforms.** Declare `uniform float uTime;` / `uniform vec2 uCenter;` etc. and set their **values on the glslTOP's Vectors / Uniforms page** (or drive them from a CHOP). Never hardcode a value you intend to animate — a uniform set on the node is the animatable, non-recompiling path.
- **`#version` is required.** The validator requires an explicit `#version` directive as the first line, one of `330`, `400`, `410`, `420`. Start every shader with e.g. `#version 330`.

Standard GLSL — the builtins, types (`float vec2 vec3 vec4 mat3 mat4`), swizzles (`.xyz`, `.st`, `.rgb`), and control flow below — you may state confidently; it is not version-specific within the allowed set. Only the **TD-specific** names above carry the "(verify against your TD build)" caveat.

---

## Core functions

Standard GLSL builtins, grouped. All accurate across the allowed `#version` set.

```glsl
// math (scalar or componentwise on vectors)
abs(x)              // magnitude
sign(x)             // -1 / 0 / +1
floor(x) ceil(x)    // round down / up
fract(x)            // x - floor(x)  → 0..1 sawtooth
mod(x, y)           // x - y*floor(x/y)  → wrap / tiling
min(a,b) max(a,b)   // extrema
clamp(x, lo, hi)    // constrain to a range
mix(a, b, t)        // linear blend  (a*(1-t) + b*t)
step(edge, x)       // 0 below edge, 1 at/above
smoothstep(a, b, x) // smooth 0..1 ramp between a and b
pow(x, e) exp(x) log(x) sqrt(x)
```

```glsl
// vector
length(v)           // magnitude
distance(a, b)      // length(a - b)
dot(a, b)           // projection / cosine
cross(a, b)         // perpendicular (vec3)
normalize(v)        // unit vector
reflect(i, n)       // reflect i about normal n
```

```glsl
// trig  (radians)
sin(x) cos(x) tan(x)
asin(x) acos(x) atan(y, x)   // atan(y,x) = full-quadrant angle
radians(deg) degrees(rad)
```

```glsl
// texture sampling  (sampler2D)
texture(samp, uv)               // filtered sample at a 0..1 uv
textureLod(samp, uv, lod)       // sample an explicit mip level
texelFetch(samp, ivec2(px), 0)  // exact texel, no filtering
```

Every function above is subject to the validator's loop-weighted texture-fetch cap only when it is a `texture*`/`texelFetch*` call inside a loop — plain math never counts.

---

## Patterns

Short, correct fragment snippets for projection-mapping content. Each stays well inside the validator limits.

**Moving gradient field** — animate a diagonal gradient with a uniform clock (no source clip):
```glsl
#version 330
out vec4 fragColor;
uniform float uTime;                        // set on the glslTOP's Vectors page (or drive from a CHOP)
void main() {
    vec2 uv = vUV.st;                       // vUV: TD-specific (verify against your TD build)
    float g = fract(uv.x + uv.y * 0.5 + uTime * 0.1);
    fragColor = TDOutputSwizzle(vec4(vec3(g), 1.0));   // TDOutputSwizzle: TD-specific (verify)
}
```
Why: `fract(... + uTime*speed)` is the canonical scrolling ramp; the uniform keeps it animatable without recompiling.

**Radial ramp / mask** — bright center falling off to the edges:
```glsl
#version 330
out vec4 fragColor;
uniform vec2 uCenter;                       // e.g. (0.5, 0.5), set on the node
void main() {
    float d = distance(vUV.st, uCenter);
    float m = 1.0 - smoothstep(0.2, 0.6, d);   // 1 at center → 0 past the radius
    fragColor = TDOutputSwizzle(vec4(vec3(m), 1.0));
}
```
Why: `smoothstep` gives a soft radial falloff you can multiply into content as a vignette or spotlight mask.

**UV distortion / displace** — warp an input TOP by a sine ripple:
```glsl
#version 330
out vec4 fragColor;
uniform sampler2D sTD2DInputs[1];           // sTD2DInputs: TD-specific (verify)
uniform float uTime;
void main() {
    vec2 uv = vUV.st;
    uv.x += sin(uv.y * 20.0 + uTime) * 0.02;   // horizontal ripple
    fragColor = TDOutputSwizzle(texture(sTD2DInputs[0], uv));
}
```
Why: offsetting the sample UV before `texture()` bends the source image — the per-pixel core of a warp/displace effect.

**Feedback trail** — blend the current source over the previous frame for a decaying trail:
```glsl
#version 330
out vec4 fragColor;
uniform sampler2D sTD2DInputs[2];           // 0 = new source, 1 = feedback (last frame)
uniform float uDecay;                       // e.g. 0.92
void main() {
    vec2 uv = vUV.st;
    vec4 src  = texture(sTD2DInputs[0], uv);
    vec4 prev = texture(sTD2DInputs[1], uv);
    fragColor = TDOutputSwizzle(max(src, prev * uDecay));   // fade the old, keep the bright new
}
```
Why: wire input 1 from a `feedbackTOP` tap of this TOP's own output; `prev * uDecay` is the trail fade. (Concept — wiring the feedback path is a network step.)

**Mask multiply** — punch content black where a mask atlas is 0 (keep projection off real windows):
```glsl
#version 330
out vec4 fragColor;
uniform sampler2D sTD2DInputs[2];           // 0 = content, 1 = grayscale mask
void main() {
    vec2 uv = vUV.st;
    vec4 content = texture(sTD2DInputs[0], uv);
    float mask   = texture(sTD2DInputs[1], uv).r;   // 0 = window (black), 1 = wall
    fragColor = TDOutputSwizzle(vec4(content.rgb * mask, content.a));
}
```
Why: the data-only masking recipe (`facade_mask_atlas`) multiplies a UV-space mask into the emit content — this is that multiply as one pixel op.

**Bounded accumulation loop** — a small static-count blur, showing a validator-legal loop:
```glsl
#version 330
out vec4 fragColor;
uniform sampler2D sTD2DInputs[1];
void main() {
    vec2 uv = vUV.st;
    vec4 acc = vec4(0.0);
    for (int i = 0; i < 8; i++) {           // fresh int counter, integer-literal bound, braced body
        float o = float(i) * 0.002;
        acc += texture(sTD2DInputs[0], uv + vec2(o, 0.0));
    }
    fragColor = TDOutputSwizzle(acc / 8.0);
}
```
Why: loops must be statically bounded with an integer-literal ceiling and a braced body; the counter is a fresh `int`, up-counts, and is never reassigned inside the body.

---

## Gotchas

- **`#version` is mandatory and first.** The validator requires an explicit `#version` (one of `330`, `400`, `410`, `420`) as the first directive. `#version 460` / compute versions are rejected — the lane is fragment-only.
- **No `while` / `do` loops.** Only statically-bounded `for` loops pass: `for (int i = 0; i < LIT; i++) { ... }` with an **integer-literal** bound (a uniform- or `textureSize`-driven bound is rejected), a **fresh `int`** counter that is **not** reassigned in the body, and a **braced** body. Ceilings: ≤ 4096 iters, nesting depth ≤ 3, product-of-ceilings ≤ 65536, ≤ 400 tokens per loop body.
- **No function-like `#define`, no `#include`.** Object-like `#define NAME value` only (≤ 32 of them, each line ≤ 128 chars). `#define SQ(x) ...`, `#include`, `#import`, and `#pragma` are all rejected.
- **No image / atomic ops.** `imageStore`/`imageLoad`, the `atomic*` family, and `image2D`-style declarations are the compute/UAV write surface and are out of the fragment lane.
- **ASCII only, size caps.** Source must be ASCII (no non-ASCII, no backtick, no `$`), ≤ 16 KB, ≤ 400 lines, ≤ 8000 tokens, ≤ 2000 calls, bracket nesting ≤ 32. Texture fetches are loop-weighted and capped at 4096 — a fetch inside an 8× loop counts as 8.
- **Set uniforms on the node, don't hardcode.** Animatable values (`uTime`, a center, a decay) belong on the glslTOP's Vectors / Uniforms page (or driven from a CHOP), declared `uniform` in the shader — not baked in as literals, which forces a recompile to change.
- **Output resolution vs input.** The GLSL TOP renders at *its own* resolution, not the input's; a shader that assumes it matches an input will misalign. The validated lane also **clamps the build resolution** (≤ 1280×720) and passes (≤ 4) as a GPU-DoS governor — raise the resolution and drive the output lane yourself for 4K delivery.
- **The 256×256 default trap.** A freshly created TOP often defaults to a small resolution; content can look blocky until you set the output resolution deliberately.
- **Coordinate origin.** `vUV.st` (TD-specific — verify against your TD build) is 0..1 with origin bottom-left; if content appears vertically flipped versus a source clip, invert `uv.y` (`uv.y = 1.0 - uv.y`).
- **Precision / banding.** Smooth gradients can band on 8-bit outputs; prefer a higher-bit-depth pixel format on the TOP for gradient-heavy fields.
- **Stay within the limits by construction.** If a proposed shader would exceed a cap, split the work across multiple TOPs (each a smaller shader) rather than growing one past the gate.

---

## Quick index

- Handoff → AI builds `glslTOP` → states intent → proposes text → `set_glsl` (if `allow_glsl`) **or** paste to `pixeldat` → verify
- Output → `out vec4 fragColor;` · `TDOutputSwizzle()` *(TD-specific — verify)*
- Pixel coord → `vUV.st` *(TD-specific — verify)*, origin bottom-left
- Sample inputs → `uniform sampler2D sTD2DInputs[N];` *(TD-specific — verify)* · `texture(samp, uv)`
- Info uniform → `uTD2DInfos[].res` *(TD-specific — verify)*; else pass your own uniform
- Math → `abs clamp mix smoothstep step pow mod fract floor sign`
- Vector → `length distance dot cross normalize reflect`
- Trig → `sin cos tan atan radians degrees`
- Texture → `texture textureLod texelFetch`
- Patterns → moving gradient · radial mask · UV distort · feedback trail · mask multiply · bounded loop
- Validator limits → `#version 330/400/410/420` first · no `while`/`do` · static integer-literal `for` bounds · no function-like `#define` · no `#include`/`#pragma` · no image/atomic · ASCII · ≤16 KB / ≤400 lines / ≤8000 tokens · fetch-weighted ≤4096 · build res clamped ≤1280×720
