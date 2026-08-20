"""Offline executor test runner (no TouchDesigner required). Mirrors the Houdini bridge's
tests/executor/run_tests.py, but the TD executor reaches the scene only through server.bind(), so a
tiny fake scene (tests/_tdmock.py) stands in for TD's op/root/app -- no DCC, no license.

    python td_executor/tests/run_tests.py          # from the repo root
    # or, equivalently:
    python -m unittest discover -t . -s td_executor/tests -p "test_*.py"

Exits non-zero if any test fails.
"""
import os
import sys
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def main():
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=os.path.dirname(os.path.realpath(__file__)),
                            top_level_dir=_REPO, pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
