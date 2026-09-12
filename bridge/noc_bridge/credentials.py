"""Prepares a sandbox-mountable copy of the operator's Claude Code OAuth
credential (master plan §28: "use minimum required credentials/config",
"never mount general host home directory").

The source `.credentials.json` is normally mode 0600, owned by the host
user — unreadable by the sandbox's own non-root uid (10001) once bind-
mounted as-is, since Docker bind mounts preserve host ownership/perms
verbatim. Rather than loosening the real file's permissions or mounting
the operator's actual `~/.claude` directory, this copies just the one
file into a fresh, process-owned temp directory with relaxed (but still
not world-writable) permissions, and only that copy is ever mounted into
a container.
"""
from __future__ import annotations

import atexit
import pathlib
import shutil
import tempfile


def prepare_sandbox_credentials(source: pathlib.Path) -> pathlib.Path:
    if not source.exists():
        raise FileNotFoundError(
            f"Claude Code credentials not found at {source} — log in with "
            f"`claude` on this host first."
        )

    tmp_dir = pathlib.Path(tempfile.mkdtemp(prefix="noc-bridge-claude-creds-"))
    # mkdtemp defaults to 0700, owned by the host user — that alone blocks
    # the sandbox's uid 10001 from traversing into the directory at all,
    # regardless of the file's own mode. 0755 lets any uid traverse/list
    # while still leaving only the host user able to write into it.
    tmp_dir.chmod(0o755)
    dest = tmp_dir / ".credentials.json"
    shutil.copy2(source, dest)
    dest.chmod(0o644)  # readable by the sandbox's uid 10001, still not group/world-writable
    atexit.register(shutil.rmtree, tmp_dir, ignore_errors=True)
    return tmp_dir
