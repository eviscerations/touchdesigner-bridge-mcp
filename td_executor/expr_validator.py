"""td_executor/expr_validator.py -- the VALIDATED parameter-EXPRESSION constrainer (profile "expr_v1").

Pure Python, imports ONLY stdlib (`ast`, and `json` under __main__). Imports NOTHING from td/op -- so it
is unit-testable standalone and runs IDENTICALLY as a gateway pre-check and the executor's authoritative
check (the exact structural mirror of `td_executor/glsl_validator.py`).

Threat model: a TD parameter
expression is evaluated as CPython in the TD host process, so the worst-case validator gap is HOST RCE -- a
categorically higher burden than the GLSL lane's recoverable GPU-DoS. This validator is therefore a true
language-sandbox gate, not merely a DoS constrainer: it permits only a tiny DECLARATIVE grammar
(arithmetic / compare / boolean / ternary over numbers, strings, and a fixed set of read-only TD navigation
roots) where `__import__` / `eval` / `getattr` / `os` / the `().__class__.__bases__[0].__subclasses__()`
dunder-traversal / the TD-native `mod` / `run` / `ext` code reaches are ALL unreachable BY OMISSION.

Mechanism (four independent positive-allowlist layers, fail-closed):
  0. raw bounds + hard-character rejects (length, single-line, ASCII-only, raw bracket-nesting cap) BEFORE parse
  1. `ast.parse(text, mode="eval")` -- structurally forbids EVERY statement (import/assign/`;`/def/class)
  2. node-count / AST-depth caps (bounds the AST-bomb DoS)
  3. a strict NodeVisitor: node-type allowlist + Name-root allowlist + call-target allowlist + attribute
     policy (structural `_`-prefix deny + positive allow) + subscript policy. Reject-by-omission is the default.

FAIL-CLOSED ALWAYS: any violation raises ExprValidationError naming the failing rule; NOTHING is sanitized
or partially accepted. Nothing is written by this module -- it only says yes (returns None) or no (raises).

HONEST RESIDUAL: the AST-allowlist closes the entire classic CPython
escape family OFFLINE (proven by the adversarial corpus below). What is NOT offline-provable is that no
*non-underscored, allowlisted* attribute or `tdu` return value on a LIVE TD OP/Par/tdu object hands back a
Python object with a reachable eval. That surface is a GATING live-API audit task; until it is done the
positive ATTR_ALLOW / ALLOWED_CALL_DOTTED lists are treated as unproven and kept as small as the recipes allow.
"""

import ast

# ---- thresholds (the expr_v1 profile constants; mirror glsl_validator's MAX_* block) ----------------
_MAX_LEN = 512            # source length <= 512 bytes (single logical expression)
_MAX_NODES = 120          # total AST node count
_MAX_DEPTH = 16           # AST tree depth
_MAX_CALLS = 16           # Call node count
_MAX_ATTR_CHAIN = 4       # attribute-chain depth (me.time.seconds = 2)
_MAX_POW_EXP = 64         # max literal exponent for `**` AND builtin pow() (see the Pow/pow guards)
_MAX_MULT_REPEAT = 65536  # max literal string-repetition count (str_lit * int_lit) -- closes the E2 alloc bomb
_MAX_RAW_NESTING = 16     # raw ()/[] bracket nesting depth, scanned on the source string BEFORE parse.
#   NOTE (addition the plan is silent on): CPython collapses redundant parens -- "("*200+"1"+")"*200 is 401
#   chars (under _MAX_LEN) yet parses to a single Constant (AST depth 1), so the AST caps in step 2 would let
#   a paren-nesting "depth bomb" through. A raw pre-parse bracket-depth scan closes that by construction and
#   also relieves parser-stack pressure. Value chosen to match _MAX_DEPTH.

_PROFILES = frozenset({"expr_v1"})

# ── NODE-TYPE ALLOWLIST (every other ast node type -> reject "node.disallowed") ──────────────────────
_ALLOWED_NODES = frozenset({
    ast.Expression,                              # the eval-mode root wrapper
    ast.Constant,                                # numbers / str / True / False / None
    ast.Name, ast.Load,                          # identifier reads only (Load ctx)
    ast.BinOp, ast.UnaryOp, ast.BoolOp,          # arithmetic / logic
    ast.Compare, ast.IfExp,                      # comparisons, `a if c else b`
    ast.Attribute,                               # STRICT policy (visit_Attribute)
    ast.Call,                                    # STRICT policy (visit_Call)
    ast.Subscript, ast.Index,                    # op('x')['ch'] ; ast.Index only exists on py<3.9
    ast.List, ast.Tuple,                         # small literal aggregates (Load ctx)
    # arithmetic / compare / bool operator leaf nodes (always visited as children):
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.UAdd, ast.USub, ast.Not, ast.Invert,
    ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
})
# DENIED by omission (named so the design teaches + a future edit can't silently re-add them):
#   Import, ImportFrom, Lambda, ListComp, SetComp, DictComp, GeneratorExp, comprehension, Starred,
#   NamedExpr(:=), JoinedStr/FormattedValue (f-strings), Await, Yield, YieldFrom, Assign/AugAssign/AnnAssign,
#   FunctionDef, ClassDef, Slice(a:b:c), Dict, Set, MatMult(@), BitOr/BitAnd/BitXor/LShift/RShift.

# ── NAME-ROOT ALLOWLIST (a bare Name load must be one of these) ──────────────────────────────────────
_ALLOWED_NAMES = frozenset({
    "me", "op", "ops", "iop", "parent", "ipar",     # TD navigation roots
    "math", "tdu", "absTime",                       # safe modules / time
    "True", "False", "None",                        # (parse as Constant on py3; kept for clarity)
    "abs", "min", "max", "round", "int", "float", "bool", "str", "len", "pow", "sorted",  # safe builtins
})
# DELIBERATELY EXCLUDED roots (each an RCE/DoS reach) -- rejected by omission, named for the design:
#   mod  -> mod('textdat') EXECUTES a DAT's text as Python              <- key TD trap
#   run  -> run('code', delayFrames=..) SCHEDULES a code string         <- key TD trap
#   ext  -> extension objects = user-authored Python classes (code)
#   root/project/app -> host-state reach (app.quit()/project.save())
#   var/eval/exec/compile/open/__import__/getattr/setattr/globals/locals/vars/type/object/super/
#   input/breakpoint/memoryview/property -> builtin code/introspection reaches.

# ── CALL-TARGET ALLOWLIST (a Call is legal ONLY if its func matches here) ─────────────────────────────
_ALLOWED_CALL_NAMES = frozenset({"op", "ops", "iop", "parent", "ipar", "abs", "min", "max", "round",
                                 "int", "float", "bool", "str", "len", "pow", "sorted"})
# dotted targets -- the FULL dotted path must be in this frozen set (NO free method calls):
_ALLOWED_CALL_DOTTED = frozenset({
    "math.sin", "math.cos", "math.tan", "math.asin", "math.acos", "math.atan", "math.atan2",
    "math.sqrt", "math.pow", "math.exp", "math.log", "math.log2", "math.log10", "math.floor",
    "math.ceil", "math.fabs", "math.radians", "math.degrees", "math.hypot", "math.fmod", "math.copysign",
    "tdu.remap", "tdu.clamp", "tdu.Vector", "tdu.Position", "tdu.Matrix", "tdu.Quaternion",
    "tdu.rgb", "tdu.hsv",
})
# NO method calls on op(...) results in v1 (no .eval()/.fetch()/.create()/.mod). A channel/cell/value is read
# with a SUBSCRIPT (op('x')['level']); a scalar property is read with an allowlisted ATTRIBUTE. The huge TD
# OP/Par method surface stays entirely out of reach.

# ── ATTRIBUTE POLICY (applied to every ast.Attribute NOT consumed as a dotted call target) ────────────
#  1. STRUCTURAL: reject if attr startswith "_"  -> kills every dunder + single-underscore internal
#     (__class__, __bases__, __globals__, __subclasses__, __mro__, __builtins__, f_globals, ...).
#  2. reject if attr in _ATTR_DENY (defense-in-depth; TD-specific escapes that are NOT underscored):
_ATTR_DENY = frozenset({"mod", "module", "ext", "exts", "extension", "create", "destroy", "copy", "cook",
                        "save", "store", "fetch", "unstore", "storage", "run", "pars", "evalExpression",
                        "owner", "currentPar"})
#  3. POSITIVE: reject unless attr in _ATTR_ALLOW (read-only scalars/navigation the recipes actually use):
_ATTR_ALLOW = frozenset({"time", "seconds", "frame", "frames", "rate", "digits", "index", "fraction",
                         "numSamples", "start", "end", "width", "height", "aspect", "depth", "par", "name",
                         "path", "tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz", "x", "y", "z", "w",
                         "r", "g", "b", "a", "u", "v", "red", "green", "blue"})


class ExprValidationError(ValueError):
    """Raised on any expression validation violation. `.rule` names the failing rule for the caller/audit
    (the exact `.rule`-carrying shape of GlslValidationError)."""

    def __init__(self, rule, detail=""):
        self.rule = rule
        msg = rule if not detail else "%s: %s" % (rule, detail)
        super(ExprValidationError, self).__init__(msg)


def _reject(rule, detail=""):
    raise ExprValidationError(rule, detail)


def _dotted(node):
    """Return the full dotted path for an Attribute/Name call target (e.g. 'math.sin'), else None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _literal_value(node):
    """Return a str/int/float literal value for `node` (unwrapping a unary +/-), else None. Bool is treated
    as NOT a numeric literal here (it is never a repetition count or a giant-int exponent)."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        node = node.operand
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float)) \
            and not isinstance(node.value, bool):
        return node.value
    return None


def _is_small_literal_exponent(node):
    """True iff `node` is a numeric-literal exponent bounded enough to never build a giant int: a float literal
    (float pow raises OverflowError, not a host hang) or an int literal with abs <= _MAX_POW_EXP. Dynamic
    (name/call/subscript) or oversized-integer exponents -> False (steer the caller to math.pow)."""
    v = _literal_value(node)
    if isinstance(v, float):
        return True
    return isinstance(v, int) and abs(v) <= _MAX_POW_EXP


def _check_mult_bomb(node):
    """Close the statically-provable multiplication allocation bomb (E2): a STRING literal repeated by an INT
    literal above _MAX_MULT_REPEAT ('a' * 999999999 allocates ~GB at eval time). ONLY the fully-literal case is
    rejected -- runtime operands (op('x')['n'] * 2, a string cell * a count) are the whole point of the lane
    and pass untouched (their unbounded case is the disclosed, not-statically-decidable availability residual)."""
    lv, rv = _literal_value(node.left), _literal_value(node.right)
    for s, k in ((lv, rv), (rv, lv)):
        if isinstance(s, str) and isinstance(k, int) and k > _MAX_MULT_REPEAT:
            _reject("mult.repeat_too_large",
                    "literal string repeated %d > %d times (allocation bomb); repeat at runtime instead"
                    % (k, _MAX_MULT_REPEAT))


class _Validator(ast.NodeVisitor):
    """The executor-authoritative structural check (the role glsl_validator's _validate_structural plays).
    generic_visit is DEFAULT-DENY: any node type not explicitly whitelisted raises."""

    def __init__(self):
        self.calls = 0

    def generic_visit(self, node):
        if type(node) not in _ALLOWED_NODES:
            _reject("node.disallowed", type(node).__name__)
        super(_Validator, self).generic_visit(node)

    def visit_Name(self, node):
        if not isinstance(node.ctx, ast.Load):
            _reject("name.not_load", "names are read-only (no assignment/deletion)")
        if node.id not in _ALLOWED_NAMES:
            _reject("name.not_allowed", node.id)

    def visit_Attribute(self, node):
        if not isinstance(node.ctx, ast.Load):
            _reject("attr.not_load", "attribute writes are forbidden")
        a = node.attr
        if a.startswith("_"):                       # (1) structural dunder/internal block -- load-bearing
            _reject("attr.dunder", a)
        if a in _ATTR_DENY:                          # (2) TD-specific non-underscored escapes
            _reject("attr.denied", a)
        if a not in _ATTR_ALLOW:                     # (3) positive allowlist
            _reject("attr.not_allowed", a)
        depth, cur = 0, node                         # (4) chain-length cap
        while isinstance(cur, ast.Attribute):
            depth += 1
            cur = cur.value
        if depth > _MAX_ATTR_CHAIN:
            _reject("attr.chain_too_deep", "%d > %d" % (depth, _MAX_ATTR_CHAIN))
        self.generic_visit(node)                     # validate node.value

    def visit_Call(self, node):
        self.calls += 1
        if self.calls > _MAX_CALLS:
            _reject("call.too_many", "%d > %d" % (self.calls, _MAX_CALLS))
        if node.keywords:
            _reject("call.keywords_banned", "keyword arguments are not allowed")
        f = node.func
        ok = False
        if isinstance(f, ast.Name):
            ok = f.id in _ALLOWED_CALL_NAMES
        elif isinstance(f, ast.Attribute):
            ok = _dotted(f) in _ALLOWED_CALL_DOTTED  # FULL dotted path must match; blocks x.evil()
        if not ok:
            _reject("call.not_allowed", _dotted(f) or getattr(f, "id", type(f).__name__))
        # E1: builtin pow(base, exp) is the same giant-int amplifier as '**' (pow(2, 99999999999) builds a
        # ~12 GB int through the front door the '**' guard closes). Gate the 2-arg exponent to a small literal,
        # exactly like the operator; the 3-arg modular pow(b, e, m) is bounded by the modulus (CPython never
        # forms the giant intermediate) so it is left to the generic argument walk. No GREEN idiom uses a
        # dynamic/oversized pow exponent, so this costs zero legitimate capability.
        if isinstance(f, ast.Name) and f.id == "pow" and len(node.args) == 2:
            if not _is_small_literal_exponent(node.args[1]):
                _reject("pow.exponent",
                        "pow() exponent must be a numeric literal <= %d; use math.pow otherwise" % _MAX_POW_EXP)
        # Walk ONLY the arguments. Do NOT generic_visit(node): that would re-descend node.func as a bare
        # Attribute and trip visit_Attribute on 'sin'/'remap' etc. The dotted target is already validated.
        for arg in node.args:
            self.visit(arg)

    def visit_BinOp(self, node):
        # ADVERSARIAL HARDENING beyond the plan (which lists ast.Pow in the node allowlist): the integer `**`
        # operator is a trivial host-HANG/OOM amplifier -- `9**9**9` (7 chars, passes every other gate) makes
        # CPython build a ~369-million-digit int. Close it WITHOUT breaking `x ** 2`: forbid nested `**` (which
        # is how a small-literal chain reaches a huge exponent, on either side) and require the exponent to be a
        # small numeric literal. Dynamic / large exponentiation must go through math.pow (float, bounded --
        # raises OverflowError, never a giant-int hang). Stricter than the plan, never looser.
        if isinstance(node.op, ast.Pow):
            if isinstance(node.left, ast.BinOp) and isinstance(node.left.op, ast.Pow):
                _reject("pow.nested", "nested '**' is not allowed (exponent blow-up); use math.pow")
            if isinstance(node.right, ast.BinOp) and isinstance(node.right.op, ast.Pow):
                _reject("pow.nested", "nested '**' is not allowed (exponent blow-up); use math.pow")
            if not _is_small_literal_exponent(node.right):
                _reject("pow.exponent",
                        "'**' exponent must be a numeric literal <= %d; use math.pow otherwise" % _MAX_POW_EXP)
        elif isinstance(node.op, ast.Mult):
            _check_mult_bomb(node)   # E2: reject only the literal string-repetition allocation bomb
        self.generic_visit(node)

    def visit_Subscript(self, node):
        if isinstance(node.slice, ast.Slice):
            _reject("subscript.slice_banned", "slices (a:b:c) are not allowed; index only")
        self.generic_visit(node)


def validate_expr(src, mode="eval"):
    """Validate a TD parameter expression. Returns None on success; raises ExprValidationError on ANY
    violation. FAIL-CLOSED: nothing is sanitized, nothing is written.

    `mode` must be "eval" -- a single expression. (The parameter is named `mode` to mirror ast.parse's
    contract and the task spec; the frozen profile it selects is "expr_v1". Any other mode is refused.)
    """
    # profile / mode gate first (cheapest). v1 accepts only the single-expression eval mode.
    if mode not in ("eval", "expr_v1"):
        _reject("mode.not_allowed", "mode %r not allowed (v1 accepts only 'eval')" % (mode,))
    if not isinstance(src, str) or not src:
        _reject("source.empty", "expression must be a non-empty string")

    # 1) raw bounds + hard-character rejects (BEFORE parsing).
    if len(src) > _MAX_LEN:
        _reject("bounds.too_long", "source %d > %d chars" % (len(src), _MAX_LEN))
    if "\n" in src or "\r" in src:
        _reject("bounds.multiline", "expression must be a single line")
    for ch in src:
        o = ord(ch)
        if o > 0x7E or o < 0x20:
            # ASCII-printable only. Kills the unicode-homoglyph/NFKC identifier vector (e.g. Cyrillic 'e',
            # zero-width joiners inside a string) by construction, before ast.parse ever runs. No tab/newline
            # is needed in a single-line expression.
            _reject("chars.non_ascii_or_control", "char U+%04X" % o)

    # 1b) raw bracket-nesting-depth cap (closes the paren-collapse depth bomb; see _MAX_RAW_NESTING note).
    depth = 0
    for ch in src:
        if ch in "([":
            depth += 1
            if depth > _MAX_RAW_NESTING:
                _reject("bounds.nesting_too_deep", "raw bracket nesting > %d" % _MAX_RAW_NESTING)
        elif ch in ")]":
            depth -= 1

    # 2) parse. mode="eval" structurally forbids ALL statements -- root MUST be a single ast.Expression.
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError as e:
        _reject("syntax.invalid", str(e))

    # 3) node-count + AST-depth caps (one pre-walk). Bounds the AST-bomb / deeply-nested DoS.
    n = 0

    def _walk_depth(node, d=0):
        nonlocal n
        n += 1
        return max([d] + [_walk_depth(c, d + 1) for c in ast.iter_child_nodes(node)])

    if _walk_depth(tree) > _MAX_DEPTH or n > _MAX_NODES:
        _reject("bounds.too_large", "nodes=%d depth-check exceeded caps" % n)

    # 4) the NodeVisitor allowlist -- raises on the first violation; else returns None.
    _Validator().visit(tree)
    return None


# ---- adversarial self-test corpus ------------------------------------------------------------------
# GREEN -- the real recipe idioms; each MUST validate.
_MUST_PASS = [
    "me.time.frame",
    "me.time.seconds * 0.5",
    "absTime.seconds",
    "math.sin(absTime.seconds) * 0.5 + 0.5",
    "op('lfo1')['tx']",
    "op('audio')['level']",
    "tdu.remap(op('audio')['level'], 0, 1, 0, 10)",
    "parent().digits * 2",
    "me.digits",
    "op('sections')[me.digits]",
    "max(0, min(1, op('ctrl')['gain']))",
    "1 if op('sw')['on'] > 0.5 else 0",
    "math.floor(me.time.frame / 30)",
    "'sect_' + str(me.digits)",                       # string concat of literals + safe builtin
    "-op('ctrl')['bias']",                            # unary
    "me.time.frame % 30 == 0",                        # compare + mod
    "me.digits ** 2",                                 # a small literal-exponent power (bounded)
    "pow(op('audio')['level'], 2)",                   # builtin pow with a small-literal exponent (preserved)
    "pow(2, 10, 7)",                                  # 3-arg modular pow -- bounded by the modulus (allowed)
    "'sect_' * me.digits",                            # runtime string repetition (count is not a literal)
    "'-' * 8",                                         # small literal string repetition (well under the cap)
]

# RED -- sandbox-escape attempts; each MUST raise. (rule = the expected/plan rule, or None to accept any
# rejection.) Grouped by escape CLASS so a future reader sees the coverage.
_MUST_FAIL = [
    # 1. dunder / type-introspection reach (the CPython escape a token allowlist misses).
    #    NOTE: the __subclasses__() forms are top-level Call nodes, so the call-target gate fires FIRST
    #    (call.not_allowed) -- defense-in-depth ahead of the attr.dunder gate that would also catch them.
    ("().__class__.__bases__[0].__subclasses__()", "call.not_allowed"),
    ("().__class__.__mro__[1].__subclasses__()[0]", "call.not_allowed"),
    ("(1).__class__.__base__.__subclasses__()", "call.not_allowed"),
    ("type(me).__init__.__globals__", "attr.dunder"),   # bare attribute chain at top -> dunder gate
    ("me.__class__", "attr.dunder"),
    # 2. comprehension / generator subclass-walk
    ("[c for c in ().__class__.__mro__]", "node.disallowed"),
    ("[x.__name__ for x in ().__class__.__subclasses__()]", "node.disallowed"),
    ("(c for c in [].__class__.__base__.__subclasses__())", "node.disallowed"),
    # 3. dynamic attribute fetch / string-built names
    ("getattr(me,'__cl'+'ass__')", "call.not_allowed"),
    ("getattr(op('x'),'destroy')()", "call.not_allowed"),
    # 4. import / exec / eval / open / os reach
    ("__import__('os').system('calc')", "call.not_allowed"),
    ("eval('1')", "call.not_allowed"),
    ("exec('x=1')", "call.not_allowed"),
    ("open('C:/secret','r')", "call.not_allowed"),
    ("compile('1','','eval')", "call.not_allowed"),
    # 5. TD-native code reaches (the traps a Python-only allowlist would MISS)
    ("mod('evildat').run()", "call.not_allowed"),
    ("me.mod.evildat.fn()", "call.not_allowed"),
    ("run('__import__(\\'os\\')')", "call.not_allowed"),
    ("op('x').ext.MyClass.method()", "call.not_allowed"),
    ("app.quit()", "call.not_allowed"),
    ("project.save('x')", "call.not_allowed"),
    ("op('x').create('textDAT')", "call.not_allowed"),
    ("op('x').par.file.eval()", "call.not_allowed"),
    # 6. f-string / format-spec / conversion tricks
    ("f'{me.__class__}'", "node.disallowed"),
    ("f'{op(\"x\")!r}'", "node.disallowed"),
    ("'{0.__class__}'.format(me)", "call.not_allowed"),
    ("'%s' % ().__class__", "attr.dunder"),
    # 7. unicode-homoglyph / non-ASCII identifier normalization
    ("getattr(me,'__cl\u200dass__')", "chars.non_ascii_or_control"),   # zero-width joiner in the string
    ("m\u0435.digits", "chars.non_ascii_or_control"),                  # Cyrillic 'e' homoglyph root
    # 8. walrus / starred / lambda / dict-set escape shapes
    ("(x:=me).digits", "node.disallowed"),
    ("[*().__class__.__bases__]", "node.disallowed"),
    ("(lambda: ().__class__)()", "call.not_allowed"),
    ("{().__class__: 1}", "node.disallowed"),
    # 9. subscript-as-escape on builtins
    ("[].__class__", "attr.dunder"),
    ("''.__class__.__mro__[1]", "attr.dunder"),
    # 10. DoS / bomb (caps)
    ("(" * 200 + "1" + ")" * 200, "bounds.nesting_too_deep"),          # paren depth bomb (AST-collapse safe)
    ("1" + "+1" * 400, "bounds.too_long"),                             # node-count bomb (length caps first)
    ("op('a')" + "".join("+op('a')" for _ in range(50)), "bounds.too_large"),  # call-count bomb
    ("9**9**9", "pow.nested"),                                         # integer-exponent host-hang (nested)
    ("(9**9)**9", "pow.nested"),                                       # left-nested '**' chain
    ("2 ** 99999999999", "pow.exponent"),                             # oversized literal exponent
    ("2 ** op('x')['n']", "pow.exponent"),                            # dynamic (non-literal) exponent
    ("pow(2, 99999999999)", "pow.exponent"),                          # E1: builtin pow giant-int (front door)
    ("pow(2, op('x')['n'])", "pow.exponent"),                         # E1: dynamic exponent via builtin pow
    ("'a' * 999999999", "mult.repeat_too_large"),                     # E2: literal string-repetition alloc bomb
    ("999999999 * 'ab'", "mult.repeat_too_large"),                    # E2: order-independent
    # RESIDUAL still open (disclosed, availability-class, plan §4.6): RUNTIME-operand string repetition
    # ("op('x')['s'] * op('x')['n']") or huge runtime int multiplication is NOT statically decidable, so it is
    # left as a known availability residual, NOT a corpus MUST_FAIL, to avoid banning legitimate scalar
    # arithmetic. The fully-LITERAL bomb above is now closed (E2).
    # 11. EXTRA adversarial cases against the allowlists
    ("me.digits @ me.digits", "node.disallowed"),                      # MatMult operator not allowed
    ("me.digits | 1", "node.disallowed"),                             # bitwise BitOr not allowed
    ("op('x', y=1)", "call.keywords_banned"),                          # keyword args banned
    ("op('x').cook()", "call.not_allowed"),                            # method call on a live object
    ("me.par.par.par.par.par", "attr.chain_too_deep"),                 # deep attribute chain
    ("me.owner", "attr.denied"),                                       # non-underscored TD escape attr
    ("me.foobar", "attr.not_allowed"),                                 # unlisted attribute
    ("os", "name.not_allowed"),                                        # bare disallowed name
    ("1" * 600, "bounds.too_long"),                                    # oversized source
    ("op('a')['x'] > 0 and [y for y in ()]", "node.disallowed"),       # comprehension buried in BoolOp
    ("chr(65)", "call.not_allowed"),                                   # unlisted builtin call
    ("''.join(['a'])", "call.not_allowed"),                            # str method reach
]


def _selftest():
    """Run the GREEN + RED corpora. Returns {passed, failed, details}. Standalone (pure ast); CI-wired.
    Contract identical to glsl_validator._selftest: a RED case that fails for a DIFFERENT-but-still-correct
    rule is counted as passed, with the mismatch recorded in details for review."""
    passed, failed, details = 0, 0, []
    for src in _MUST_PASS:
        try:
            validate_expr(src, "eval")
            passed += 1
        except ExprValidationError as e:
            failed += 1
            details.append({"kind": "must_pass_but_failed", "rule": getattr(e, "rule", None),
                            "src": src[:60]})
    for src, want_rule in _MUST_FAIL:
        try:
            validate_expr(src, "eval")
            failed += 1
            details.append({"kind": "must_fail_but_passed", "want": want_rule, "src": src[:60]})
        except ExprValidationError as e:
            got = getattr(e, "rule", None)
            if want_rule and got != want_rule:
                details.append({"kind": "rule_mismatch", "want": want_rule, "got": got, "src": src[:60]})
            passed += 1
    # profile/mode gate assertion: an unknown mode must reject.
    try:
        validate_expr(_MUST_PASS[0], "exec")
        failed += 1
        details.append({"kind": "mode_gate_leak", "src": "exec"})
    except ExprValidationError:
        passed += 1
    return {"passed": passed, "failed": failed, "details": details}


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(_selftest(), indent=2))
