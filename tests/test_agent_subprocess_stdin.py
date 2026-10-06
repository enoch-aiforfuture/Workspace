"""Agent bash/python must not inherit the server stdin.

A child that inherits an open stdin (``cat``, ``input()``) blocks until the
one-hour tool timeout. The regression holds a pipe open on fd 0 so a child
that still inherits stdin cannot see EOF.
"""

import asyncio
import os
from contextlib import contextmanager

import pytest

from core.platform_compat import IS_WINDOWS, find_bash
from src.agent_tools import subprocess_tools

_NEEDS_BASH = IS_WINDOWS and not find_bash()


@contextmanager
def _stdin_held_open():
    """Point fd 0 at a pipe whose write end stays open in this process."""
    saved = os.dup(0)
    read_fd, write_fd = os.pipe()
    try:
        os.dup2(read_fd, 0)
        os.close(read_fd)
        read_fd = -1
        yield
    finally:
        if read_fd >= 0:
            os.close(read_fd)
        os.close(write_fd)
        os.dup2(saved, 0)
        os.close(saved)


@pytest.mark.asyncio
@pytest.mark.skipif(_NEEDS_BASH, reason="Git Bash is required for the Bash tool on Windows")
async def test_bash_cat_returns_without_reading_server_stdin(monkeypatch, tmp_path):
    monkeypatch.setattr("src.tool_execution.agent_cwd", lambda: str(tmp_path))
    with _stdin_held_open():
        result = await asyncio.wait_for(
            subprocess_tools.BashTool().execute(
                "cat; echo done",
                {"subproc_env": None, "session_id": None},
            ),
            timeout=10,
        )
    assert result["exit_code"] == 0
    assert "done" in result["output"]


@pytest.mark.asyncio
async def test_python_stdin_read_is_immediate_eof(monkeypatch, tmp_path):
    monkeypatch.setattr("src.tool_execution.agent_cwd", lambda: str(tmp_path))
    with _stdin_held_open():
        result = await asyncio.wait_for(
            subprocess_tools.PythonTool().execute(
                "import sys\nprint(repr(sys.stdin.read()))\nprint('done')",
                {"subproc_env": None},
            ),
            timeout=10,
        )
    assert result["exit_code"] == 0
    assert "''" in result["output"]
    assert "done" in result["output"]
