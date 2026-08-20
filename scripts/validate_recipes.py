#!/usr/bin/env python3
"""Recipe-integrity gate for reference/recipes.json (TouchDesigner port of the Houdini bridge's
scripts/validate_recipes.py).

Recipes are the DRIVE LAYER that turns tool SURFACE into DRIVABLE capability: an agent calls
`recipe_reference(classify=…)` -> a routing row -> its `entry_recipe` -> the ordered steps. If a step
names a tool that no longer exists (renamed / never shipped), the agent routes into a dead end. This
gate keeps recipes honest against the SHIPPED surface:

  1. every recipe step `tool` is a real tool (a catalog operator, a utility tool, or a gateway-native)
  2. every `tool_manifest` entry is a real tool
  3. every routing `entry_recipe` is a real recipe id
  4. every `domain` used by a recipe is declared in `domains`
  5. (advisory) the leading tool token of a `verify.cheap` string resolves too
     (`milestone_check` is human-readable prose, not a tool invocation, so it is not checked)
  6. every `<gate>_opportunity` step object has the required shape (why/propose_via/consent) and any
     `gated_tool` resolves to a live tool

TD SPECIFICS vs the Houdini port: TouchDesigner's `reference/catalog.json` holds ONLY the 509 OPERATOR
tools (keyed by optype) — the 15 utility tools + `recipe_reference` live in the generated Rust surface
(gateway/src/tools.rs), NOT in catalog.json. So the tool UNIVERSE is built from BOTH: the operator
optypes from catalog.json + the utility/native tool names parsed out of tools.rs (the same parse the
registry audit uses). This makes the gate track exactly what the server ships.

Plain python (no TouchDesigner):  python scripts/validate_recipes.py   (exit 1 on any hard violation)
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RECIPES = REPO / "reference" / "recipes.json"
CATALOG = REPO / "reference" / "catalog.json"
TOOLS_RS = REPO / "gateway" / "src" / "tools.rs"

# Gateway-native tools (computed offline in the Rust gateway; no executor endpoint). Kept in sync with
# gateway/src/gateway.rs GATEWAY_NATIVE + scripts/audit_registry_consistency.py. They are real,
# callable tools and belong in the universe.
GATEWAY_NATIVE = {"td_capabilities", "help", "batch", "recipe_reference"}

# Recognised `<gate>_opportunity` step-key family for TouchDesigner. The bridge's gated, side-effectful
# capability is OUTPUT (bake a file / open a live send) — the WIRE-ONLY hand-off. `render` is kept for
# parity/extension (a render milestone an agent may want to flag). `glsl`/`expr` are the CODE-handoff
# cues: a step where a GLSL shader or a parameter expression is the real tool, surfaced for the consented
# validated lane (set_glsl/set_expr) or paste-by-hand via glsl_reference/expr_reference. Each opportunity
# object requires why/propose_via/consent; an optional gated_tool must resolve to a live tool.
_OPP_GATES = {"output", "render", "glsl", "expr"}


def parse_tools_rs():
    """Utility (optype=None) + operator (optype=Some) tool names from the generated Rust catalog —
    the same regex the registry audit uses. Returns (utility_names, operator_names)."""
    src = TOOLS_RS.read_text(encoding="utf-8")
    tool_re = re.compile(
        r'name:\s*"([^"]+)",\s*category:\s*"[^"]*",\s*optype:\s*(?:None|Some\("([^"]+)"\))'
    )
    utility, operator = set(), set()
    for m in tool_re.finditer(src):
        name, optype = m.group(1), m.group(2)
        (operator if optype else utility).add(name)
    return utility, operator


def main():
    rec = json.loads(RECIPES.read_text(encoding="utf-8"))
    catalog_ops = set(json.loads(CATALOG.read_text(encoding="utf-8")).keys())  # 509 operator optypes
    utility_names, operator_names_rs = parse_tools_rs()

    if not utility_names or not operator_names_rs:
        print("FAIL  could not parse tools.rs (utility=%d, operator=%d) — regex drift?"
              % (len(utility_names), len(operator_names_rs)))
        return 1

    # The full shipped surface: operator optypes (catalog.json is the ground truth) + the utility/
    # native tools from tools.rs + the gateway-native set (belt-and-suspenders; all also in tools.rs).
    universe = catalog_ops | utility_names | GATEWAY_NATIVE
    # `catalog` (operators only) mirrors the Houdini script's variable used by the advisory check.
    catalog = catalog_ops

    recipes = rec.get("recipes", [])
    routing = rec.get("routing", [])
    domains = set(rec.get("domains", []))
    ids = {r["id"] for r in recipes}

    hard = []      # (recipe/route, kind, offending)
    advisory = []

    tool_token = re.compile(r"[a-z][a-z0-9_]+")

    for r in recipes:
        rid = r.get("id", "<no-id>")
        if r.get("domain") and r["domain"] not in domains:
            hard.append((rid, "undeclared domain", r["domain"]))
        for i, step in enumerate(r.get("steps", [])):
            t = step.get("tool")
            # a parenthesized "(…)" tool is a deliberate META-step (agent vision, dispatch,
            # cross-recipe reference), not a real tool — the established convention.
            if t and t.startswith("("):
                continue
            if t and t not in universe:
                hard.append((rid, f"step[{i}] tool", t))
            # advisory: leading tool token of the machine-checkable `cheap` string.
            # `milestone_check` is prose (a human-readable success signal), so its first
            # word is not a tool name and must not be validated as one.
            v = step.get("verify", {}) or {}
            for key in ("cheap",):
                s = v.get(key)
                if not s:
                    continue
                head = s.split("@")[0].split("(")[0].strip()
                tok = head.split()[0] if head.split() else ""
                if tok and tok in catalog:
                    pass
                elif tok and tok not in universe and tool_token.fullmatch(tok):
                    advisory.append((rid, f"step[{i}] verify.{key} tool", tok))
            # opportunity-family signals: validate the shape + that any gated_tool is a live tool.
            for okey in [k for k in step.keys() if k.endswith("_opportunity")]:
                gate = okey[: -len("_opportunity")]
                if gate not in _OPP_GATES:
                    hard.append((rid, f"step[{i}] unknown opportunity kind", okey))
                opp = step.get(okey)
                if not isinstance(opp, dict):
                    hard.append((rid, f"step[{i}] {okey} not an object", type(opp).__name__))
                    continue
                for req in ("why", "propose_via", "consent"):
                    if not opp.get(req):
                        hard.append((rid, f"step[{i}] {okey} missing '{req}'", okey))
                gt = opp.get("gated_tool")
                if gt and gt not in universe:
                    hard.append((rid, f"step[{i}] {okey}.gated_tool", gt))
        for t in r.get("tool_manifest", []):
            if t not in universe:
                hard.append((rid, "tool_manifest", t))

    for row in routing:
        er = row.get("entry_recipe")
        if er and er not in ids:
            hard.append((row.get("input_element", "<route>")[:40], "entry_recipe", er))

    print(f"recipes: {len(recipes)} | routing: {len(routing)} | domains: {len(domains)} | "
          f"catalog operators: {len(catalog_ops)} | utility tools: {len(utility_names)} | "
          f"universe: {len(universe)}")
    # advisory verify-string tokens: skip common English verbs that aren't tools
    _VERBS = {"confirm", "open", "list", "check", "verify", "ensure", "read", "see", "run", "tap"}
    advisory = [(w, k, o) for (w, k, o) in advisory if o not in _VERBS]
    if advisory:
        print(f"\nADVISORY ({len(advisory)}) — tool named in a verify string not in the surface:")
        for who, kind, off in advisory:
            print(f"  ~ {who}: {kind} = {off!r}")
    if hard:
        print(f"\nHARD VIOLATIONS ({len(hard)}):")
        for who, kind, off in hard:
            print(f"  [X] {who}: {kind} = {off!r}")
        print("\nRESULT: FAIL")
        return 1
    print("\nRESULT: ALL PASS — every recipe/route references a real tool + recipe.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
