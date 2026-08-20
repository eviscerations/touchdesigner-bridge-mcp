"""scripts/gen_integrity_manifest.py -- (re)generate td_executor/INTEGRITY.json, the hash-pin manifest
of the on-disk executor code (SHA-256 over RAW file bytes). Run OUTSIDE TouchDesigner, plain CPython.

    python scripts/gen_integrity_manifest.py            # write/refresh the manifest
    python scripts/gen_integrity_manifest.py --check    # CI mode: exit nonzero if the manifest is STALE

Pinned set (POSIX-relative to td_executor/): __init__.py, server.py, governor.py, handlers/__init__.py,
handlers/*.py. tests/** and the repo-root arm.py are DELIBERATELY excluded (never loaded as executor code;
arm.py is the bootstrap that CALLS the check, and it lives at the repo root, outside td_executor/). This checkout is NOT a git repo -- hashes are over the bytes
exactly as they sit on disk (no git/eol normalization). Dev edits a handler -> run this -> review the digest
diff -> the manifest update is an intentional, reviewed act.
"""
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1] / "td_executor"
# Pin EVERY top-level module (…/td_executor/*.py: __init__, server, governor, glsl_validator, and any
# future validator such as expr_validator) plus every handler. Security-critical validators live at the
# package root and MUST be hash-pinned — a weakened validator is exactly the tamper we detect.
PINNED_GLOBS = ["*.py", "handlers/*.py"]


def collect(root=ROOT):
    seen = {}
    for pat in PINNED_GLOBS:
        for p in sorted(root.glob(pat)):
            if "tests" in p.parts:          # never pin tests
                continue
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            seen[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return seen


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    files = collect()
    dest = ROOT / "INTEGRITY.json"
    if "--check" in argv:
        try:
            cur = json.loads(dest.read_text())["files"]
        except Exception as e:
            print("INTEGRITY.json missing/unreadable: %s" % e, file=sys.stderr)
            return 1
        if cur != files:
            print("INTEGRITY.json is STALE -- run: python scripts/gen_integrity_manifest.py", file=sys.stderr)
            return 1
        print("manifest up to date (%d files)" % len(files))
        return 0
    out = {"algo": "sha256",
           "files": files}
    dest.write_text(json.dumps(out, indent=2) + "\n", newline="\n")
    print("wrote %s (%d files)" % (dest, len(files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
