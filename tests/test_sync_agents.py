"""Tests for factory/bin/sync-agents' tool-name mapping.

They render temporary agent files only; nothing is written to .agents/ or
.claude/."""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

SYNC_SCRIPT = Path(__file__).resolve().parents[1] / "factory" / "bin" / "sync-agents"

REPL_TOOLS = [
    "view_file",
    "write_to_file",
    "replace_file_content",
    "multi_replace_file_content",
    "grep_search",
    "find_by_name",
    "list_dir",
    "run_command",
]


def _load_sync_agents():
    loader = importlib.machinery.SourceFileLoader("factory_sync_agents", str(SYNC_SCRIPT))
    spec = importlib.util.spec_from_loader("factory_sync_agents", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


sync_agents = _load_sync_agents()


def _agent(tmp_path: Path, tools: str) -> Path:
    path = tmp_path / "probe.md"
    path.write_text(
        f"---\nname: probe\ndescription: A probe agent.\ntools: {tools}\nmodel: opus\n---\n\nBody.\n"
    )
    return path


def _tools(text: str) -> list[str]:
    head = text.split("\n---\n", 1)[0]
    after = head.split("tools:\n", 1)[1]
    return [line.removeprefix("  - ") for line in after.splitlines() if line.startswith("  - ")]


def test_repl_and_poll_become_the_native_tools(tmp_path):
    tools = _tools(sync_agents.render(_agent(tmp_path, "REPL, Poll")))
    assert tools == REPL_TOOLS
    assert "Poll" not in tools and "REPL" not in tools


def test_mcp_tools_are_still_dropped(tmp_path):
    tools = _tools(sync_agents.render(_agent(tmp_path, "REPL, Poll, mcp__plugin_figma_figma__get_metadata")))
    assert tools == REPL_TOOLS


def test_unknown_tool_still_exits(tmp_path):
    with pytest.raises(SystemExit):
        sync_agents.render(_agent(tmp_path, "REPL, Teleport"))
