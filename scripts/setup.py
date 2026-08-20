#!/usr/bin/env python3
"""
setup.py -- install helper for touchdesigner-bridge-mcp.

What it does (and NOTHING else):
  1. Builds the gateway binary with cargo (unless --no-build).
  2. Detects THIS clone's location and the built binary path.
  3. Prints a ready-to-paste claude_desktop_config.json snippet with the real
     paths for THIS machine filled in (to STDOUT -- you copy/paste it).
  4. Prints the exact TouchDesigner Textport arm command, with TDMCP_REPO set
     to this clone.

It is idempotent and makes NO changes outside this repository: the only thing it
writes is cargo's build output under gateway/target/ (inside the repo). It never
touches your Claude Desktop config, arm.json, or anything in your home directory
-- it only PRINTS text for you to paste.

Usage:
    python scripts/setup.py            # build, then print config + arm command
    python scripts/setup.py --no-build # skip cargo, just print (binary must exist)
    python scripts/setup.py --server-name td   # name the MCP server entry "td"

The binary is BOTH the MCP server (stdio, when TDMCP_GW_HEADLESS is set) and a
GUI (default launch). Claude Desktop launches it as an MCP server, so the printed
config sets TDMCP_GW_HEADLESS=1; you separately run the SAME binary with no args
to open the GUI and set the working directory.
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys


def repo_root() -> str:
    """This clone's root -- the parent of the scripts/ directory holding this file."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fwd(path: str) -> str:
    """Forward-slash a path. JSON strings and TD's exec() line both prefer '/', which
    sidesteps Windows backslash-escaping entirely."""
    return path.replace("\\", "/")


def binary_path(root: str) -> str:
    """Expected location of the built gateway binary for this OS."""
    exe = "touchdesigner-bridge-mcp.exe" if platform.system() == "Windows" else "touchdesigner-bridge-mcp"
    return os.path.join(root, "gateway", "target", "release", exe)


def run_cargo_build(root: str) -> bool:
    """cargo build --release --manifest-path <root>/gateway/Cargo.toml.
    Returns True on success. Cargo itself is idempotent (no-op when up to date)."""
    manifest = os.path.join(root, "gateway", "Cargo.toml")
    if shutil.which("cargo") is None:
        print("ERROR: 'cargo' not found on PATH. Install the Rust toolchain from "
              "https://rustup.rs and re-run, or pass --no-build if the binary "
              "already exists.", file=sys.stderr)
        return False
    if not os.path.isfile(manifest):
        print("ERROR: gateway/Cargo.toml not found at %s" % manifest, file=sys.stderr)
        return False
    print("==> Building gateway (cargo build --release)...", file=sys.stderr)
    try:
        subprocess.run(
            ["cargo", "build", "--release", "--manifest-path", manifest],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print("ERROR: cargo build failed (exit %d)." % e.returncode, file=sys.stderr)
        return False
    return True


def config_snippet(root: str, binary: str, server_name: str) -> str:
    """A claude_desktop_config.json snippet with THIS machine's real paths filled in.

    TDMCP_GW_HEADLESS makes the binary serve MCP over stdio (default launch is the
    GUI). TDMCP_REPO points the gateway at this clone so bundled reference data
    (reference/recipes.json, catalog.json) resolves deterministically."""
    entry = {
        "mcpServers": {
            server_name: {
                "command": fwd(binary),
                "env": {
                    "TDMCP_GW_HEADLESS": "1",
                    "TDMCP_REPO": fwd(root),
                },
            }
        }
    }
    return json.dumps(entry, indent=2)


def arm_command(root: str) -> str:
    """The exact line to paste into the TouchDesigner Textport (Alt+T) to arm the
    executor for this clone. Mirrors what the GUI's 'Copy arm command' button emits."""
    r = fwd(root)
    return ("import os; os.environ['TDMCP_REPO']=r'%s'; "
            "exec(open(os.path.join(os.environ['TDMCP_REPO'],'arm.py')).read())" % r)


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up touchdesigner-bridge-mcp on this machine.")
    parser.add_argument("--no-build", action="store_true",
                        help="skip the cargo build; only print config + arm command")
    parser.add_argument("--server-name", default="touchdesigner",
                        help="name for the MCP server entry (default: touchdesigner)")
    args = parser.parse_args()

    root = repo_root()
    binary = binary_path(root)

    if not args.no_build:
        if not run_cargo_build(root):
            return 1

    if not os.path.isfile(binary):
        print("ERROR: gateway binary not found at %s\n"
              "Run without --no-build, or build it manually:\n"
              "    cargo build --release --manifest-path gateway/Cargo.toml"
              % binary, file=sys.stderr)
        return 1

    print("==> Detected clone:  %s" % fwd(root), file=sys.stderr)
    print("==> Gateway binary:  %s" % fwd(binary), file=sys.stderr)
    print(file=sys.stderr)

    print("=" * 70)
    print("1. Claude Desktop config  (merge into claude_desktop_config.json)")
    print("   Windows: %APPDATA%\\Claude\\claude_desktop_config.json")
    print("   macOS:   ~/Library/Application Support/Claude/claude_desktop_config.json")
    print("=" * 70)
    print(config_snippet(root, binary, args.server_name))
    print()
    print("=" * 70)
    print("2. TouchDesigner arm command  (paste into the Textport, Alt+T)")
    print("=" * 70)
    print(arm_command(root))
    print()
    print("Next: open the GUI to set your working directory -- run the SAME binary")
    print("with NO arguments:")
    print("    %s" % fwd(binary))
    print("Then verify the executor is up:  http://127.0.0.1:9980/health")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
