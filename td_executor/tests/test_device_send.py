"""device_send: the consent-gated PJLink Class-1 projector-control lane.

Verifies the security-critical invariants: (1) default-OFF consent refuses before touching anything;
(2) only CLOSED-allowlist tokens are accepted (never a caller string on the wire); (3) the reviewed mapped
body is what gets sent to a tcpipDAT; (4) a non-tcpipDAT target is refused; (5) every allowlisted body is a
safe PJLink Class-1 frame. Consent is forced ON by monkeypatching _read_allow_device_control (default-off)."""
import unittest

from td_executor.tests._tdmock import install, MockOp, MockPar
from td_executor.handlers import device as dev


class _Recorder:
    """A stand-in tcpipDAT.send() that records how it was called."""
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class TestDeviceSend(unittest.TestCase):
    def setUp(self):
        self.server, self.scene = install()
        self.op = MockOp("/project1/pjlink1",
                         pars={"port": MockPar("port", val=4352, style="Int")},
                         opType="tcpipDAT", family="DAT")
        self.rec = _Recorder()
        self.op.send = self.rec          # give the mock DAT a recording send()
        self.scene.add(self.op)
        self._saved = dev._read_allow_device_control

    def tearDown(self):
        dev._read_allow_device_control = self._saved

    def _consent(self, on):
        dev._read_allow_device_control = (lambda: on)

    # (1) default-OFF consent refuses before anything is sent
    def test_refused_without_consent(self):
        self._consent(False)
        with self.assertRaises(PermissionError):
            dev.device_send({"op": "/project1/pjlink1", "command": "power_on"})
        self.assertEqual(self.rec.calls, [], "nothing may be sent without consent")

    # (2) only closed-allowlist tokens are accepted -- no caller string reaches the wire
    def test_arbitrary_command_refused(self):
        self._consent(True)
        for bad in ("%1POWR 1", "power_on; rm -rf", "reboot", "", None, "POWR"):
            with self.assertRaises(ValueError):
                dev.device_send({"op": "/project1/pjlink1", "command": bad})
        self.assertEqual(self.rec.calls, [], "a non-allowlisted command must send nothing")

    # (3) the reviewed mapped body is sent to the tcpipDAT with a CR terminator
    def test_valid_command_sends_reviewed_body(self):
        self._consent(True)
        r = dev.device_send({"op": "/project1/pjlink1", "command": "power_on"})
        self.assertEqual(r["sent"], "%1POWR 1")
        self.assertFalse(r["is_query"])
        self.assertEqual(len(self.rec.calls), 1)
        args, kwargs = self.rec.calls[0]
        self.assertEqual(args[0], "%1POWR 1", "the exact reviewed body is sent")
        self.assertEqual(kwargs.get("terminator"), "\r", "PJLink uses a CR terminator")

    # (4) a non-tcpipDAT target is refused
    def test_wrong_optype_refused(self):
        self._consent(True)
        noise = MockOp("/project1/noise1", opType="noiseTOP", family="TOP")
        self.scene.add(noise)
        with self.assertRaises(ValueError):
            dev.device_send({"op": "/project1/noise1", "command": "power_on"})

    # (4b) a tcpipDAT pointed at a non-PJLink port (not 4352) is refused -- nothing sent
    def test_wrong_port_refused(self):
        self._consent(True)
        off = MockOp("/project1/pjlink_badport",
                     pars={"port": MockPar("port", val=7000, style="Int")},
                     opType="tcpipDAT", family="DAT")
        rec = _Recorder()
        off.send = rec
        self.scene.add(off)
        with self.assertRaises(ValueError):
            dev.device_send({"op": "/project1/pjlink_badport", "command": "power_on"})
        self.assertEqual(rec.calls, [], "a non-4352 target port must send nothing")

    # (5) query tokens are flagged; a query body is sent read-only
    def test_query_flag(self):
        self._consent(True)
        r = dev.device_send({"op": "/project1/pjlink1", "command": "power_query"})
        self.assertTrue(r["is_query"])
        self.assertEqual(r["sent"], "%1POWR ?")

    # (fence) every allowlisted body is a safe PJLink Class-1 frame (mirrors the import-time assert)
    def test_allowlist_bodies_are_safe_class1(self):
        for tok, body in dev._PJLINK_COMMANDS.items():
            self.assertTrue(body.startswith("%1"), "%s: not a Class-1 frame" % tok)
            for marker in dev._PJLINK_FORBIDDEN_MARKERS:
                self.assertNotIn(marker, body, "%s: body carries a forbidden marker %r" % (tok, marker))


if __name__ == "__main__":
    unittest.main()
