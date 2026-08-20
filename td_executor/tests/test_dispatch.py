"""The Web Server DAT dispatch envelope in server.handle(): token auth (constant-time compare),
the 1 MB request-body cap, JSON-body validation, and unknown-endpoint handling. These are the
SECURITY.md guarantees (auth + body cap) enforced on the executor's front door; handle() runs on
TD's main thread but takes plain dicts, so it is fully exercisable offline."""
import unittest

from td_executor.tests._tdmock import install


def _call(server, name, data="{}", headers=None):
    request = {"uri": "/tool/" + name, "data": data}
    if headers:
        request.update(headers)
    resp = {}
    return server.handle(None, request, resp)


class TestDispatchEnvelope(unittest.TestCase):
    def setUp(self):
        self.server, _ = install()
        self._saved_token = self.server.TOKEN

    def tearDown(self):
        self.server.TOKEN = self._saved_token

    def test_health_is_open(self):
        resp = self.server.handle(None, {"uri": "/health", "data": ""}, {})
        self.assertEqual(resp["statusCode"], 200)

    def test_open_mode_allows_call(self):
        self.server.TOKEN = ""  # dev/open (loopback is the boundary)
        resp = _call(self.server, "scene_info", "{}")
        self.assertEqual(resp["statusCode"], 200)

    def test_token_required_when_set(self):
        self.server.TOKEN = "s3cr3t-session-token"
        # no token -> 403
        self.assertEqual(_call(self.server, "scene_info", "{}")["statusCode"], 403)
        # wrong token -> 403
        self.assertEqual(
            _call(self.server, "scene_info", "{}", {"X-TDMCP-Token": "wrong"})["statusCode"], 403)
        # correct token -> 200 (header lookup is case-insensitive)
        self.assertEqual(
            _call(self.server, "scene_info", "{}", {"x-tdmcp-token": "s3cr3t-session-token"})["statusCode"], 200)

    def test_body_cap_rejects_oversize(self):
        big = "{" + "x" * (self.server.MAX_BODY_BYTES + 1) + "}"
        self.assertEqual(_call(self.server, "scene_info", big)["statusCode"], 413)

    def test_invalid_json_body(self):
        self.assertEqual(_call(self.server, "scene_info", "{not json")["statusCode"], 422)

    def test_non_object_body(self):
        self.assertEqual(_call(self.server, "scene_info", "[1,2,3]")["statusCode"], 422)

    def test_unknown_endpoint(self):
        self.assertEqual(_call(self.server, "no_such_tool", "{}")["statusCode"], 404)


if __name__ == "__main__":
    unittest.main()
