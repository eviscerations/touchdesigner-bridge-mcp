"""Executable data-only-boundary audit: the Rust catalog (gateway/src/tools.rs) <-> the Python
executor _REGISTRY, adapted to TouchDesigner's GENERIC-ENGINE architecture. Makes the dispatch-integrity
invariant runnable OFFLINE (no TouchDesigner needed -- the executor reaches the scene only through
server.OP/ROOT/APP, which the tests' _tdmock binds, so importing the handlers works license-free).

    python scripts/audit_registry_consistency.py          # audit the SHIPPED surface (exit 0 = clean)

The gateway's tools.rs catalog is the AI-facing allowlist; the Python _REGISTRY is what an authed
loopback caller actually reaches. TD does NOT use per-operator Python handlers: the 509 OPERATOR tools
(optype = Some) LOWER in the gateway to create_op + set_par, so they need NO executor endpoint. Only the
UTILITY tools (optype = None) map 1:1 to an executor endpoint (except the gateway-native `batch`, which
the gateway answers itself). The invariants, all fail the build (exit 1) when violated:

  1. Every UTILITY tool has an executor endpoint (utility_targets - registry == empty)     -> broken tool
  2. No OPERATOR tool carries an executor endpoint (operator_names & registry == empty)     -> should lower
  3. No off-catalog endpoint is reachable that is not a lowering primitive or control-plane -> orphan
     (registry - utility_targets - LOWERING_PRIMITIVES - CONTROL_PLANE == empty)
  4. No endpoint name is RCE-shaped (reuse server._name_is_rce_shaped)                       -> boundary

This complements `cargo test` (Rust catalog self-consistency + the RCE/code-sink fences) -- together they
cover the AI-facing surface AND the loopback-reachable surface.
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# Utility tools answered INSIDE the Rust gateway itself (native), so they have NO Python executor
# endpoint by design. Kept in sync with the native dispatch in gateway/src/gateway.rs: `batch` has its
# own path in tools_call (run_batch); `capabilities` (orientation), `help` (local operator-help
# lookup), and `recipe_reference` (façade-content workflow recipes) are the GATEWAY_NATIVE set
# dispatched by call_one -> native.rs. All are exempt from the "every utility tool has an executor
# endpoint" invariant.
GATEWAY_NATIVE = {"batch", "td_capabilities", "help", "recipe_reference",
                  "glsl_reference", "expr_reference", "code_reference"}

# Executor endpoints the gateway uses to LOWER operator tools onto the generic engine. Reachable by
# design; create_op is lowering-only, set_par/connect are ALSO utility tools (harmless overlap).
LOWERING_PRIMITIVES = {"create_op", "set_par", "connect"}

# Endpoints intentionally present in _REGISTRY but deliberately OFF the tools.rs catalog: the control
# plane, reachable only by the authed loopback caller, never the AI/MCP path. TD's sole control-plane
# op is `dev_reload` (hot-reload the on-disk handler modules; NOT a request-code path -- it reimports
# the developer's files, never anything from the request, and re-asserts assert_no_rce_endpoints after).
# Unlike the Houdini bridge's `reload` (registered only under a dev flag), server.py registers
# `dev_reload` UNCONDITIONALLY, so it is always in the control plane here.
CONTROL_PLANE = {"dev_reload"}

# ---- populate the executor registry OFFLINE (the _tdmock bind pattern; no TouchDesigner needed) ----
from td_executor.tests import _tdmock  # noqa: E402
server, _scene = _tdmock.install()     # imports server + every handler module (registers @endpoint)

registry = set(server._REGISTRY)

# ---- parse tool names + optypes out of the generated Rust catalog ----
TOOLS_RS = os.path.join(REPO, "gateway", "src", "tools.rs")
with open(TOOLS_RS, encoding="utf-8") as fh:
    src = fh.read()

# Each ToolDef emits one line:  name: "X", category: "...", optype: (None | Some("Y")),
TOOL_RE = re.compile(
    r'name:\s*"([^"]+)",\s*category:\s*"[^"]*",\s*optype:\s*(?:None|Some\("([^"]+)"\))'
)
utility_names = set()
operator_names = set()
for m in TOOL_RE.finditer(src):
    name, optype = m.group(1), m.group(2)
    if optype is None:
        utility_names.add(name)
    else:
        operator_names.add(name)

if not utility_names or not operator_names:
    print("FAIL  could not parse tools.rs (utility=%d, operator=%d) -- regex drift?"
          % (len(utility_names), len(operator_names)))
    sys.exit(1)

# Utility tools that MUST have an executor endpoint (everything but the gateway-native ones).
utility_targets = utility_names - GATEWAY_NATIVE

# ---- the invariants ----
missing_handler = sorted(utility_targets - registry)
operator_with_handler = sorted(operator_names & registry)
orphans = sorted(registry - utility_targets - LOWERING_PRIMITIVES - CONTROL_PLANE)
banned = tuple(server._BANNED)
rce = sorted(n for n in registry if server._name_is_rce_shaped(n))

print("catalog utility tools     :", len(utility_names))
print("catalog operator tools    :", len(operator_names))
print("catalog tools (total)     :", len(utility_names) + len(operator_names))
print("python _REGISTRY endpoints:", len(registry))
print("gateway-native (no handler):", sorted(GATEWAY_NATIVE))
print("lowering primitives        :", sorted(LOWERING_PRIMITIVES))
print("control-plane off-catalog  :", sorted(CONTROL_PLANE))
print()

ok = True


def report(label, items, hint):
    global ok
    if items:
        ok = False
        print("FAIL  %s (%d): %s" % (label, len(items), ", ".join(items)))
        print("      -> %s" % hint)
    else:
        print("OK    %s" % label)


report("every utility tool has an executor endpoint", missing_handler,
       "a tools.rs utility tool has no @endpoint -> tools/call would fail; add the handler, remove the "
       "ToolDef, or (if native) add it to GATEWAY_NATIVE")
report("no operator tool carries an executor endpoint", operator_with_handler,
       "an operator tool (optype=Some) also has a Python @endpoint -- it must LOWER to create_op+set_par, "
       "not have its own handler; remove the handler")
report("no orphan executor endpoint", orphans,
       "an endpoint the loopback port exposes is neither a utility-tool target, a lowering primitive, nor "
       "a known control-plane op; surface it as a tool, add it to CONTROL_PLANE with justification, or "
       "remove it")
report("no RCE-shaped endpoint name", rce,
       "an endpoint name carries a banned token (%s) -> data-only boundary violation" % (banned,))

print("\nSUMMARY: utility=%d  operator=%d  endpoints=%d  orphans=%d"
      % (len(utility_names), len(operator_names), len(registry), len(orphans)))
print("RESULT :", "ALL PASS" if ok else "FAILURES ABOVE")
sys.exit(0 if ok else 1)
