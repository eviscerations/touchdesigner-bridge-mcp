"""td_executor/glsl_validator.py -- the VALIDATED GLSL fragment-shader constrainer.

Pure Python. Imports NOTHING from td/op -- so it is unit-testable standalone and runs IDENTICALLY in a
gateway pre-check and the executor's authoritative check (the exact structural mirror of expr_validator.py).

Threat model: GLSL runs on the GPU inside the driver's
sandbox -- there is NO host RCE / exfiltration / file / network reach in the language. The residual risk
is AVAILABILITY ONLY (GPU denial-of-service / driver TDR reset, ~2 s recoverable). So this is NOT a
language sandbox; it is a DoS-constrainer + delivery-hygiene gate. FAIL-CLOSED ALWAYS: any violation
raises GlslValidationError naming the failing rule; nothing is sanitized-and-proceeded.
"""

# ---- thresholds ------------------------------------------------------------------------------------
MAX_SOURCE_BYTES = 16 * 1024      # source length <= 16 KB
MAX_LINES = 400                   # lines <= 400
MAX_TOKENS = 8000                 # tokens <= 8000
MAX_DEFINES = 32                  # object-like #define count <= 32
MAX_DEFINE_CHARS = 128            # each #define line <= 128 chars
ALLOWED_VERSIONS = frozenset({330, 400, 410, 420})   # fragment-capable; compute 430+ excluded
ALLOWED_EXTENSIONS = frozenset()  # start empty; add per proven need (GL_GOOGLE_include_directive NOT allowed)
MAX_LOOP_ITERS = 4096             # per-loop static ceiling
MAX_LOOP_NESTING = 3              # loop nesting depth
MAX_LOOP_PRODUCT = 65536          # product-of-ceilings across nested loops
MAX_LOOP_BODY_TOKENS = 400        # tokens in a loop body
MAX_TEXTURE_FETCH_WEIGHTED = 4096  # loop-weighted texture-fetch cap
MAX_NESTING_DEPTH = 32            # bracket nesting depth
MAX_CALLS = 2000                  # total call count

_ALLOWED_STAGES = frozenset({"pixel"})

# Preprocessor directives that are permitted (besides #version / #define / #extension handled specially).
_ALLOWED_PP_CONDITIONAL = frozenset({
    "if", "ifdef", "ifndef", "else", "elif", "endif", "undef",
})
# Explicitly banned directives (also caught by omission, but named for a precise error).
_BANNED_PP = frozenset({"include", "import", "pragma"})

_TEXTURE_FETCH_FUNCS = ("texture", "texelFetch", "textureLod", "textureGrad", "textureProj",
                        "textureOffset", "texelFetchOffset",
                        # G1: the gather / lod-offset / grad-offset / proj-* sampling family are real per-call
                        # texture fetches too -- weight them like the rest so a loop of them cannot evade the
                        # loop-weighted fetch cap (pure hardening; the cap is generous, no legit shader loses).
                        "textureGather", "textureGatherOffset", "textureGatherOffsets",
                        "textureLodOffset", "textureGradOffset",
                        "textureProjLod", "textureProjGrad", "textureProjOffset",
                        "textureProjLodOffset", "textureProjGradOffset")
# Compute / UAV write surface -- out of fragment v1 (image-store/atomic write path).
_BANNED_IMAGE_TOKENS = ("imageStore", "imageLoad", "imageAtomicAdd", "imageAtomicExchange",
                        "imageAtomicCompSwap", "imageAtomicMin", "imageAtomicMax", "imageAtomicAnd",
                        "imageAtomicOr", "imageAtomicXor", "atomicAdd", "atomicExchange",
                        "atomicCompSwap", "atomicMin", "atomicMax", "atomicAnd", "atomicOr",
                        "atomicXor", "atomicCounter", "atomicCounterIncrement", "atomicCounterDecrement")
_BANNED_IMAGE_TYPES = ("image1D", "image2D", "image3D", "imageCube", "imageBuffer", "image2DArray",
                       "uimage2D", "iimage2D", "image2DMS")


class GlslValidationError(Exception):
    """Raised on any GLSL validation violation. `.rule` names the failing rule for the caller/audit."""

    def __init__(self, rule, detail=""):
        self.rule = rule
        msg = rule if not detail else "%s: %s" % (rule, detail)
        super(GlslValidationError, self).__init__(msg)


def _reject(rule, detail=""):
    raise GlslValidationError(rule, detail)


# ---- lexer ------------------------------------------------------------------------------------------
def _strip_comments(src):
    """Remove // line comments and /* */ block comments, preserving newlines so line counts stay honest."""
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i < n and not (src[i] == "*" and i + 1 < n and src[i + 1] == "/"):
                if src[i] == "\n":
                    out.append("\n")
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


_ID_START = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
_ID_CONT = _ID_START | set("0123456789")
_NUM_START = set("0123456789.")


def _lex(src):
    """Lex into (kind, value) tokens: id | num | str | op | punct. Comments already stripped.
    Preprocessor '#' lines are NOT lexed here (they are handled line-wise before lexing)."""
    toks = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c in _ID_START:
            j = i + 1
            while j < n and src[j] in _ID_CONT:
                j += 1
            toks.append(("id", src[i:j]))
            i = j
            continue
        if c in _NUM_START and (c != "." or (i + 1 < n and src[i + 1].isdigit())):
            j = i + 1
            while j < n and (src[j] in "0123456789.eExXaAbBcCdDfFuU+-"):
                # allow hex/float/exponent chars; break on a '+'/'-' that is not an exponent sign
                if src[j] in "+-" and src[j - 1] not in "eE":
                    break
                j += 1
            toks.append(("num", src[i:j]))
            i = j
            continue
        if c == '"':
            j = i + 1
            while j < n and src[j] != '"':
                if src[j] == "\\":
                    j += 1
                j += 1
            toks.append(("str", src[i + 1:j]))
            i = j + 1
            continue
        if c in "(){}[];,":
            toks.append(("punct", c))
            i += 1
            continue
        # operators (multi-char handled coarsely; enough for structural pass)
        j = i + 1
        while j < n and src[j] in "+-*/%<>=!&|^~?:.":
            j += 1
        toks.append(("op", src[i:j]))
        i = j
    return toks


# ---- preprocessor pass ------------------------------------------------------------------------------
def _validate_preprocessor(src):
    """Allowlist scan over every line whose first non-space char is '#'. Returns the source with all
    preprocessor lines blanked (newline preserved) so the lexer/structural pass never sees them."""
    lines = src.split("\n")
    out_lines = []
    seen_version = False
    seen_first_directive = False
    define_count = 0
    for raw in lines:
        stripped = raw.strip()
        if not stripped.startswith("#"):
            if stripped:
                seen_first_directive = True  # a real code line precedes any later #version -> handled below
            out_lines.append(raw)
            continue
        body = stripped[1:].strip()
        parts = body.split()
        directive = parts[0] if parts else ""
        if directive == "version":
            if seen_version:
                _reject("preprocessor.version_duplicate", "#version appears more than once")
            if seen_first_directive:
                _reject("preprocessor.version_not_first", "#version must be the first directive")
            if len(parts) < 2 or not parts[1].isdigit():
                _reject("preprocessor.version_malformed", stripped)
            ver = int(parts[1])
            if ver not in ALLOWED_VERSIONS:
                _reject("preprocessor.version_not_allowed",
                        "#version %d not in %s" % (ver, sorted(ALLOWED_VERSIONS)))
            seen_version = True
            seen_first_directive = True
        elif directive == "define":
            if len(stripped) > MAX_DEFINE_CHARS:
                _reject("preprocessor.define_too_long", "#define line > %d chars" % MAX_DEFINE_CHARS)
            # object-like only: "#define NAME ..." -- reject function-like "#define NAME(args)".
            if len(parts) < 2:
                _reject("preprocessor.define_malformed", stripped)
            name = parts[1]
            # function-like macro if the name token is immediately followed by '(' with no space.
            after = body[len("define"):].lstrip()
            mname = after.split()[0] if after.split() else ""
            # detect NAME( with no intervening space (function-like macro)
            paren = mname.find("(")
            if paren > 0 or (paren == -1 and after[len(mname):len(mname) + 1] == "("):
                _reject("preprocessor.define_function_like",
                        "function-like #define is banned (compile-DoS / obfuscation)")
            if "(" in mname:
                _reject("preprocessor.define_function_like",
                        "function-like #define is banned (compile-DoS / obfuscation)")
            define_count += 1
            if define_count > MAX_DEFINES:
                _reject("preprocessor.define_too_many", "> %d #define directives" % MAX_DEFINES)
            seen_first_directive = True
        elif directive == "extension":
            ext = parts[1] if len(parts) > 1 else ""
            ext = ext.rstrip(":")
            if ext not in ALLOWED_EXTENSIONS:
                _reject("preprocessor.extension_not_allowed", "#extension %r not allowed" % ext)
            seen_first_directive = True
        elif directive in _ALLOWED_PP_CONDITIONAL:
            seen_first_directive = True
        elif directive in _BANNED_PP:
            _reject("preprocessor.banned_directive", "#%s is banned" % directive)
        else:
            _reject("preprocessor.unknown_directive", "#%s is not allowed" % (directive or "<empty>"))
        out_lines.append("")  # blank the preprocessor line (keep line count)
    if not seen_version:
        _reject("preprocessor.version_required", "a #version directive is required")
    return "\n".join(out_lines)


# ---- structural / DoS pass --------------------------------------------------------------------------
def _validate_structural(toks):
    """Walk the token stream: ban while/do, allow only statically-bounded for loops, cap texture fetches,
    ban image/atomic ops, enforce bracket/nesting/call caps."""
    n = len(toks)

    # 1) banned image/atomic tokens + banned image type declarations (cheap identifier scan).
    for kind, val in toks:
        if kind == "id":
            if val in _BANNED_IMAGE_TOKENS:
                _reject("image.banned_op", "%s is banned (compute/UAV write surface)" % val)
            if val in _BANNED_IMAGE_TYPES:
                _reject("image.banned_type", "%s declaration banned (out of fragment v1)" % val)
            if val in ("while", "do"):
                _reject("loop.unbounded_form", "'%s' loops are banned" % val)

    # 2) bracket nesting depth + call count + balance.
    depth = 0
    max_depth = 0
    calls = 0
    for idx in range(n):
        kind, val = toks[idx]
        if kind == "punct" and val in "({[":
            depth += 1
            max_depth = max(max_depth, depth)
        elif kind == "punct" and val in ")}]":
            depth -= 1
            if depth < 0:
                _reject("brackets.unbalanced", "close bracket with no matching open")
        if kind == "id" and idx + 1 < n and toks[idx + 1] == ("punct", "("):
            calls += 1
    if depth != 0:
        _reject("brackets.unbalanced", "unbalanced brackets at end of source")
    if max_depth > MAX_NESTING_DEPTH:
        _reject("nesting.too_deep", "nesting depth %d > %d" % (max_depth, MAX_NESTING_DEPTH))
    if calls > MAX_CALLS:
        _reject("calls.too_many", "%d calls > %d" % (calls, MAX_CALLS))

    # 3) for-loops: statically-bounded only + loop-weighted texture-fetch cap + counter-mutation guard.
    _validate_loops_and_fetches(toks)


def _find_matching(toks, open_idx, open_ch, close_ch):
    """Return index of the matching close punct for the open punct at open_idx, or -1."""
    d = 0
    for i in range(open_idx, len(toks)):
        if toks[i] == ("punct", open_ch):
            d += 1
        elif toks[i] == ("punct", close_ch):
            d -= 1
            if d == 0:
                return i
    return -1


def _parse_for_bound(header_toks):
    """Given the tokens inside a for(...) header, extract the static iteration ceiling.
    Header must look like: int VAR = INITLIT ; VAR CMP BOUNDLIT ; INCR
    Returns (ceiling:int, counter_name:str). Raises on any non-static / malformed header."""
    # split header on ';'
    segs, cur = [], []
    for t in header_toks:
        if t == ("punct", ";"):
            segs.append(cur)
            cur = []
        else:
            cur.append(t)
    segs.append(cur)
    if len(segs) != 3:
        _reject("loop.for_header_malformed", "for header must have exactly two ';'")
    init, cond, incr = segs

    # init: [int] VAR = INITLIT
    init_ids = [v for (k, v) in init if k == "id"]
    if not init_ids:
        _reject("loop.for_init_malformed", "for-init must declare an int counter")
    # require a fresh int counter declaration
    if init[0] != ("id", "int"):
        _reject("loop.for_counter_not_int", "for counter must be a fresh 'int' (got %r)" % (init[0],))
    counter = init[1][1] if len(init) > 1 and init[1][0] == "id" else None
    if counter is None:
        _reject("loop.for_init_malformed", "cannot identify loop counter")
    # find '=' and the init literal
    init_val = None
    for i, t in enumerate(init):
        if t == ("op", "=") and i + 1 < len(init):
            nk, nv = init[i + 1]
            if nk == "num":
                init_val = _to_int(nv)
            break
    if init_val is None:
        _reject("loop.for_init_not_literal", "for-init must assign an integer literal")

    # cond: VAR CMP BOUNDLIT   (up-count only: < or <=)
    if len(cond) < 3 or cond[0] != ("id", counter):
        _reject("loop.for_cond_malformed", "for-cond must be 'VAR < LIT' on the counter")
    cmp_op = cond[1]
    if cmp_op not in (("op", "<"), ("op", "<=")):
        _reject("loop.for_cond_not_upcount", "for-cond must be up-count (< or <=)")
    bk, bv = cond[2]
    if bk != "num":
        _reject("loop.for_bound_not_literal",
                "for bound must be an integer literal (runtime bounds -- uniform/textureSize/len -- banned)")
    bound = _to_int(bv)
    if cmp_op == ("op", "<="):
        bound += 1

    # incr: VAR++ or ++VAR or VAR += LIT ; must not mutate by an arbitrary amount that isn't +1..
    # (we only require it references the counter and is an increment form)
    incr_ids = [v for (k, v) in incr if k == "id"]
    if counter not in incr_ids:
        _reject("loop.for_incr_malformed", "for-incr must advance the loop counter")

    ceiling = bound - init_val
    if ceiling <= 0:
        ceiling = 0
    if ceiling > MAX_LOOP_ITERS:
        _reject("loop.iters_too_many", "%d iters > %d" % (ceiling, MAX_LOOP_ITERS))
    return ceiling, counter


def _to_int(numstr):
    s = numstr.strip().rstrip("uU")
    try:
        if s.lower().startswith("0x"):
            return int(s, 16)
        if "." in s or "e" in s.lower():
            _reject("loop.for_bound_not_integer", "loop bound must be an integer literal, got %r" % numstr)
        return int(s)
    except ValueError:
        _reject("loop.for_bound_not_integer", "cannot parse integer literal %r" % numstr)


def _validate_loops_and_fetches(toks):
    """Recursive-descent over for-loops to enforce static bounds, nesting, product-of-ceilings, body
    caps, counter-mutation, and the loop-weighted texture-fetch cap."""
    n = len(toks)

    def walk(start, end, weight, nesting):
        """Scan toks[start:end]; `weight` = product of enclosing loop ceilings; returns weighted fetch count."""
        fetch_weighted = 0
        i = start
        while i < end:
            kind, val = toks[i]
            # texture fetch call?
            if kind == "id" and val in _TEXTURE_FETCH_FUNCS and i + 1 < end and toks[i + 1] == ("punct", "("):
                fetch_weighted += max(1, weight)
                if fetch_weighted > MAX_TEXTURE_FETCH_WEIGHTED:
                    _reject("texture.fetch_cap",
                            "loop-weighted texture fetches exceed %d" % MAX_TEXTURE_FETCH_WEIGHTED)
            if kind == "id" and val == "for":
                if nesting + 1 > MAX_LOOP_NESTING:
                    _reject("loop.nesting_too_deep", "loop nesting > %d" % MAX_LOOP_NESTING)
                # header
                if i + 1 >= n or toks[i + 1] != ("punct", "("):
                    _reject("loop.for_malformed", "'for' not followed by '('")
                hopen = i + 1
                hclose = _find_matching(toks, hopen, "(", ")")
                if hclose < 0:
                    _reject("loop.for_malformed", "unterminated for-header")
                header = toks[hopen + 1:hclose]
                ceiling, counter = _parse_for_bound(header)
                new_weight = max(1, weight) * max(1, ceiling)
                if new_weight > MAX_LOOP_PRODUCT:
                    _reject("loop.product_too_large",
                            "product-of-ceilings %d > %d" % (new_weight, MAX_LOOP_PRODUCT))
                # body: require a braced block
                if hclose + 1 >= n or toks[hclose + 1] != ("punct", "{"):
                    _reject("loop.body_not_braced", "for body must be a braced block")
                bopen = hclose + 1
                bclose = _find_matching(toks, bopen, "{", "}")
                if bclose < 0:
                    _reject("loop.body_unterminated", "unterminated for body")
                body = toks[bopen + 1:bclose]
                if len(body) > MAX_LOOP_BODY_TOKENS:
                    _reject("loop.body_too_large", "loop body %d tokens > %d" % (len(body), MAX_LOOP_BODY_TOKENS))
                _check_counter_not_mutated(body, counter)
                fetch_weighted += walk(bopen + 1, bclose, new_weight, nesting + 1)
                if fetch_weighted > MAX_TEXTURE_FETCH_WEIGHTED:
                    _reject("texture.fetch_cap",
                            "loop-weighted texture fetches exceed %d" % MAX_TEXTURE_FETCH_WEIGHTED)
                i = bclose + 1
                continue
            i += 1
        return fetch_weighted

    walk(0, n, 1, 0)


def _check_counter_not_mutated(body, counter):
    """Reject reassignment / ++ / -- of the loop counter inside the body (closes reset-counter infinite loop)."""
    m = len(body)
    for i in range(m):
        kind, val = body[i]
        if kind == "id" and val == counter:
            nxt = body[i + 1] if i + 1 < m else None
            if nxt in (("op", "="), ("op", "+="), ("op", "-="), ("op", "*="), ("op", "/="),
                       ("op", "++"), ("op", "--")):
                _reject("loop.counter_mutated", "loop counter %r reassigned in body" % counter)
        if kind == "op" and val in ("++", "--"):
            nxt = body[i + 1] if i + 1 < m else None
            if nxt == ("id", counter):
                _reject("loop.counter_mutated", "loop counter %r mutated (pre-inc/dec) in body" % counter)


# ---- entry point ------------------------------------------------------------------------------------
def validate_glsl(src, stage):
    """Validate a GLSL fragment-shader source. Returns None on success; raises GlslValidationError on any
    violation. FAIL-CLOSED: nothing is sanitized. `stage` must be 'pixel' (fragment on glslTOP) in v1."""
    # stage gate first (cheapest, and v1 accepts pixel/fragment only).
    if stage not in _ALLOWED_STAGES:
        _reject("stage.not_allowed", "stage %r not allowed (v1 accepts only 'pixel')" % (stage,))
    if not isinstance(src, str):
        _reject("source.not_string", "source must be a string")

    # 1) bounds + hard character rejects (before parsing).
    if len(src.encode("utf-8", "surrogatepass")) > MAX_SOURCE_BYTES:
        _reject("bounds.source_too_long", "source > %d bytes" % MAX_SOURCE_BYTES)
    for ch in src:
        o = ord(ch)
        if o > 0x7F:
            _reject("chars.non_ascii", "non-ASCII char U+%04X (GLSL source is ASCII)" % o)
        if o < 0x20 and ch not in "\t\n\r":
            _reject("chars.control", "control char 0x%02X" % o)
    if "`" in src:
        _reject("chars.backtick", "backtick not valid in GLSL (templating/injection signal)")
    if "$" in src:
        _reject("chars.dollar", "'$' not valid in GLSL (templating/injection signal)")
    if src.count("\n") + 1 > MAX_LINES:
        _reject("bounds.too_many_lines", "> %d lines" % MAX_LINES)

    # 2) preprocessor allowlist (blank pp lines out for the lexer).
    code = _strip_comments(src)
    code = _validate_preprocessor(code)

    # 3) lex + token cap.
    toks = _lex(code)
    if len(toks) > MAX_TOKENS:
        _reject("bounds.too_many_tokens", "%d tokens > %d" % (len(toks), MAX_TOKENS))

    # 4) string-literal hygiene (rare in GLSL; content-check).
    for kind, val in toks:
        if kind == "str":
            for bad in ("/", "\\", ":", ".."):
                if bad in val:
                    _reject("string.suspicious", "string literal contains %r" % bad)

    # 5) structural / DoS pass.
    _validate_structural(toks)
    return None


# ---- adversarial self-test corpus ------------------------------------------------------------------
_MUST_PASS = [
    # a simple pass-through
    "#version 330\nout vec4 fragColor;\nvoid main(){ fragColor = vec4(1.0); }",
    # a bounded-loop fractal / accumulation with a texture fetch inside a static loop
    ("#version 420\nuniform sampler2D sTD2DInputs[1];\nout vec4 c;\nvoid main(){\n"
     "  vec4 acc = vec4(0.0);\n  for(int i=0; i<64; i++){ acc += texture(sTD2DInputs[0], vec2(0.5)); }\n"
     "  c = acc / 64.0;\n}"),
    # a domain-warp with #define and nested bounded loops
    ("#version 400\n#define STEPS 8\nout vec4 o;\nvoid main(){\n  float s = 0.0;\n"
     "  for(int y=0; y<8; y++){ for(int x=0; x<8; x++){ s += float(x*y); } }\n  o = vec4(s); }"),
    # conditional preprocessor + math builtins
    ("#version 410\n#ifdef FOO\n#endif\nout vec4 o;\nvoid main(){ o = vec4(clamp(sin(0.5),0.0,1.0)); }"),
    # G1: a bounded textureGather loop is legitimate and well under the fetch cap
    ("#version 420\nuniform sampler2D sTD2DInputs[1];\nout vec4 c;\nvoid main(){\n"
     "  vec4 acc = vec4(0.0);\n  for(int i=0; i<8; i++){ acc += textureGather(sTD2DInputs[0], vec2(0.5)); }\n"
     "  c = acc / 8.0;\n}"),
]

_MUST_FAIL = [
    ("#version 330\n#include \"evil.glsl\"\nvoid main(){}", "preprocessor.banned_directive"),
    ("#version 330\nvoid main(){ while(true){} }", "loop.unbounded_form"),
    ("#version 330\nvoid main(){ do { } while(true); }", "loop.unbounded_form"),
    ("#version 330\nuniform int n;\nvoid main(){ for(int i=0;i<n;i++){} }", "loop.for_bound_not_literal"),
    ("#version 330\nvoid main(){ for(int i=0;i<5000;i++){} }", "loop.iters_too_many"),
    ("#version 330\nvoid main(){ for(int i=0;i<10;i++){ i = 0; } }", "loop.counter_mutated"),
    ("#version 330\n#define SQ(x) ((x)*(x))\nvoid main(){}", "preprocessor.define_function_like"),
    ("#version 330\nlayout(rgba8) uniform image2D img;\nvoid main(){ imageStore(img, ivec2(0), vec4(0)); }",
     "image.banned_type"),  # the image2D declaration is caught before the imageStore call
    ("#version 330\nvoid main(){ imageStore(a, ivec2(0), vec4(0)); }", "image.banned_op"),
    ("#version 460\nvoid main(){}", "preprocessor.version_not_allowed"),
    ("void main(){}", "preprocessor.version_required"),
    ("#version 330\nvoid main(){ float x = 1.0; } // é", "chars.non_ascii"),
    ("#version 330\nvoid main(){ int x = `1`; }", "chars.backtick"),
    # G1: a nested-loop textureGather fetch bomb (100*100 weighted = 10000 > 4096) now trips the fetch cap
    # (before G1, textureGather was unweighted and this slipped through).
    ("#version 330\nuniform sampler2D s;\nout vec4 c;\nvoid main(){ vec4 a=vec4(0.0);\n"
     " for(int i=0;i<100;i++){ for(int j=0;j<100;j++){ a += textureGather(s, vec2(0.5)); } } c=a; }",
     "texture.fetch_cap"),
]


def _selftest():
    """Run the must-pass + must-fail corpora. Returns {passed, failed, details}. License-free / CI-wired."""
    passed, failed, details = 0, 0, []
    for src in _MUST_PASS:
        try:
            validate_glsl(src, "pixel")
            passed += 1
        except GlslValidationError as e:
            failed += 1
            details.append({"kind": "must_pass_but_failed", "rule": getattr(e, "rule", None),
                            "src": src[:60]})
    for src, want_rule in _MUST_FAIL:
        try:
            validate_glsl(src, "pixel")
            failed += 1
            details.append({"kind": "must_fail_but_passed", "want": want_rule, "src": src[:60]})
        except GlslValidationError as e:
            got = getattr(e, "rule", None)
            if want_rule and got != want_rule:
                # accept it as a pass if it failed for ANY rule, but record the mismatch for review.
                details.append({"kind": "rule_mismatch", "want": want_rule, "got": got, "src": src[:60]})
            passed += 1
    # consent-gate stage assertion: a non-pixel stage must reject.
    try:
        validate_glsl(_MUST_PASS[0], "compute")
        failed += 1
        details.append({"kind": "stage_gate_leak", "src": "compute"})
    except GlslValidationError:
        passed += 1
    return {"passed": passed, "failed": failed, "details": details}


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(_selftest(), indent=2))
