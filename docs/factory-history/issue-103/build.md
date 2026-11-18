STATUS: READY

# Build: Code Mode (REPL, Poll) for every factory agent except intake

## Summary
- builder, spec-writer, approver, planner and the four reviewers now declare `tools: REPL, Poll` (planner keeps its Figma MCP tools); intake is byte-for-byte unchanged.
- Prompt lines naming Read/Grep/Glob/Edit/Write/Bash as tools are reworded for the REPL; the `cd <worktree> && ` and absolute-path rules are kept.
- `sync-agents` maps `REPL` to the eight Antigravity native tools and drops `Poll`; `.agents/agents/` regenerated; new `tests/test_sync_agents.py`.
- Draft PR #104 carries the Antigravity explanation, the side-effect note and the unchecked spawn-probe / Stop-hook verification items for the orchestrator/owner.

## Changes
- `.claude/agents/{builder,spec-writer,approver,reviewer-code,reviewer-security,reviewer-ui,reviewer-ux}.md`: `tools: REPL, Poll`; the "Where you work" sentence now says to start every shell command run through the REPL's `$` with `cd <worktree> && ` and to read/search (builder: also edit/write) files from the REPL with absolute paths under the worktree. Reviewers: "Write the file from the REPL before you reply: ...".
- `.claude/agents/planner.md`: `tools: REPL, Poll,` + the same five `mcp__plugin_figma_figma__*` tools in order. Its body names no tools, so no prompt change.
- `factory/bin/sync-agents`: `TOOLS["REPL"]` = the eight native tools, `TOOLS["Poll"] = []`; docstring and `NOTE` explain it (NOTE still lists Read/Write/Edit/Grep/Glob/Bash). Added the `__name__ == "__main__"` guard to the Windows UTF-8 re-exec, as `factory/bin/job` already has; without it, importing the script from pytest on Windows re-runs pytest in a subprocess.
- `.agents/agents/*.md`: regenerated with `factory/bin/sync-agents`.
- `tests/test_sync_agents.py`: loads the script via `SourceFileLoader`, renders temp agents under `tmp_path`: REPL/Poll mapping, `mcp__` dropped, unknown tool raises `SystemExit`.
- `AGENTS.md`: one sentence in the Antigravity bullet about Code Mode and the sync-agents mapping.

## Acceptance criteria
1. [x] `grep -n '^tools:' .claude/agents/*.md`: `tools: REPL, Poll` for the seven agents, planner line is `tools: REPL, Poll, mcp__plugin_figma_figma__create_session, ..._get_metadata, ..._get_design_context, ..._get_screenshot, ..._get_variable_defs`, intake `tools: Read, Grep, Glob, Write`.
2. [x] `git diff origin/main -- .claude/agents/intake.md` prints nothing.
3. [x] The builder.md diff only touches line 4 and lines 22-24; the `hooks:`/`Stop:` lines appear only as context. `grep -n "gate --hook"` finds line 10.
4. [x] `grep -nE "Bash command|Write tool|give Read|Read, Edit|Read, Grep and Glob" .claude/agents/*.md` matches only `intake.md:25`.
5. [x] `grep -n '\$' .claude/agents/builder.md` shows line 23: "REPL's `$` with `cd <worktree> && `, and read, search, edit and write files"; the same phrase is found once in approver, spec-writer and all four reviewers.
6. [x] `python factory/bin/sync-agents --check` prints `sync-agents: 9 agents up to date`.
7. [x] `.agents/agents/builder.md` and `reviewer-code.md` list view_file, write_to_file, replace_file_content, multi_replace_file_content, grep_search, find_by_name, list_dir, run_command; `grep -nE "^  - (REPL|Poll)$" .agents/agents/*.md` prints nothing.
8. [x] `.agents/agents/intake.md` lists exactly view_file, grep_search, find_by_name, list_dir, write_to_file (no run_command).
9. [x] `grep -L 'REPL.*Poll' .agents/agents/*.md` prints nothing: every generated note mentions both.
10. [x] `.venv/Scripts/python -m pytest -q tests/test_sync_agents.py`: 3 passed (the three cases).
11. [x] `AGENTS.md` Antigravity bullet contains "The Claude Code agents except intake run in Code Mode (`tools: REPL, Poll`); `sync-agents` gives their Antigravity copies the native file tools and `run_command` in its place."
12. [x] PR #104 body has "How Antigravity handles REPL and Poll" (mapping + Poll dropped + side-effect note) and "Verification by the orchestrator/owner" with unchecked items (a) and (b) and the do-not-merge-if-(a)-fails note.

Not verifiable by the builder (left to the orchestrator/owner, as the spec says): whether a spawned agent actually gets REPL/Poll, and whether the builder's Stop hook still fires.

## Validation
- `bash factory/checks.sh` from the worktree: passed (651 passed, 1188 skipped because the local Postgres at 5433 was not running; shell and factory script checks passed).
- `.venv/Scripts/python -m pytest -q tests/test_sync_agents.py`: passed.
- `python factory/bin/sync-agents --check`: passed.

## Log
### Round 1
- Set up `.venv` with `requirements-dev.txt`.
- Changed the eight agents' tools lines and tool-naming prompt text (files are CRLF in the working tree; edits kept CRLF).
- Added the REPL/Poll mapping to `sync-agents`, updated its docstring and NOTE, regenerated `.agents/agents/`.
- Found that `sync-agents` re-execs itself on import under Windows; added the `__main__` guard that `factory/bin/job` uses so the new test can load it.
- Added `tests/test_sync_agents.py` and the AGENTS.md sentence; committed in three commits, pushed, opened draft PR #104.
