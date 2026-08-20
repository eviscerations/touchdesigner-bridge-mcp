# Testing method

The standardized, repeatable way touchdesigner-bridge-mcp is tested.

The constraint that shapes everything: **the whole automated suite runs without TouchDesigner and
without a license.** TouchDesigner ships no `pip`-importable module, so — unlike a DCC that you would
`import` — the executor reaches the scene only through `server.OP(...)`, `server.ROOT`, `server.APP`,
which the in-TD callbacks DAT binds at arm time. That indirection is what makes the tests license-free:
a small fake scene (`td_executor/tests/_tdmock.py`) is handed to `server.bind()`, and every handler runs
against it. The honesty rule is absolute: a test's headline says exactly what it proves and nothing
more. "The handler's Python runs against a mock" and "the catalog is internally consistent" are **not**
"produces correct output in a running TouchDesigner."

## The layers

### Executor unit + logic suite (offline, TD mock) — the breadth

Proves every executor handler's Python runs end to end against the recording mock — node resolved, parms
set, inputs wired, a JSON-serializable return assembled — and that the security guards (confinement,
code-sink denial, the expr/GLSL validators, the DoS ceiling, the integrity gate) hold. Pure CPython, no
third-party packages, no TouchDesigner.

```sh
python td_executor/tests/run_tests.py
```

**282 tests.** Equivalent invocation: `python -m unittest discover -t . -s td_executor/tests -p "test_*.py"`.
The mock (`_tdmock.py`) is deliberately not catch-all truthy: `op.par` raises `AttributeError` for an
unknown parameter and the op has no `__getattr__`, so a genuine wrong-attribute bug in a handler still
surfaces. It also records every parameter-expression write to `EXPR_WRITES`, which must stay empty — the
runtime proof that `set_par` sets values only, never expressions. Notable areas: `test_confined_path.py`
and `test_boundary.py` (write confinement), `test_code_sink_guard.py` (the deny-list guard, cross-checked
against the gateway fence so the two cannot drift), `test_expr_validator.py` / `test_glsl_validator.py`
(the validated code lanes), `test_governor.py` / `test_dos_ceiling.py` (the build-to-a-budget governor),
and `test_integrity.py` (the hash-pin manifest).

### Gateway catalog fence (Rust) — the data-only boundary

Proves the AI-facing tool catalog is internally consistent and can never expose a code-execution path.
This is the strongest claim a reviewer can independently re-run in seconds.

```sh
cargo test --manifest-path gateway/Cargo.toml
```

**65 tests.** Among them: `catalog_names_are_unique` and `batch_is_in_catalog_and_unique` (no duplicate
tools), `catalog_never_exposes_rce_tools` and `code_named_params_are_the_known_reviewed_set` (no
code-carrying tool or unreviewed code-named param reaches the surface),
`glsl_ops_expose_shader_source_only_as_nodepath_never_str` (shader source is a node reference, never
inline string), the `confine_*` cases (`confine_rejects_junction_escaping_root`,
`confine_rejects_parent_dir_traversal_in_tail`, `confine_rejects_absolute_outside` — the crux Windows
traversal vectors), the `validate_*` / `oplist_*` / `parmap_*` schema-validation and batch-shape cases,
and the `recipe_reference` / `help` / `code_reference` native-utility checks. Building the release binary
(`cargo build --release --manifest-path gateway/Cargo.toml`) is also part of the gate.

### Consistency audits (offline) — surface, recipes, and integrity

Three plain-Python gates that keep the shipped surface honest against itself. None needs TouchDesigner.

```sh
python scripts/audit_registry_consistency.py
python scripts/validate_recipes.py
python scripts/gen_integrity_manifest.py --check
```

- **Registry consistency** — the Rust catalog (`gateway/src/tools.rs`) versus the Python executor
  `_REGISTRY`. Because TouchDesigner uses a generic engine, the operator tools (optype set) lower to
  `create_op` + `set_par` and need no executor endpoint; only the utility tools map 1:1 to an endpoint
  (except the gateway-native ones). It asserts every utility tool has an endpoint, no operator tool
  carries one, there are no orphan endpoints beyond the lowering primitives and the `dev_reload` control
  plane, and no endpoint name is RCE-shaped. It populates the registry via the same `_tdmock` bind, so it
  runs license-free.
- **Recipe integrity** — every step, `tool_manifest` entry, and routing `entry_recipe` in
  `reference/recipes.json` resolves to a real, shipped tool or recipe (the tool universe = the operator
  optypes in `reference/catalog.json` + the utility/native tools parsed from `tools.rs`), so an agent
  following a recipe never routes into a dead end.
- **Integrity manifest (`--check`)** — fails if `td_executor/INTEGRITY.json` is stale relative to the
  on-disk executor code (see below).

### The aggregate gate

One entrypoint runs every layer above in sequence and prints a PASS/FAIL summary. Every check runs even
if an earlier one fails, and the process exits nonzero if any failed.

```sh
python scripts/check_all.py            # run everything
python scripts/check_all.py --no-rust  # skip the cargo test + release build
```

It runs, in order: the Rust tests, the Rust release build, the Python executor suite, the registry audit,
the recipe gate, and the integrity-manifest `--check`.

## What CI enforces today

Two jobs run on every push and pull request (both on `windows-latest`, because the governor uses Windows
APIs and must exercise the real code paths):

- **Rust (gateway)** — `cargo test` + `cargo build --release`.
- **Python (executor + gates)** — the executor suite, the registry audit, the recipe gate, and the
  integrity-manifest `--check`.

This mirrors `scripts/check_all.py`; run that locally before opening a change and CI holds no surprises.

## What these tests do NOT prove

Everything above is **unit / logic + catalog-fence** testing. It proves the tool surface is consistent
and data-only, the handlers' Python runs against a mock scene, and the guards hold. It does **not** cook
anything in a running TouchDesigner, so it does not prove that a built network renders, that an image
saves, or that a projection maps correctly. **Live verification against a licensed, running TouchDesigner
is a separate, manual step** — arm the executor and drive real networks — and is never a substitute for,
nor substituted by, the automated suite.

## When to regenerate the integrity manifest

`td_executor/INTEGRITY.json` hash-pins every executor `.py` file (`server.py`, `governor.py`, the
validators, and every handler). Arming and `dev_reload` **verify before import** and fail closed on any
mismatch or unpinned handler. So **after any edit under `td_executor/`** (including adding a handler
module), regenerate the manifest or the next arm/reload will refuse to load:

```sh
python scripts/gen_integrity_manifest.py
```

The digest diff is meant to be reviewed — the manifest update is an intentional, reviewed act.

## Adding coverage — the standard loop

1. New operator tool: the registry and recipe audits pick it up automatically (they are catalog-derived).
   Confirm they stay green.
2. New utility tool / executor handler: add a `test_*.py` under `td_executor/tests/` that drives the
   handler against `_tdmock`, and ensure the registry audit still passes (add a gateway-native or
   control-plane exemption only with justification, never to force green).
3. A real bug found while driving live: write a test that reproduces the failing condition first, then
   fix, so it can never silently return.
4. Never headline a test as proving more than it does. The suite proves consistency and handler Python;
   correctness in a running TouchDesigner is proven live.
