#!/usr/bin/env python3
"""scripts/check_all.py -- the single "run all checks" entrypoint for touchdesigner-bridge-mcp.

Runs every local gate CI runs, in sequence, and prints a clear PASS/FAIL summary. This is the
command CONTRIBUTING references before pushing:

    python scripts/check_all.py            # run everything
    python scripts/check_all.py --no-rust  # skip the (slow) cargo test + release build

Checks, in order:
  1. Rust tests        cargo test  --manifest-path gateway/Cargo.toml
  2. Rust release build cargo build --release --manifest-path gateway/Cargo.toml
  3. Python executor tests   python td_executor/tests/run_tests.py
  4. Registry consistency    python scripts/audit_registry_consistency.py
  5. Recipe integrity        python scripts/validate_recipes.py
  6. Integrity manifest      python scripts/gen_integrity_manifest.py --check  (fails if stale)

Every check runs even if an earlier one fails (so one invocation surfaces all problems); the process
exits nonzero if ANY check failed. Commands are invoked with the current interpreter (sys.executable)
and cwd pinned to the repo root, so it works regardless of where you launch it from.
"""
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
PY = sys.executable or "python"


def _checks(include_rust=True):
    checks = []
    if include_rust:
        checks += [
            ("Rust tests", ["cargo", "test", "--manifest-path", "gateway/Cargo.toml"]),
            ("Rust release build",
             ["cargo", "build", "--release", "--manifest-path", "gateway/Cargo.toml"]),
        ]
    checks += [
        ("Python executor tests", [PY, "td_executor/tests/run_tests.py"]),
        ("Registry consistency", [PY, "scripts/audit_registry_consistency.py"]),
        ("Recipe integrity", [PY, "scripts/validate_recipes.py"]),
        ("Integrity manifest (--check)",
         [PY, "scripts/gen_integrity_manifest.py", "--check"]),
    ]
    return checks


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    include_rust = "--no-rust" not in argv

    results = []
    for name, cmd in _checks(include_rust):
        print("\n" + "=" * 72)
        print("RUN  %s" % name)
        print("     $ %s" % " ".join(cmd))
        print("=" * 72, flush=True)
        try:
            rc = subprocess.call(cmd, cwd=REPO)
        except FileNotFoundError as e:
            print("  !! command not found: %s" % e, file=sys.stderr)
            rc = 127
        results.append((name, rc))

    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    failed = 0
    for name, rc in results:
        status = "PASS" if rc == 0 else "FAIL (exit %d)" % rc
        if rc != 0:
            failed += 1
        print("  %-32s %s" % (name, status))
    print("-" * 72)
    if failed:
        print("RESULT: FAIL  (%d of %d checks failed)" % (failed, len(results)))
        return 1
    print("RESULT: PASS  (%d checks)" % len(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
