"""td_executor/governor.py -- advisory scale/VRAM governor (ADVISORY-first telemetry, one hard-refuse).

The parity port of the Houdini bridge's governor.py (houdini_executor/governor.py), adapted to a
REALTIME-GPU app. Heavy-output requests SURFACE an advisory magnitude flag (and, where telemetry is
honest, a live resource envelope) so the watching AI/human governs to the deliverable's budget; ONLY a
genuinely catastrophic band hard-refuses (honoring "never OOM/BSOD the display driver").

TELEMETRY HONESTY (the decisive TD limit — DOCUMENTED, not faked):
  TouchDesigner's documented Python API exposes GPU memory only PER-TOP (`TOP.gpuMemory`, surfaced by
  top_info as gpu_memory_bytes) and `SysInfo.ram` (available system RAM) + `SysInfo.GPUName`. It exposes
  NO whole-card total/used/available VRAM. So unlike the Houdini governor (which had a PDH per-adapter
  VRAM query), the TD envelope classifies on SYSTEM RAM and marks VRAM headroom UNKNOWN, and the magnitude
  advisory (PURE, param-only, offline) carries the realtime-GPU scale signal instead. On this rig VRAM
  (12 GB AMD RX 6700 XT) is the tight resource, so the magnitude flags are tuned to realtime-GPU cost
  (resolution, instance/particle counts, render passes) and honor the non-commercial 1280 output cap.

Design rules (audited, mirrors Houdini):
  * `classify_band` is PURE -- numbers in, band out, no imports.
  * `magnitude_advice` is PURE -- reads ONLY `params` + module-constant thresholds; imports nothing;
    NEVER raises; NEVER a hard-refuse (a "heavy" level is a FLAG to down-scale, not a block).
  * FAIL-SOFT everywhere. Telemetry, not a security boundary. The ONE intentional refuse is the
    catastrophic band in `governor_gate`; a band of "unknown" (telemetry failure) NEVER refuses.

Thresholds live as MODULE-LEVEL CONSTANTS so they're tunable without touching handler code.
"""

# ── band thresholds (GB) ─────────────────────────────────────────────────────
# RAM is the honest live signal on TD (see the telemetry note above). 64 GB box; bands with margin.
#   critical  < 4.0 GB free -- the OS is under pressure; heavy work refuses (the single hard-gate).
#   caution   < 8.0 GB free -- getting tight; proceed but flag prominently.
# VRAM bands are kept for parity + used only if a caller passes a measured VRAM figure (TD's API does
# not provide a whole-card avail, so envelope_status() runs RAM-only with vram_known=False by default).
VRAM_CRITICAL_GB = 1.0
VRAM_CAUTION_GB = 3.0
RAM_CRITICAL_GB = 4.0
RAM_CAUTION_GB = 8.0

_GUIDANCE = {
    "critical": "free VRAM below safe margin — reduce resolution/counts or clear the scene before heavy ops",
    "caution": "headroom is tight — consider lowering resolution/point/instance counts, or clearing unused operators first",
    "ok": "resource headroom ok",
}
_GUIDANCE_RAM_CRITICAL = "free system RAM below safe margin — close other apps or clear the scene before heavy ops"
_GUIDANCE_RAM_CAUTION = "system RAM headroom is tight — consider lowering counts or freeing memory"
_VRAM_UNKNOWN_NOTE = (" (whole-card VRAM headroom is not exposed by TouchDesigner's API — classified on "
                      "system RAM only; use top_info for per-TOP GPU memory)")

# ── magnitude-advisory thresholds (per-request REQUESTED magnitude, classified PRE-cook) ──────────
# ADVISORY FLAGS, not limits. magnitude_advice() reads only the caller's params dict + these constants
# and returns {level,note}; a "heavy" level NEVER refuses. Tunable here without touching handler code.
#
# Output RESOLUTION (the dominant realtime-GPU cost). A realtime GPU cooks every visible TOP every
# frame, so pixel count is the primary scale signal. >HD gets heavy for a realtime chain; >=4K width is
# heavy; and TD's NON-COMMERCIAL license CAPS output resolution at 1280, so any request above that is
# flagged (on non-commercial it silently won't produce the larger frame).
MAG_RES_CAUTION = 1920            # > 1920 (beyond HD) on a realtime chain: getting heavy
MAG_RES_HEAVY = 3840              # >= 3840 (4K width) / 2160 height: heavy
NONCOMMERCIAL_OUTPUT_CAP = 1280   # TD non-commercial output-resolution cap (a magnitude signal)
# INSTANCE / PARTICLE counts (geometry replicated on the GPU each frame).
MAG_INSTANCE_CAUTION = 10_000
MAG_INSTANCE_HEAVY = 100_000
# RENDER PASSES / multi-sampling (renderTOP passes, high AA): each pass re-renders the scene.
MAG_PASSES_CAUTION = 8
MAG_PASSES_HEAVY = 32

# ── ENFORCED magnitude CEILING ─────────────────────────────────────────────────────────────────────
# A HARD refuse, distinct from the advisory above. The advisory FLAGS "heavy" (a down-scale hint, ~4K);
# these CEILINGS REFUSE only a CATASTROPHIC, driver-killing magnitude far above any legitimate projection
# value (4K/8K delivery passes comfortably). Without this, a generic `set_par {resolutionw:100000}` reaches
# TD unclamped and exhausts VRAM -> display-driver TDR/hang -- the exact outcome the governor promises to
# prevent. Overridable ONLY by the arm.json `allow_highres` consent flag (human-gated, like the code
# lanes), so the capability is guarded, never permanently amputated. Tunable here without touching handlers.
CEIL_RES_DIM = 16384          # per-dimension pixel cap (>16K/dim is DoS territory; also the common GPU max tex dim)
CEIL_INSTANCES = 5_000_000    # instance/particle hard cap (advisory turns "heavy" at 100k)
CEIL_PASSES = 256             # render-pass hard cap (advisory turns "heavy" at 32)

_CEIL_RES_KEYS = frozenset({"resolutionw", "resolutionh", "resolution1", "resolution2", "resx", "resy"})
_CEIL_INST_KEYS = frozenset({"instancecount", "ninstances", "numinstances", "instances",
                             "numparticles", "maxparticles"})
_CEIL_PASS_KEYS = frozenset({"npasses", "passes"})

_MAG_OK = "ok"
_MAG_CAUTION = "caution"
_MAG_HEAVY = "heavy"


def classify_band(vram_avail_gb, ram_avail_gb, vram_known=False):
    """PURE band classifier. Numbers in → (band, guidance) out. No imports, never raises on normal
    numeric input.

    band ∈ {"critical","caution","ok"}:
      critical  vram_known and vram_avail_gb < VRAM_CRITICAL_GB, OR ram_avail_gb < RAM_CRITICAL_GB
      caution   vram_known and vram_avail_gb < VRAM_CAUTION_GB, OR ram_avail_gb < RAM_CAUTION_GB
      ok        otherwise.
    Default `vram_known=False` (TD's API does not expose whole-card VRAM): classification is RAM-only,
    with a note appended to guidance.
    """
    try:
        ram = float(ram_avail_gb)
    except (TypeError, ValueError):
        ram = None
    vram = None
    if vram_known:
        try:
            vram = float(vram_avail_gb)
        except (TypeError, ValueError):
            vram = None
            vram_known = False

    vram_crit = vram_known and vram is not None and vram < VRAM_CRITICAL_GB
    ram_crit = ram is not None and ram < RAM_CRITICAL_GB
    if vram_crit or ram_crit:
        g = _GUIDANCE_RAM_CRITICAL if (ram_crit and not vram_crit) else _GUIDANCE["critical"]
        if not vram_known:
            g += _VRAM_UNKNOWN_NOTE
        return "critical", g

    vram_caut = vram_known and vram is not None and vram < VRAM_CAUTION_GB
    ram_caut = ram is not None and ram < RAM_CAUTION_GB
    if vram_caut or ram_caut:
        g = _GUIDANCE_RAM_CAUTION if (ram_caut and not vram_caut) else _GUIDANCE["caution"]
        if not vram_known:
            g += _VRAM_UNKNOWN_NOTE
        return "caution", g

    g = _GUIDANCE["ok"]
    if not vram_known:
        g += _VRAM_UNKNOWN_NOTE
    return "ok", g


def _read_ram_gb():
    """System RAM (avail_gb, total_gb, load_pct) via a small GlobalMemoryStatusEx ctypes call (OS-level,
    no TouchDesigner dependency). Returns (None, None, None) on any failure (fail-soft)."""
    import ctypes
    from ctypes import wintypes
    gb = 1024.0 ** 3

    class MSX(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    msx = MSX()
    msx.dwLength = ctypes.sizeof(MSX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(msx)):
        return None, None, None
    return (round(msx.ullAvailPhys / gb, 1), round(msx.ullTotalPhys / gb, 1), int(msx.dwMemoryLoad))


def envelope_status():
    """Gather the live resource envelope and classify it. NEVER raises.

    Returns a dict with (best-effort) keys:
        ram_avail_gb, ram_total_gb, ram_load_pct,
        vram_basis  (always present: names the honest telemetry limit),
        band, guidance
    On total failure returns {"band":"unknown","guidance":"envelope unavailable"}.
    """
    try:
        status = {}
        try:
            ram_avail, ram_total, ram_load = _read_ram_gb()
        except Exception:  # noqa: BLE001
            ram_avail, ram_total, ram_load = None, None, None
        if ram_avail is not None:
            status["ram_avail_gb"] = ram_avail
        if ram_total is not None:
            status["ram_total_gb"] = ram_total
        if ram_load is not None:
            status["ram_load_pct"] = ram_load

        # VRAM: TD's API exposes no whole-card total/avail. Be explicit about the limit rather than
        # inventing a number. Per-TOP GPU memory is available via top_info (gpu_memory_bytes).
        status["vram_basis"] = ("whole-card VRAM not exposed by TouchDesigner's Python API; "
                                "classified on system RAM. Use top_info for per-TOP gpu_memory_bytes.")

        band, guidance = classify_band(None, status.get("ram_avail_gb"), vram_known=False)
        status["band"] = band
        status["guidance"] = guidance
        return status
    except Exception as exc:  # noqa: BLE001 — absolute backstop; never raise out of envelope_status
        return {"band": "unknown", "guidance": "envelope unavailable", "envelope_err": str(exc)[:80]}


def governor_gate(op_label):
    """Advisory gate for a heavy op. Calls envelope_status(); if band == "critical", RAISE ValueError
    (the ONE intentional hard-refuse). Otherwise return the status dict.

    FAIL-SOFT: a band of "unknown" (telemetry couldn't be read) does NOT refuse — never block real work
    on a telemetry failure. Only an explicit "critical" band refuses.
    """
    status = envelope_status()
    if status.get("band") == "critical":
        ram = status.get("ram_avail_gb")
        ram_s = ("%.1fGB" % ram) if isinstance(ram, (int, float)) else "unknown"
        raise ValueError("refused: %s — %s (free RAM %s)"
                         % (op_label, status.get("guidance", "resource envelope critical"), ram_s))
    return status


# ── magnitude advisory ────────────────────────────────────────────────────────────────────────────
def _fmt(n):
    """Human-readable count (5.0M / 500k / 42). Never raises."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    if n >= 1e6:
        return "%.1fM" % (n / 1e6)
    if n >= 1e3:
        return "%.0fk" % (n / 1e3)
    return "%d" % int(n)


def _mag(level, note):
    return {"level": level, "note": note}


def _num(params, *keys):
    """First present key in `params` coerced to float; None on missing/garbage. Never raises."""
    if not isinstance(params, dict):
        return None
    for k in keys:
        if k in params:
            try:
                return float(params[k])
            except (TypeError, ValueError):
                return None
    return None


def _res_note(res):
    """A note about the requested resolution relative to the non-commercial 1280 output cap."""
    if res > NONCOMMERCIAL_OUTPUT_CAP:
        return (" NOTE: TouchDesigner NON-COMMERCIAL caps output resolution at %d — a request above that "
                "will not produce the larger frame without a commercial license." % NONCOMMERCIAL_OUTPUT_CAP)
    return ""


def magnitude_advice(op_label, params):
    """Advisory classification of a REQUESTED magnitude BEFORE it cooks, from the params the caller passed.
    Returns {"level": "ok"|"caution"|"heavy", "note": str}.

    PURE: reads ONLY `params` + the module-constant thresholds; imports nothing; NEVER raises; NEVER a
    hard-refuse (a "heavy" level is a FLAG for the watching crew to down-scale, not a block). The primary
    realtime-GPU signal is OUTPUT RESOLUTION (every visible TOP cooks each frame); instance/particle
    counts and render passes are the secondary signals. Honors the non-commercial 1280 output cap.
    """
    try:
        p = params if isinstance(params, dict) else {}

        # ── output resolution (the dominant realtime-GPU cost; the 1280 non-commercial cap) ──────────
        w = _num(p, "resolutionw", "w", "resx", "resolution1", "resolution")
        h = _num(p, "resolutionh", "h", "resy", "resolution2")
        dims = [d for d in (w, h) if d is not None and d > 0]
        if dims:
            res = max(dims)
            note_res = _res_note(res)
            dim_s = "%dx%d" % (int(w or 0), int(h or 0)) if (w and h) else ("%d px" % int(res))
            if res >= MAG_RES_HEAVY or (h is not None and h >= 2160):
                return _mag(_MAG_HEAVY, "requested output %s — 4K+ is heavy for a realtime GPU chain; "
                            "confirm the deliverable needs it and budget the cook.%s" % (dim_s, note_res))
            if res > MAG_RES_CAUTION:
                return _mag(_MAG_CAUTION, "requested output %s — beyond HD; sizeable for a realtime "
                            "chain.%s" % (dim_s, note_res))
            if res > NONCOMMERCIAL_OUTPUT_CAP:
                # HD-ish but above the non-commercial cap: worth flagging even though GPU cost is modest.
                return _mag(_MAG_CAUTION, "requested output %s.%s" % (dim_s, note_res))
            return _mag(_MAG_OK, "requested output %s" % dim_s)

        # ── instance / particle counts ───────────────────────────────────────────────────────────────
        inst = _num(p, "instancecount", "ninstances", "numinstances", "instances",
                    "numparticles", "maxparticles", "birth", "count")
        if inst is not None and inst >= 0:
            if inst >= MAG_INSTANCE_HEAVY:
                return _mag(_MAG_HEAVY, "requested %s instances/particles — heavy on a realtime GPU; "
                            "confirm the target density." % _fmt(inst))
            if inst >= MAG_INSTANCE_CAUTION:
                return _mag(_MAG_CAUTION, "requested %s instances/particles — sizeable for realtime; "
                            "down-scale unless intended." % _fmt(inst))
            return _mag(_MAG_OK, "requested %s instances/particles" % _fmt(inst))

        # ── render passes / multi-sampling ─────────────────────────────────────────────────────────
        passes = _num(p, "npasses", "passes")
        if passes is not None and passes > 0:
            n = int(passes)
            if passes >= MAG_PASSES_HEAVY:
                return _mag(_MAG_HEAVY, "%d render passes — each re-renders the scene; heavy." % n)
            if passes >= MAG_PASSES_CAUTION:
                return _mag(_MAG_CAUTION, "%d render passes — sizeable per-frame cost." % n)
            return _mag(_MAG_OK, "%d render passes" % n)

        return _mag(_MAG_OK, "no magnitude heuristic for these params")
    except Exception:  # noqa: BLE001 — advisory must NEVER raise out of a handler
        return {"level": _MAG_OK, "note": "magnitude advisory unavailable"}


# ── enforced ceiling — the HARD refuse ───────────────────────────────────────────────────────────────
def magnitude_ceiling_for(parname):
    """Return (cap, kind) for a KNOWN catastrophic-magnitude parameter name, else (None, None). PURE.
    Scoped to UNAMBIGUOUS magnitude params only (resolutionw/h, instance/particle counts, render passes) so
    the enforced refuse never fires on a legitimately-small look-alike (e.g. a COMP's `w`)."""
    p = str(parname).lower()
    if p in _CEIL_RES_KEYS:
        return CEIL_RES_DIM, "resolution (px/dim)"
    if p in _CEIL_INST_KEYS:
        return CEIL_INSTANCES, "instance/particle count"
    if p in _CEIL_PASS_KEYS:
        return CEIL_PASSES, "render passes"
    return None, None


def enforce_magnitude_ceiling(parname, value, allow_override=False):
    """HARD-REFUSE a catastrophic magnitude on a known parameter: raise ValueError when `value`
    exceeds the enforced ceiling, UNLESS `allow_override` (the human-gated arm.json `allow_highres` consent).
    A non-numeric value or an unknown/non-magnitude parameter passes silently. PURE: no imports, reads no
    config (the caller supplies allow_override), never raises except the intentional ceiling refusal."""
    if allow_override:
        return
    cap, kind = magnitude_ceiling_for(parname)
    if cap is None:
        return
    try:
        v = float(value)
    except (TypeError, ValueError):
        return
    if v > cap:
        raise ValueError(
            "refused: %s=%s exceeds the enforced %s ceiling (%s) — this magnitude can exhaust GPU/VRAM and "
            "hang the display driver. Lower it, or set \"allow_highres\": true in "
            "~/.touchdesigner-bridge-mcp/arm.json to override." % (parname, _fmt(v), kind, _fmt(cap)))
