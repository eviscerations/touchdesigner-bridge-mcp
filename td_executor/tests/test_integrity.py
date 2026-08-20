"""P0.4 executor hash-pin: verify_integrity() must round-trip a fresh manifest, and fail closed on a
one-byte tamper, a missing pinned file, and an unpinned handler file smuggled onto disk. Self-contained:
it builds its OWN fixture package + manifest in a tmpdir and points verify_integrity() at them, so it does
NOT depend on the committed td_executor/INTEGRITY.json."""
import hashlib
import json
import os
import shutil
import tempfile
import unittest

from td_executor.tests._tdmock import install


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


class TestIntegrity(unittest.TestCase):
    def setUp(self):
        self.server, _ = install()
        self.pkg = tempfile.mkdtemp(prefix="tdmcp_integ_")
        os.makedirs(os.path.join(self.pkg, "handlers"))
        # fixture pinned files
        self._write("__init__.py", b"# pkg\n")
        self._write("server.py", b"SERVER = 1\n")
        self._write("governor.py", b"GOV = 1\n")
        self._write("handlers/__init__.py", b"# handlers\n")
        self._write("handlers/foo.py", b"FOO = 1\n")
        self.manifest = os.path.join(self.pkg, "INTEGRITY.json")
        self._write_manifest()

    def tearDown(self):
        shutil.rmtree(self.pkg, ignore_errors=True)

    def _write(self, rel, data):
        with open(os.path.join(self.pkg, rel.replace("/", os.sep)), "wb") as fh:
            fh.write(data)

    def _pinned_rels(self):
        return ["__init__.py", "server.py", "governor.py", "handlers/__init__.py", "handlers/foo.py"]

    def _write_manifest(self):
        files = {rel: _sha(os.path.join(self.pkg, rel.replace("/", os.sep))) for rel in self._pinned_rels()}
        with open(self.manifest, "w", encoding="utf-8") as fh:
            json.dump({"algo": "sha256", "files": files}, fh)

    def _verify(self):
        return self.server.verify_integrity(pkg_dir=self.pkg, manifest_path=self.manifest, enforce=True)

    def test_roundtrip_passes(self):
        out = self._verify()
        self.assertTrue(out["enforced"])
        self.assertEqual(out["files"], 5)

    def test_tamper_one_byte_raises(self):
        self._write("handlers/foo.py", b"FOO = 2\n")   # digest now differs from manifest
        with self.assertRaises(self.server.IntegrityError):
            self._verify()

    def test_missing_pinned_file_raises(self):
        os.remove(os.path.join(self.pkg, "handlers", "foo.py"))   # pinned but gone -> unreadable
        with self.assertRaises(self.server.IntegrityError):
            self._verify()

    def test_smuggled_unpinned_handler_raises(self):
        self._write("handlers/bar.py", b"BAR = 1\n")   # on disk, not in manifest -> set mismatch
        with self.assertRaises(self.server.IntegrityError):
            self._verify()

    def test_missing_manifest_raises(self):
        os.remove(self.manifest)
        with self.assertRaises(self.server.IntegrityError):
            self._verify()

    def test_bypass_flag_skips(self):
        self._write("handlers/foo.py", b"tampered\n")
        out = self.server.verify_integrity(pkg_dir=self.pkg, manifest_path=self.manifest, enforce=False)
        self.assertFalse(out["enforced"])


if __name__ == "__main__":
    unittest.main()
