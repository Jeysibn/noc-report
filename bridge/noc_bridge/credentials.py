"""Manage the isolated Claude Code OAuth credential used by sandboxes.

The host source is normally mode 0600. The bridge never mounts the
operator's ``~/.claude`` directory. Instead it maintains a bridge-owned
credential directory containing only ``.credentials.json``. The directory
is writable by the sandbox because Claude Code must persist OAuth refresh
state, but it is isolated from the rest of the host home directory.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import tempfile


def prepare_sandbox_credentials(
    source: pathlib.Path,
    persistent_dir: pathlib.Path | None = None,
) -> pathlib.Path:
    """Return a sandbox-mountable credential directory.

    The bridge passes a persistent directory so OAuth refreshes survive
    sandbox and bridge restarts. A newer host login replaces the cache;
    otherwise a newer cached copy is retained because it may contain a
    refresh performed by Claude Code inside a previous sandbox.

    Omitting ``persistent_dir`` retains the disposable copy behavior used by
    isolated callers and tests.
    """
    if not source.exists():
        raise FileNotFoundError(
            f"Claude Code credentials not found at {source} — log in with "
            f"`claude` on this host first."
        )

    cleanup = persistent_dir is None
    tmp_dir = (
        pathlib.Path(tempfile.mkdtemp(prefix="noc-bridge-claude-creds-"))
        if cleanup
        else persistent_dir.expanduser()
    )
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.chmod(0o700)
    dest = tmp_dir / ".credentials.json"
    should_copy = not dest.exists() or source.stat().st_mtime_ns > dest.stat().st_mtime_ns
    if should_copy:
        fd, temporary_name = tempfile.mkstemp(prefix=".credentials.", dir=tmp_dir)
        os.close(fd)
        temporary = pathlib.Path(temporary_name)
        try:
            shutil.copyfile(source, temporary)
            temporary.chmod(0o600)
            os.replace(temporary, dest)
        finally:
            temporary.unlink(missing_ok=True)
    dest.chmod(0o600)
    if cleanup:
        import atexit

        atexit.register(shutil.rmtree, tmp_dir, ignore_errors=True)
    return tmp_dir
