"""P0.3 token default-on: with server.TOKEN set to a known value, an auth=True endpoint must 403 when the
X-TDMCP-Token header is missing or wrong and 200 when correct; an auth=False endpoint (health) stays open.
Exercised through the same in-process server.handle() path the dispatch tests use (handle() takes plain
dicts, so it runs offline on TD's would-be main thread)."""
import unittest

from td_executor.tests._tdmock import install


def _call(server, name, data="{}", headers=None):
    request = {"uri": "/tool/" + name, "data": data}
    if headers:
        request.update(headers)
    return server.handle(None, request, {})


class TestAuthTokenDefaultOn(unittest.TestCase):
    KNOWN = "known-session-token-abc123"

    def setUp(self):
        self.server, _ = install()
        self._saved = self.server.TOKEN
        self.server.TOKEN = self.KNOWN

    def tearDown(self):
        self.server.TOKEN = self._saved

    def test_auth_endpoint_403_without_token(self):
        # scene_info is an auth=True endpoint (default auth=True on @endpoint).
        self.assertEqual(_call(self.server, "scene_info")["statusCode"], 403)

    def test_auth_endpoint_403_with_wrong_token(self):
        self.assertEqual(
            _call(self.server, "scene_info", headers={"X-TDMCP-Token": "nope"})["statusCode"], 403)

    def test_auth_endpoint_200_with_correct_token(self):
        # header lookup is case-insensitive
        self.assertEqual(
            _call(self.server, "scene_info", headers={"x-tdmcp-token": self.KNOWN})["statusCode"], 200)

    def test_health_stays_open_without_token(self):
        # health is served before any auth gate; open regardless of TOKEN.
        resp = self.server.handle(None, {"uri": "/health", "data": ""}, {})
        self.assertEqual(resp["statusCode"], 200)

    def test_validate_glsl_is_auth_false_and_open(self):
        # the GLSL dry-run endpoint is auth=False, so it must serve even with a token set + no header.
        resp = _call(self.server, "validate_glsl",
                     data='{"source": "#version 330\\nvoid main(){}", "stage": "pixel"}')
        self.assertEqual(resp["statusCode"], 200)

    def test_dev_reload_is_auth_true(self):
        # the most dangerous endpoint must be auth-gated -> 403 without a token.
        self.assertEqual(_call(self.server, "dev_reload")["statusCode"], 403)


if __name__ == "__main__":
    unittest.main()
