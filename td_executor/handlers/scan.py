"""td_executor/handlers/scan.py -- the building-SCAN import convenience endpoint (the meshed-scan analog
of control.import_segmented_model). ONE call turns a raw building-scan asset (a `.ply`/`.obj` mesh, or a
point cloud) into a projection-mapping-ready rig: a geometryCOMP holding the (decimated) surface with
render+display flags and a dark, emission-driven phongMAT assigned -- ready for a renderTOP + camera.

Data-only, exactly like import_segmented_model: it only CREATES data operators and sets LITERAL parameter
values / node flags (via control._set_lit + server.confined_path). No code, no expressions.

TD 2025 operator pipeline (researched against docs.derivative.ca; see the endpoint docstring for citations):
  * .obj MESH        -> fileinSOP (File In SOP loads .obj/.tog/.classic -- NOT .ply)
  * .ply (or other) MESH -> fileinPOP (File In POP loads .ply/.obj mesh into a GPU POP) -> poptoSOP
                            (POP to SOP -> CPU polygons) so the SOP decimator + render flag apply
  * POINT CLOUD      -> pointfileinPOP (Point File In POP loads .ply/.obj/.xyz/... as POINTS only) -> poptoSOP
  * heavy MESH       -> polyreduceSOP (Polyreduce SOP) decimates for realtime GPU
"""
from td_executor import server
from td_executor.handlers import control


# A realtime GPU chokes on multi-million-face scans (a dense photogrammetry mesh can exceed 10M faces), so mesh
# imports are decimated. These bound the one-call defaults; an explicit reduce_percent / target_polys wins.
_REALTIME_FACE_BUDGET = 750_000     # auto-decimation target when a heavy mesh is imported with no reduce arg
_HEAVY_FACE_WARN = 1_000_000        # above this we warn about realtime cost even if not auto-decimating
_OBJ_SOP_EXTS = (".obj", ".tog", ".classic", ".bhclassic")   # what File In SOP itself can load (doc)


def _reuse_or_create(container, name, optype):
    """Idempotent-by-name child create (mirrors import_segmented_model)."""
    for c in list(container.children):
        if getattr(c, "name", None) == name:
            return c
    return container.create(optype, name)


def _wire(src, dst, in_idx=0):
    """Wire src -> dst input in_idx using the same connector API control.connect uses, but tolerant:
    returns True on success, False if the runtime/mock exposes no such connector (LIVE-only wiring).
    Never raises -- a failed internal wire is reported, not fatal (mock has no input connectors)."""
    try:
        conns = dst.inputConnectors
        if in_idx < len(conns):
            conns[in_idx].connect(src)
            return True
    except Exception:
        pass
    return False


def _set_flag(op, flag, value):
    try:
        setattr(op, flag, bool(value))
    except Exception:
        pass


def _geo_attribs(sop):
    """Report (has_uv, has_normal, npoints, nprims) for a cooked SOP, fully defensively -- never raises.
    UVs (attrib 'uv'/'uv0') are load-bearing for the projection method (emitmapcoord=uv0); normals ('N')
    matter for any lit path. Unknown/mock API -> (False, False, None, None)."""
    try:
        sop.cook(force=True)
    except Exception:
        pass
    has_uv = has_normal = False
    npts = nprims = None
    try:
        npts = int(sop.numPoints)
    except Exception:
        pass
    try:
        nprims = int(sop.numPrims)
    except Exception:
        pass
    for coll_name in ("pointAttribs", "vertexAttribs", "primAttribs"):
        try:
            coll = getattr(sop, coll_name, None)
            if coll is None:
                continue
            names = {getattr(a, "name", None) for a in coll}
            if names & {"uv", "uv0"}:
                has_uv = True
            if "N" in names:
                has_normal = True
        except Exception:
            pass
    return has_uv, has_normal, npts, nprims


@server.endpoint("import_scan")
def import_scan(params):
    """Build a projection-mapping-ready RIG from a building-scan asset in ONE call (the meshed-scan analog
    of import_segmented_model): load a `.ply`/`.obj` scan -- a MESH or a POINT CLOUD -- decimate a heavy
    mesh for realtime, wrap it in a geometryCOMP with render+display flags, and assign a dark,
    emission-driven phongMAT. Ready for a renderTOP + camera. Data-only: only creates data operators and
    sets literal values / node flags.

    TD 2025 operator pipeline (researched, not guessed -- docs.derivative.ca):
      * kind='mesh', .obj  : File In SOP (fileinSOP) -- loads .obj/.tog/.classic; .ply is NOT a File In
                             SOP format, see https://docs.derivative.ca/File_In_SOP
      * kind='mesh', .ply  : File In POP (fileinPOP) -- loads .ply/.obj MESH (faces) into a GPU POP,
                             https://docs.derivative.ca/File_In_POP -- then POP to SOP (poptoSOP),
                             https://docs.derivative.ca/POP_to_SOP -- to get CPU polygons the SOP
                             decimator + render flag act on.
      * kind='pointcloud'  : Point File In POP (pointfileinPOP) -- loads .ply/.obj/.xyz/.pts/... as POINTS
                             ONLY (no polygons), https://docs.derivative.ca/Point_File_In_POP -- then
                             POP to SOP so the cloud is renderable. A raw cloud is NOT a projection surface:
                             true surface reconstruction (Poisson/ball-pivot) is an UPSTREAM step (e.g. the
                             houdini-lidar mesh; or supply an already-meshed .ply); this lane gives a
                             visible reference rig + a loud warning.
      * heavy mesh         : Polyreduce SOP (polyreduceSOP), https://docs.derivative.ca/Polyreduce_SOP --
                             decimates. Auto-applied (target ~750k faces) when a >1M-face mesh is imported
                             with no reduce arg; overridden by reduce_percent or target_polys.

      file           : path to the scan (.ply/.obj/...). Confined to the working dir EXACTLY
                       like import_segmented_model -- an asset outside the working dir is refused.
      kind           : 'mesh' (default) or 'pointcloud'.
      parent         : COMP to build under (default /project1).
      name           : geometryCOMP name (default 'scan'); the material is '<name>_mat'.
      reduce_percent : Polyreduce target as a PERCENT of the original polycount (method=percentage).
      target_polys   : Polyreduce target as an ABSOLUTE face count (method=number). Takes precedence over
                       reduce_percent. If neither is given and the mesh is heavy, an automatic
                       target_polys=~750k decimation is applied (with a warning).
      max_points     : (point cloud) cap the loaded point count (pointfileinPOP maxpoints) to thin a huge
                       cloud for realtime.

    Returns the built rig: geometryCOMP path, material path, the loader-chain node paths, UV/normal
    presence, point/face counts, whether decimation was applied, and heaviness/watertight warnings.
    Idempotent by name (re-running rebuilds the same '<name>' COMP + '<name>_mat')."""
    import os as _os

    # Confine the asset path to the working dir, EXACTLY like import_segmented_model's dir.
    # (If an asset lives outside the working dir, this WILL refuse it until the working-dir/asset-root
    # is configured. Confining correctly is the right behaviour; solving the asset-root is a deployment
    # configuration decision, not this endpoint's job.)
    filepath = server.confined_path(str(params["file"]))
    ext = _os.path.splitext(filepath)[1].lower()
    kind = str(params.get("kind", "mesh")).lower()
    if kind not in ("mesh", "pointcloud"):
        raise ValueError("kind must be 'mesh' or 'pointcloud' (got %r)" % kind)

    parent = server.resolve_op(params.get("parent") or "/project1")
    if parent is None:
        raise ValueError("no such parent COMP: %s" % (params.get("parent") or "/project1"))
    name = str(params.get("name", "scan"))
    matname = str(params.get("matname") or (name + "_mat"))

    warnings = []
    if not _os.path.isfile(filepath):
        warnings.append("asset file does not exist under the working dir: %s -- the loader will cook 0 "
                        "points until the file is present/configured" % filepath.replace("\\", "/"))

    # Reduce-target resolution (explicit args win; else auto-decimate a heavy mesh below).
    target_polys = params.get("target_polys")
    reduce_percent = params.get("reduce_percent")
    if target_polys is not None:
        target_polys = int(target_polys)
    if reduce_percent is not None:
        reduce_percent = float(reduce_percent)

    # Optypes this endpoint may instantiate -- data-only boundary check up front (parity with
    # import_segmented_model). None carry code markers; any denial raises before anything is created.
    server.check_optype_allowed("geometryCOMP")
    server.check_optype_allowed("phongMAT")

    # ---- build the geometryCOMP shell (idempotent), clearing its default torus + any prior build ----
    geo = _reuse_or_create(parent, name, "geometryCOMP")
    for ch in list(geo.children):
        try:
            ch.destroy()
        except Exception:
            pass

    chain = []          # ordered node paths of the load->convert->decimate chain, for the report
    prev = None         # upstream node to wire into the next
    render_sop = None   # the FINAL SOP in the chain -- gets the render+display flags

    # ---- LOADER stage ---------------------------------------------------------------------------------
    if kind == "pointcloud":
        server.check_optype_allowed("pointfileinPOP")
        server.check_optype_allowed("poptoSOP")
        loader = geo.create("pointfileinPOP", "load")
        control._set_lit(loader, "file", filepath)   # confined literal (never an expression)
        mp = params.get("max_points")
        if mp is not None:
            control._set_lit(loader, "maxpointsenable", True)
            control._set_lit(loader, "maxpoints", int(mp))
        chain.append(loader.path)
        prev = loader
        # POP -> SOP so the cloud is renderable inside the geometryCOMP.
        pts = geo.create("poptoSOP", "pop_to_sop")
        control._set_lit(pts, "pop", loader.path)     # POP-reference param (poptoSOP.pop)
        _wire(prev, pts)                              # belt-and-suspenders (LIVE only; mock has no connectors)
        chain.append(pts.path)
        prev = render_sop = pts
        warnings.append("kind='pointcloud': loaded POINTS only (Point File In POP imports no polygons). A raw "
                        "cloud is NOT a projection surface -- reconstruct a mesh UPSTREAM (houdini-lidar) or "
                        "supply an already-meshed .ply; this rig renders the cloud as reference geometry.")
    elif ext in _OBJ_SOP_EXTS:
        # .obj (etc.): File In SOP loads it straight into SOP land (the import_segmented_model path).
        server.check_optype_allowed("fileinSOP")
        loader = geo.create("fileinSOP", "load")
        control._set_lit(loader, "file", filepath)
        chain.append(loader.path)
        prev = render_sop = loader
    else:
        # .ply (and any non-SOP mesh format): File In POP loads the MESH, POP to SOP brings it to CPU
        # polygons that Polyreduce + the render flag can act on. (File In SOP cannot load .ply.)
        server.check_optype_allowed("fileinPOP")
        server.check_optype_allowed("poptoSOP")
        loader = geo.create("fileinPOP", "load")
        control._set_lit(loader, "file", filepath)
        if ext not in (".ply", ".obj", ".tog", ".bhclassic", ".hclassic"):
            warnings.append("extension %r is not a documented File In POP mesh format (.ply/.obj/.tog/"
                            ".bhclassic) -- load may fail; pass kind='pointcloud' for a point file" % ext)
        chain.append(loader.path)
        prev = loader
        pts = geo.create("poptoSOP", "pop_to_sop")
        control._set_lit(pts, "pop", loader.path)
        _wire(prev, pts)
        chain.append(pts.path)
        prev = render_sop = pts

    # ---- attrib / count probe on the current final SOP (before optional decimation) -------------------
    has_uv, has_normal, npts, nprims = _geo_attribs(render_sop) if render_sop is not None else (False, False, None, None)

    # ---- DECIMATION stage (mesh only) -----------------------------------------------------------------
    decimated = None
    if kind == "mesh":
        want_reduce = target_polys is not None or reduce_percent is not None
        auto = False
        if not want_reduce and isinstance(nprims, int) and nprims > _HEAVY_FACE_WARN:
            # auto-decimate a heavy mesh imported with no explicit target
            target_polys = _REALTIME_FACE_BUDGET
            want_reduce = auto = True
        if want_reduce:
            server.check_optype_allowed("polyreduceSOP")
            red = geo.create("polyreduceSOP", "reduce")
            if target_polys is not None:
                control._set_lit(red, "method", "number")
                control._set_lit(red, "numpolys", int(target_polys))
                decimated = {"method": "number", "numpolys": int(target_polys), "auto": auto}
            else:
                control._set_lit(red, "method", "percentage")
                control._set_lit(red, "percentage", float(reduce_percent))
                decimated = {"method": "percentage", "percentage": float(reduce_percent), "auto": auto}
            _wire(prev, red)
            chain.append(red.path)
            prev = render_sop = red
            if auto:
                warnings.append("mesh has %s faces (>%s) and no reduce_percent/target_polys was given -- "
                                "AUTO-decimated to ~%s faces for realtime. Pass target_polys/reduce_percent "
                                "to control this." % (nprims, _HEAVY_FACE_WARN, _REALTIME_FACE_BUDGET))
        elif isinstance(nprims, int) and nprims > _HEAVY_FACE_WARN:
            warnings.append("mesh has %s faces (>%s) -- likely too heavy for a realtime GPU; consider "
                            "target_polys/reduce_percent" % (nprims, _HEAVY_FACE_WARN))

    # ---- render/display flags on the FINAL SOP (what the geometryCOMP renders) -------------------------
    if render_sop is not None:
        _set_flag(render_sop, "render", True)
        _set_flag(render_sop, "display", True)
    control._set_lit(geo, "render", True)

    # ---- dark, emission-driven phongMAT (like import_segmented_model) + assign to the COMP -------------
    mat = _reuse_or_create(parent, matname, "phongMAT")
    for p in ("diffr", "diffg", "diffb", "specr", "specg", "specb", "emitr", "emitg", "emitb"):
        control._set_lit(mat, p, 0)
    mp = getattr(geo.par, "material", None)
    if mp is not None:
        try:
            mp.val = mat
        except Exception:
            try:
                mp.val = mat.path
            except Exception:
                pass

    # ---- warnings on attrib presence + watertight/heaviness -------------------------------------------
    if not npts:
        warnings.append("final SOP cooked 0 points -- the asset did not load (verify the file exists, is "
                        "under the working dir, and is a supported format for this kind)")
    if not has_uv:
        warnings.append("no 'uv' attribute -- emitmapcoord=uv0 content will NOT map correctly (re-export "
                        "the scan with UVs, or UV-unwrap it before projecting)")
    if not has_normal:
        warnings.append("no 'N' (normal) attribute -- lit/PBR shading may be flat (File In SOP/POP can "
                        "compute normals; enable normals on the loader)")
    warnings.append("watertight/manifold is NOT checked here (a TD-side concern for upstream cleanup); "
                    "building EXTERIOR scans are typically open shells with non-manifold edges -- fine for "
                    "projection onto the visible faces, but verify facing/backface culling in the render.")

    note = ("Built projection-mapping rig '%s' (geometryCOMP, render+display on the final SOP) with a dark, "
            "emission-driven phongMAT '%s'. kind=%s; %d-node load chain%s. Render with a renderTOP + camera "
            "(geometry='%s'); animate via a choreography (drive %s emit) or assign content (%s.emitmap, "
            "emitmapcoord=uv0). NOTE most parameters + internal wiring are set on the LIVE TD API; the offline "
            "mock exercises structure only." % (
                geo.path, mat.path, kind, len(chain),
                (" + decimation" if decimated else ""), geo.path, matname, matname))

    return {
        "kind": kind,
        "geo": geo.path,
        "material": mat.path,
        "chain": chain,
        "render_sop": render_sop.path if render_sop is not None else None,
        "file": filepath.replace("\\", "/"),
        "uv": has_uv,
        "normals": has_normal,
        "points": npts,
        "prims": nprims,
        "decimated": decimated,
        "warnings": warnings,
        "note": note,
    }
