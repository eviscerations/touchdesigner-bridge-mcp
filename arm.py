# ARM THE TD BRIDGE -- assembles /mcp_bridge (Web Server DAT + thin callbacks DAT that loads the
# td_executor package from disk) and starts it on loopback. Copy the exact Textport line from the
# Rust GUI's "Working dir" pane (it injects TDMCP_REPO = this repo's location, derived from the running
# binary -- so NO developer path is committed here). It looks like:
#     import os; os.environ['TDMCP_REPO']=r'<repo>'; exec(open(os.path.join(os.environ['TDMCP_REPO'],'arm.py')).read())
# Re-run any time to HOT-RELOAD on-disk edits to td_executor/*.py (it purges the module cache first).
# Remove with:  op('/mcp_bridge').destroy()
import sys
import os
import json
import secrets

# Repo / code location. NO hardcoded path committed here: the GUI-generated arming command injects
# TDMCP_REPO (derived at runtime from the binary's location); we fall back to the cwd only if it is
# unset. Used to put td_executor on sys.path and to find the trust root -- it is NOT the confinement
# working dir (that is resolved separately below and lives outside the source tree).
PROJECT = os.path.realpath(os.environ.get('TDMCP_REPO') or os.getcwd())
PORT = 9980

# --- auth token: DEFAULT-ON (P0.3). Auto-mint a CSPRNG token unless one is supplied via env; the user
#     never types or sees it -- it is exchanged with the gateway via arm.json. This neutralizes the
#     loopback-CSRF / DNS-rebinding class (a malicious web page POSTing to 127.0.0.1:9980) for ~0 UX cost.
TOKEN = os.environ.get('TDMCP_TOKEN') or secrets.token_hex(16)

# --- dev hot-reload: drop cached td_executor modules so on-disk edits are picked up on re-run ---
for _m in [m for m in list(sys.modules) if m == 'td_executor' or m.startswith('td_executor.')]:
    del sys.modules[_m]
if PROJECT not in sys.path:
    sys.path.append(PROJECT)


# --- VERIFY BEFORE IMPORT. Hash the on-disk executor files against td_executor/INTEGRITY.json
#     with a SELF-CONTAINED bootstrap (stdlib only; imports NOTHING from td_executor) BEFORE the package is
#     imported, so a tampered server.py is caught before its module body can run. This closes the import-
#     before-verify hole: the manifest cannot protect the file that HOSTS its own verifier, so the check
#     that guards the verifier must not live inside the code it verifies.
#     HONEST CEILING (do not overstate this): INTEGRITY.json is tamper-EVIDENCE / defense-in-depth, NOT a
#     boundary against an attacker who can already WRITE the install directory -- such an attacker can
#     rewrite the files AND the manifest together (or pass TDMCP_INTEGRITY=0). The real root of trust is the
#     OS file permissions on the install dir; this bootstrap raises the bar (a tampered verifier can no
#     longer simply return success) but is not a substitute for those permissions. ---
def _preverify_executor(pkg_dir):
    import hashlib as _hashlib
    import glob as _glob
    if os.environ.get('TDMCP_INTEGRITY', '1') == '0':
        print('[td-bridge] WARNING: integrity pre-check BYPASSED (TDMCP_INTEGRITY=0)')
        return
    with open(os.path.join(pkg_dir, 'INTEGRITY.json'), 'r', encoding='utf-8') as _fh:
        _expected = (json.load(_fh) or {}).get('files', {})
    if not _expected:
        raise RuntimeError('INTEGRITY.json has no pinned files')
    # set-equality over handlers/*.py: an UNPINNED handler .py on disk is tamper (a smuggled module).
    _on_disk = set(_expected)
    for _p in _glob.glob(os.path.join(pkg_dir, 'handlers', '*.py')):
        _on_disk.add(os.path.relpath(_p, pkg_dir).replace(os.sep, '/'))
    _extra = sorted(_on_disk - set(_expected))
    if _extra:
        raise RuntimeError('handler set mismatch -- unpinned file(s) on disk: %s' % _extra)
    _bad = []
    for _rel, _want in sorted(_expected.items()):
        _ap = os.path.join(pkg_dir, _rel.replace('/', os.sep))
        _h = _hashlib.sha256()
        try:
            with open(_ap, 'rb') as _f:
                for _chunk in iter(lambda: _f.read(65536), b''):
                    _h.update(_chunk)
        except OSError as _e:
            _bad.append((_rel, 'unreadable: %s' % _e)); continue
        if _h.hexdigest() != str(_want):
            _bad.append((_rel, 'digest mismatch'))
    if _bad:
        raise RuntimeError('integrity pre-check FAILED (refusing to arm): %s' % _bad)
    print('[td-bridge] integrity pre-check OK (%d files verified BEFORE import)' % len(_expected))

try:
    _preverify_executor(os.path.join(PROJECT, 'td_executor'))
except Exception as _pe:
    print('[td-bridge] *** INTEGRITY PRE-CHECK FAILED: %s' % _pe)
    print('[td-bridge] *** refusing to arm (fail-closed, BEFORE importing td_executor). Regenerate '
          'td_executor/INTEGRITY.json (python scripts/gen_integrity_manifest.py) or set TDMCP_INTEGRITY=0 '
          'for local dev.')
    raise

from td_executor import server

# --- integrity (defense in depth): the package's own verifier re-checks post-import. The pre-check above
#     is the primary gate (it runs before this module's body); this second pass keeps parity with dev_reload
#     and catches a manifest/file race between the two reads. ---
try:
    _iv = server.verify_integrity()
    print('[td-bridge] integrity: %s' % _iv)
except server.IntegrityError as _e:
    print('[td-bridge] *** INTEGRITY CHECK FAILED: %s' % _e)
    print('[td-bridge] *** refusing to arm (fail-closed). Regenerate td_executor/INTEGRITY.json '
          '(python scripts/gen_integrity_manifest.py) or set TDMCP_INTEGRITY=0 for local dev.')
    raise

import td_executor.handlers  # populates server._REGISTRY via @endpoint
server.assert_no_rce_endpoints()   # fail-closed data-only guard

# --- enable auth on the executor BEFORE the webserverDAT is created, and write the single source of
#     truth (arm.json) that the gateway reads to send X-TDMCP-Token. ---
server.TOKEN = TOKEN
_CFG_DIR = os.path.join(os.path.expanduser('~'), '.touchdesigner-bridge-mcp')
_ARM_JSON = os.path.join(_CFG_DIR, 'arm.json')
# PRESERVE consent flags across re-arms. arm.py used to overwrite arm.json with only token/port/working_dir,
# so any allow_expr/allow_glsl set by hand (or by the GUI toggle below) was WIPED on the next re-arm. Read the
# prior arm.json first and carry the consent forward -- a bare re-arm must never silently disable a lane.
_prior_cfg = {}
try:
    with open(_ARM_JSON, 'r', encoding='utf-8') as _pf:
        _prior_cfg = json.load(_pf) or {}
except Exception:
    _prior_cfg = {}
_allow_expr = bool(_prior_cfg.get('allow_expr', False))
_allow_glsl = bool(_prior_cfg.get('allow_glsl', False))
# projector device-control consent (default OFF): gates `device_send` -- the ONLY outbound .send() path, a
# CLOSED PJLink Class-1 command allowlist. Preserved across re-arms like the code-lane consents.
_allow_device_control = bool(_prior_cfg.get('allow_device_control', False))
# high-res override (default OFF): when true, the enforced magnitude ceiling (resolution/instance/pass hard
# cap that stops a driver-killing render) is bypassed. Preserved across re-arms like the code-lane consents.
_allow_highres = bool(_prior_cfg.get('allow_highres', False))
# CONFINEMENT WORKING DIR (what tools may read/write). Single source of truth = arm.json's working_dir,
# set by the Rust GUI's "Working dir" Apply. Precedence here: explicit env override > the PRIOR arm.json
# value (so a GUI / manual choice STICKS across re-arms -- a bare re-arm must never silently reset the
# jail) > a per-user default under Documents. It is deliberately NOT the repo/code dir -- the jail stays
# separate from the source tree (Houdini-MCP parity). The executor reads this fresh per call via
# server.working_dir(); server.WORKING_DIR below is only the fallback if arm.json becomes unreadable.
_default_wd = os.path.join(os.path.expanduser('~'), 'Documents', 'touchdesigner-bridge-mcp')
_working_dir = os.path.realpath(
    os.environ.get('TDMCP_WORKING_DIR') or _prior_cfg.get('working_dir') or _default_wd)
try:
    os.makedirs(_working_dir, exist_ok=True)
except Exception:
    pass
server.WORKING_DIR = _working_dir
try:
    os.makedirs(_CFG_DIR, exist_ok=True)
    with open(_ARM_JSON, 'w', encoding='utf-8') as _fh:
        json.dump({'token': TOKEN, 'port': PORT, 'working_dir': _working_dir,
                   'allow_expr': _allow_expr, 'allow_glsl': _allow_glsl,
                   'allow_highres': _allow_highres,
                   'allow_device_control': _allow_device_control}, _fh, indent=2)
    print('[td-bridge] auth ENABLED; token written to %s (working_dir=%s, consent: allow_expr=%s, '
          'allow_glsl=%s, allow_highres=%s, allow_device_control=%s)'
          % (_ARM_JSON, _working_dir, _allow_expr, _allow_glsl, _allow_highres, _allow_device_control))
    # Tighten ACLs so ONLY this user can read the plaintext token (grant the user first, THEN
    # strip inheritance, so we never lock ourselves out). Best-effort -- never blocks arming.
    try:
        import subprocess as _sp
        _me = os.environ.get('USERNAME')
        if _me:
            for _p in (_CFG_DIR, _ARM_JSON):
                _sp.run(['icacls', _p, '/grant:r', '%s:F' % _me, '/inheritance:r'],
                        capture_output=True, check=False)
    except Exception as _e2:
        print('[td-bridge] note: could not tighten arm.json ACLs: %s' % _e2)
except Exception as _e:
    print('[td-bridge] WARNING: could not write %s: %s (gateway may 403 until token is exchanged)'
          % (_ARM_JSON, _e))

print('[td-bridge] loaded %d executor verbs (the LOW-LEVEL primitives; the gateway exposes ~509 typed '
      'operator tools + 35 utility tools ON TOP of these -- ~544 AI-facing tools total): %s'
      % (len(server._REGISTRY), ', '.join(sorted(server._REGISTRY))))

# --- thin callbacks DAT: binds TD globals + delegates to the on-disk executor ---
CB_CODE = r"""from td_executor import server
import td_executor.handlers  # ensure registry populated in this interpreter
def onHTTPRequest(webServerDAT, request, response):
    server.bind(op=op, root=root, app=app)
    return server.handle(webServerDAT, request, response)
def onServerStart(webServerDAT): return
def onServerStop(webServerDAT): return
"""

# --- (re)assemble the bridge component (also clear the old spike so port 9980 is free) ---
for _old in ('mcp_bridge', 'mcp_spike'):
    _ex = root.op(_old)
    if _ex is not None:
        _ex.destroy()

bridge = root.create('baseCOMP', 'mcp_bridge')
cb = bridge.create('textDAT', 'callbacks')
cb.text = CB_CODE
web = bridge.create('webserverDAT', 'webserver')
web.par.callbacks = cb.name
web.par.localaddress = '127.0.0.1'   # loopback ONLY -- the transport boundary (no firewall rule needed)
web.par.port = PORT
web.par.active = 1

# --- GUI CONSENT TOGGLES (Houdini parity): Allow Expr / Allow GLSL toggles on the bridge COMP that PERSIST to
#     arm.json (the executor's single consent source, read fresh per call). A Parameter Execute DAT writes
#     arm.json whenever a toggle changes -- flip it in the GUI, no hand-edit and no re-arm. Initialized from the
#     preserved arm.json values above. The /mcp_bridge subtree is off-limits to MCP mutation
#     (server.assert_writable), so the AI can NEVER flip its own consent -- only a human at the GUI (or this
#     bootstrap) can. Fully defensive: any TD-API hiccup here is logged and skipped so it can never break arming
#     (consent still works via arm.json). API verified vs docs.derivative.ca: COMP.appendCustomPage -> Page,
#     Page.appendToggle -> ParGroup ([0]=the Par); parameterexecuteDAT op/pars/valuechange; onValueChange(par,
#     val, prev); par.owner = the COMP. Values are set BEFORE the parexec exists so they don't fire a spurious write.
try:
    _page = bridge.appendCustomPage('MCP')
    _pg_expr = _page.appendToggle('Allowexpr', label='Allow Expr Lane')
    _pg_glsl = _page.appendToggle('Allowglsl', label='Allow GLSL Lane')
    _pg_dev = _page.appendToggle('Allowdevicecontrol', label='Allow Device Control (projector)')
    _pg_expr[0].val = bool(_allow_expr)
    _pg_glsl[0].val = bool(_allow_glsl)
    _pg_dev[0].val = bool(_allow_device_control)
    _old_sync = bridge.op('consent_sync')
    if _old_sync:
        _old_sync.destroy()
    _sync = bridge.create('parameterexecuteDAT', 'consent_sync')
    _sync.par.active = False                 # inert while configuring
    _sync.par.op = bridge                    # monitor the bridge COMP's pars (must be pointed explicitly)
    _sync.par.pars = 'Allowexpr Allowglsl Allowdevicecontrol'
    _sync.par.valuechange = True
    _sync.text = (
        "import json, os\n"
        "def onValueChange(par, val, prev):\n"
        "    if par.name not in ('Allowexpr', 'Allowglsl', 'Allowdevicecontrol'):\n"
        "        return\n"
        "    cfg = os.path.join(os.path.expanduser('~'), '.touchdesigner-bridge-mcp', 'arm.json')\n"
        "    try:\n"
        "        with open(cfg, 'r', encoding='utf-8') as f: data = json.load(f)\n"
        "    except Exception:\n"
        "        data = {}\n"
        "    b = par.owner\n"
        "    try:\n"
        "        data['allow_expr'] = bool(b.par.Allowexpr.eval())\n"
        "        data['allow_glsl'] = bool(b.par.Allowglsl.eval())\n"
        "        data['allow_device_control'] = bool(b.par.Allowdevicecontrol.eval())\n"
        "        os.makedirs(os.path.dirname(cfg), exist_ok=True)\n"
        "        with open(cfg, 'w', encoding='utf-8') as f: json.dump(data, f, indent=2)\n"
        "    except Exception:\n"
        "        pass\n"
        "    return\n"
    )
    _sync.par.active = True                   # armed: either toggle now writes to arm.json
    print('[td-bridge] GUI consent toggles ready on /mcp_bridge (Allow Expr Lane / Allow GLSL Lane / '
          'Allow Device Control) -> persist to arm.json (flip in the GUI, no re-arm)')
except Exception as _te:
    print('[td-bridge] note: GUI consent toggle setup skipped (%s); consent still works via arm.json' % _te)

print('[td-bridge] armed at %s  ->  http://127.0.0.1:%d/health' % (bridge.path, PORT))
print('[td-bridge] remove with: op("/mcp_bridge").destroy()')
