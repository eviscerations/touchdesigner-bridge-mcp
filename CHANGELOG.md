# Changelog

All notable changes to TouchDesigner Bridge MCP are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — Unreleased

Initial release of the data-only TouchDesigner control surface: a Rust MCP gateway plus an
in-TouchDesigner Python executor, modeled on the author's Houdini bridge.

### Added

- **Two-process bridge.** A Rust gateway (`gateway/src/`) serving the MCP tool surface over
  stdio JSON-RPC 2.0, relaying to a Python executor (`td_executor/`) armed inside
  TouchDesigner over loopback HTTP (`127.0.0.1:9980`).
- **Typed tool surface — 544 tools.** 509 create-and-configure operator tools (one per
  TouchDesigner operator type, generated from the live-probed `reference/catalog.json`) plus
  35 utility tools (`scene_info`, `read_network`, `connect`, `set_par`, `set_par_many`,
  `operator_reference`, `recipe_reference`, `save_top`, `capture_ui`, `write_csv`,
  `find_errors`, `inspect`, `top_info`, `import_scan`, `import_segmented_model`, `batch`, …).
- **New operator tools (+14).** Camera trackers (`freedinCHOP`, `stypeinCHOP`, `mosysCHOP`,
  `ncamCHOP`), disguise RenderStream in/out (`renderstreaminTOP`, `renderstreamoutTOP`), depth
  sensors (`orbbecTOP`, `orbbecselectTOP`, `realsenseTOP`, `kinectazureTOP`, `kinectazureCHOP`),
  plus `tileTOP`, `pointtransformTOP`, and multi-machine `syncinCHOP` — taking the catalog to
  509 operators / 544 tools.
- **Consent-gated `device_send` tool (default-off).** Sends a command to a projector over a
  closed PJLink Class-1 allowlist via a Text TCP/IP DAT; off unless a human explicitly enables
  `allow_device_control`, and refused with no bytes on the wire otherwise.
- **Generic create+set_par engine.** Every operator tool lowers to `create_op` + `set_par`
  in the executor, so the whole surface funnels through one validated choke point.
- **Recipe / reference layer.** Offline `help` and `recipe_reference` (66 tool-mapped
  workflow recipes across render / texture / projection-mapping / camera / output /
  choreography / show-control / media / interactive) plus `operator_reference`, all reading bundled, original data.
- **Learning / code-teaching pathway.** Read-only `glsl_reference`, `expr_reference`, and
  `code_reference` tools (backed by bundled `reference/*_REFERENCE.md` guides) teach TD's GLSL
  and parameter-expression surfaces and the human-gated consent handshake; recipe steps carry
  `glsl_opportunity` / `expr_opportunity` cues, and the README documents honest per-domain
  coverage. The tools propose code text for the validated lanes or paste-by-hand; they never run code.
- **Validated code lanes (default-off).** `set_glsl` / `validate_glsl` and `set_expr` /
  `validate_expr` — consent-gated (`allow_glsl` / `allow_expr`), allowlist-first validators.
- **Documentation suite.** GUIDE, HOWTO, RUNBOOK, TROUBLESHOOTING, TESTING, INSTALL, plus the
  full TOOL_CATALOG.
- **Advisory magnitude governor** (`governor.py`) that flags heavy realtime-GPU requests
  (resolution, instance/particle counts, render passes) and honors the non-commercial 1280
  output cap, classifying on system RAM (TouchDesigner's API exposes no whole-card VRAM).
- **Dual license.** Free for noncommercial use (PolyForm Noncommercial 1.0.0); commercial
  use requires a separate paid license.

### Security

- **No arbitrary-code path.** No `exec` / `eval` / `run` / `node_op` / shell tool exists.
  Enforced by the runtime canary `assert_no_rce_endpoints()`, the runtime
  `check_optype_allowed()` (denies `script` / `execute` / `cplusplus` optypes and
  `evaluateDAT`), and the build-time fence `catalog_never_exposes_rce_tools`.
- **Layered parameter guard** (`check_par_allowed`): universal deny of code-pointer
  parameter names (`callbacks` / `*script` / `datexpr`), a reviewed inline code-sink denylist
  with Sequence-block-index regex generalization, and a **fail-closed allowlist** so
  unknown/newer parameters are refused instead of waved through. Cross-checked between the
  executor and the gateway build-time fences by `test_code_sink_guard.py`.
- **Loopback + auto-minted token.** Web Server DAT binds `127.0.0.1` only; arming mints a
  128-bit CSPRNG token to `~/.touchdesigner-bridge-mcp/arm.json`, presented as
  `X-TDMCP-Token` and compared with `secrets.compare_digest`. Cross-origin (non-loopback
  `Origin`/`Host`) requests are refused on every endpoint, including the auth-exempt
  `health` / `validate_*` ones (closes the DNS-rebind class, F-AUTH-1). `arm.json` ACLs are
  best-effort restricted to the user on arming (F-AUTH-2).
- **Working-directory confinement.** All file operations `realpath`-confine to one working
  directory, read fresh from `arm.json` by both the gateway and the executor. The config dir
  and the executor trust root (`td_executor/*.py`, `INTEGRITY.json`, `arm.py`) are off-limits
  even inside the working dir (F-TRUST-1); write tools enforce extension whitelists.
- **Integrity pinning** (`INTEGRITY.json`) with **verify-before-import** in `arm.py`
  (F-INTEG-1): a self-contained stdlib bootstrap hashes every executor file before the
  package is imported and fails closed on any mismatch or unpinned handler; `dev_reload`
  re-verifies too. Honest ceiling documented: tamper-evidence, not a boundary against an
  attacker who can write the install dir — OS file permissions are the root of trust.
- **Two validated code lanes, default-off and consent-gated.** `glsl_v1`
  (`set_glsl`/`validate_glsl`, GPU-sandboxed) and `expr_v1` (`set_expr`/`validate_expr`,
  AST positive-allowlist, shipped EXPERIMENTAL). Both validate before write, are
  executor-authoritative, audited, single-write-path, and off unless `allow_glsl` /
  `allow_expr` is set in `arm.json`. The AI cannot flip its own consent: the `/mcp_bridge`
  component and its GUI consent toggles are refused by `assert_writable` on every mutating
  tool, and the flags live in the off-limits config dir. A bare re-arm preserves consent.
- **Enforced magnitude ceiling (F-DOS-1).** A hard refuse in `governor.py` for
  catastrophic, driver-killing magnitudes (per-dimension resolution > 16384 px,
  instance/particle counts > 5,000,000, render passes > 256), overridable only by the
  human-gated `allow_highres` flag — so legitimate 4K/8K delivery passes but a runaway
  `set_par` cannot hang the display driver.
- **Red-team audited.** An independent adversarial code review found no working
  RCE bypass; its additive findings (F-AUTH-1/2, F-TRUST-1, F-INTEG-1, F-DOS-1, F-EXEC-1
  fail-closed allowlist) are addressed above. The residual is documented honestly in
  [SECURITY.md](SECURITY.md): the parameter guard is a denylist over a closed-source
  ~17k-parameter surface and cannot be proven complete.

### Changed

- **De-hardcoded all developer paths.** No absolute developer-path literal remains in shipped
  code or docs. The gateway resolves the working dir from an OS-relative default /
  env / `arm.json`; the executor derives its repo location from the injected `TDMCP_REPO`
  (falling back to the process cwd); the arm command is generated at runtime by the GUI and
  documented with a `<path-to-your-clone>` placeholder.
- **Reference data reads from the code-relative base**, not the confinement working dir, so
  `catalog.json` / `recipes.json` resolve identically wherever the working dir points and the
  security-critical catalog cannot be corrupted by a confined write.

### Notes

- Windows only; requires a licensed TouchDesigner install. The pipeline targets an AMD GPU;
  NVIDIA/CUDA-only operators are intentionally out of scope (untestable on the target rig).

[0.1.0]: https://keepachangelog.com/en/1.1.0/
