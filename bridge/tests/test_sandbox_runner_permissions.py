"""Regression tests for Skill Runtime mission Phase 17: the sandbox's
mounted input/output directories must grant the container's fixed non-root
uid (10001) only the "other" bits it actually needs, never a blanket
world-writable/readable 0o777/0o755 and never any "group" bits.
"""
from __future__ import annotations

import stat
import tempfile
from pathlib import Path

from noc_bridge.sandbox_runner import _grant_sandbox_uid_access


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_input_dir_grants_other_read_and_traverse_but_not_write():
    with tempfile.TemporaryDirectory() as input_dir, tempfile.TemporaryDirectory() as output_dir:
        input_path = Path(input_dir)
        (input_path / "log.txt").write_text("hello")

        _grant_sandbox_uid_access(input_path, Path(output_dir))

        assert _mode(input_path) == 0o705
        assert _mode(input_path / "log.txt") == 0o604


def test_output_dir_grants_other_write_and_traverse_but_not_read():
    with tempfile.TemporaryDirectory() as input_dir, tempfile.TemporaryDirectory() as output_dir:
        _grant_sandbox_uid_access(Path(input_dir), Path(output_dir))

        mode = _mode(Path(output_dir))
        assert mode == 0o703
        assert not (mode & stat.S_IROTH)  # other=read must NOT be granted
        assert mode & stat.S_IWOTH and mode & stat.S_IXOTH  # other=write+traverse required


def test_neither_directory_grants_any_group_bit():
    with tempfile.TemporaryDirectory() as input_dir, tempfile.TemporaryDirectory() as output_dir:
        input_path = Path(input_dir)
        (input_path / "log.txt").write_text("hello")
        output_path = Path(output_dir)

        _grant_sandbox_uid_access(input_path, output_path)

        for path in (input_path, input_path / "log.txt", output_path):
            mode = _mode(path)
            assert mode & 0o070 == 0, f"{path} unexpectedly grants a group bit: {oct(mode)}"
