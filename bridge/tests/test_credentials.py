"""Regression tests for Skill Runtime mission Phase 17: the sandbox-mounted
copy of the operator's Claude Code OAuth credential must grant only the
"other" bits the sandbox's fixed non-root uid (10001) actually needs to
read it, never any "group" bit and never write access to anyone but the
host process that owns the copy.
"""
from __future__ import annotations

import stat
import tempfile
from pathlib import Path

from noc_bridge.credentials import prepare_sandbox_credentials


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_prepared_credentials_grant_other_read_only_no_group_bits():
    with tempfile.TemporaryDirectory() as source_dir:
        source = Path(source_dir) / ".credentials.json"
        source.write_text('{"token": "secret"}')
        source.chmod(0o600)

        tmp_dir = prepare_sandbox_credentials(source)
        try:
            dest = tmp_dir / ".credentials.json"
            assert _mode(tmp_dir) == 0o705
            assert _mode(dest) == 0o604
            assert _mode(dest) & stat.S_IWOTH == 0  # not world-writable
            assert _mode(dest) & 0o070 == 0  # no group bits
        finally:
            import shutil

            shutil.rmtree(tmp_dir, ignore_errors=True)


def test_prepare_sandbox_credentials_raises_when_source_missing():
    import pytest

    with tempfile.TemporaryDirectory() as source_dir:
        missing = Path(source_dir) / "does-not-exist.json"
        with pytest.raises(FileNotFoundError):
            prepare_sandbox_credentials(missing)
