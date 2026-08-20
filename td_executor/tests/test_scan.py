"""Offline tests for the import_scan endpoint (handlers/scan.py) -- the meshed-scan analog of
import_segmented_model. Mirrors the mock usage in test_visibility.py / _tdmock.py.

MOCK SCOPE (honest, so a reviewer knows what green means): the offline mock's create() only populates
real parameters for a few optypes (noiseTOP/opviewerTOP/renameCHOP/tableDAT/textDAT) and has no
input-connectors on generic ops, and cannot load real geometry. So these tests exercise the endpoint's
STRUCTURE and control flow -- which nodes get created, the load->convert->decimate chain, the branch per
kind/extension, the return shape, path confinement, and the data-only guarantee (no expression writes) --
NOT live parameter values, internal wiring, or cooked attributes. Those are LIVE-only (flagged in the
endpoint's note)."""
import os
import tempfile
import unittest

from td_executor.tests import _tdmock
from td_executor.tests._tdmock import install
from td_executor.handlers import scan


class _Base(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = install()
        self._saved_wd = self.server.WORKING_DIR
        self._tmp = os.path.realpath(tempfile.mkdtemp(prefix="tdmcp_scan_"))
        self.server.WORKING_DIR = self._tmp
        self.server._WD_OVERRIDE = self._tmp   # working_dir() honors this over the live arm.json

    def tearDown(self):
        self.server.WORKING_DIR = self._saved_wd
        self.server._WD_OVERRIDE = None

    def _asset(self, fname, write=True):
        p = os.path.join(self._tmp, fname)
        if write:
            with open(p, "wb") as f:
                f.write(b"# fake asset\n")
        return p

    def _geo(self):
        return self.scene.ops["/project1"]

    def _child(self, comp, name):
        return next((c for c in comp.children if getattr(c, "name", None) == name), None)


class TestRegistration(_Base):
    def test_registered_endpoint(self):
        self.assertIn("import_scan", self.server._REGISTRY)


class TestObjMeshPath(_Base):
    def test_obj_uses_filein_sop(self):
        out = scan.import_scan({"file": self._asset("mesh.obj"), "name": "scan"})
        self.assertEqual(out["kind"], "mesh")
        self.assertEqual(out["geo"], "/project1/scan")
        self.assertEqual(out["material"], "/project1/scan_mat")
        # the geometryCOMP was created under /project1 and holds a File In SOP named 'load'
        geo = self._child(self._geo(), "scan")
        self.assertIsNotNone(geo)
        self.assertEqual(geo.opType, "geometryCOMP")
        loader = self._child(geo, "load")
        self.assertIsNotNone(loader)
        self.assertEqual(loader.opType, "fileinSOP")   # .obj -> File In SOP (NOT File In POP)
        # a dark phongMAT was created as a sibling under the parent
        mat = self._child(self._geo(), "scan_mat")
        self.assertIsNotNone(mat)
        self.assertEqual(mat.opType, "phongMAT")
        # render flag set on the final SOP (what the geometryCOMP renders)
        self.assertTrue(getattr(loader, "render", False))
        self.assertTrue(getattr(loader, "display", False))
        self.assertEqual(out["render_sop"], loader.path)
        # data-only: no parameter EXPRESSION was ever written
        self.assertEqual(_tdmock.EXPR_WRITES, [])
        # a real file present -> no "does not exist" warning
        self.assertFalse(any("does not exist" in w for w in out["warnings"]))

    def test_missing_file_warns_not_fatal(self):
        out = scan.import_scan({"file": self._asset("gone.obj", write=False)})
        self.assertTrue(any("does not exist" in w for w in out["warnings"]))
        # rig is still built (default name 'scan')
        self.assertEqual(out["geo"], "/project1/scan")


class TestPlyMeshPath(_Base):
    def test_ply_uses_filein_pop_then_poptosop(self):
        out = scan.import_scan({"file": self._asset("mesh.ply"), "name": "bldg"})
        self.assertEqual(out["kind"], "mesh")
        geo = self._child(self._geo(), "bldg")
        loader = self._child(geo, "load")
        pts = self._child(geo, "pop_to_sop")
        self.assertEqual(loader.opType, "fileinPOP")   # .ply is NOT a File In SOP format
        self.assertEqual(pts.opType, "poptoSOP")       # POP -> SOP bridge to renderable polygons
        self.assertEqual(out["render_sop"], pts.path)  # render flag lands on the SOP, not the POP
        self.assertEqual(len(out["chain"]), 2)
        self.assertEqual(_tdmock.EXPR_WRITES, [])


class TestPointCloudPath(_Base):
    def test_pointcloud_uses_point_filein_pop(self):
        out = scan.import_scan({"file": self._asset("cloud.ply"), "kind": "pointcloud",
                                "name": "cloud", "max_points": 500000})
        self.assertEqual(out["kind"], "pointcloud")
        geo = self._child(self._geo(), "cloud")
        loader = self._child(geo, "load")
        pts = self._child(geo, "pop_to_sop")
        self.assertEqual(loader.opType, "pointfileinPOP")
        self.assertEqual(pts.opType, "poptoSOP")
        # the honest "a raw cloud is not a projection surface" warning is present
        self.assertTrue(any("reconstruct" in w.lower() for w in out["warnings"]))
        self.assertEqual(_tdmock.EXPR_WRITES, [])


class TestDecimation(_Base):
    def test_target_polys_creates_polyreduce_number(self):
        out = scan.import_scan({"file": self._asset("m.obj"), "target_polys": 500000})
        geo = self._child(self._geo(), "scan")
        red = self._child(geo, "reduce")
        self.assertIsNotNone(red)
        self.assertEqual(red.opType, "polyreduceSOP")
        self.assertEqual(out["decimated"]["method"], "number")
        self.assertEqual(out["decimated"]["numpolys"], 500000)
        self.assertFalse(out["decimated"]["auto"])
        self.assertEqual(out["render_sop"], red.path)   # render flag moves to the decimated SOP

    def test_reduce_percent_creates_polyreduce_percentage(self):
        out = scan.import_scan({"file": self._asset("m.obj"), "reduce_percent": 5.0})
        self.assertEqual(out["decimated"]["method"], "percentage")
        self.assertEqual(out["decimated"]["percentage"], 5.0)

    def test_no_reduce_arg_no_polyreduce_in_mock(self):
        # mock cooks 0 prims, so no auto-decimation triggers and no reduce node is made
        out = scan.import_scan({"file": self._asset("m.obj")})
        self.assertIsNone(out["decimated"])
        geo = self._child(self._geo(), "scan")
        self.assertIsNone(self._child(geo, "reduce"))


class TestConfinementAndValidation(_Base):
    def test_asset_outside_working_dir_refused(self):
        # an asset that lives outside the working dir (e.g. on another drive) -> refused here
        with self.assertRaises(PermissionError):
            scan.import_scan({"file": "D:/outside/mesh.ply"})

    def test_path_traversal_refused(self):
        with self.assertRaises(PermissionError):
            scan.import_scan({"file": "../../escape.ply"})

    def test_bad_kind_refused(self):
        with self.assertRaises(ValueError):
            scan.import_scan({"file": self._asset("m.obj"), "kind": "volume"})

    def test_unknown_parent_raises(self):
        with self.assertRaises(ValueError):
            scan.import_scan({"file": self._asset("m.obj"), "parent": "/nope"})


class TestDataOnlySource(unittest.TestCase):
    def test_no_code_or_expression_sink_in_source(self):
        src_path = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
                                "handlers", "scan.py")
        with open(src_path, "r", encoding="utf-8") as f:
            src = f.read().lower()
        for sink in (".expr", "setexpression", "exec(", "eval(", "screengrab", "os.system"):
            self.assertNotIn(sink, src, "scan.py must contain no code/expression sink (found %r)" % sink)


if __name__ == "__main__":
    unittest.main()
