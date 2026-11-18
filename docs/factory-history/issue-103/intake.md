DECISION: ACCEPT
RISK: high
SURFACES: security
RESCOPED: no

## Summary
- Accepted as asked: switch `builder`, `spec-writer`, `approver`, `planner` and the four `reviewer-*` agents to `tools: REPL, Poll` and leave `intake` as it is.
- `factory/bin/sync-agents` currently exits with an error on any tool name it doesn't know. It needs a mapping for `REPL` and `Poll`, or `.agents/agents/` can't be regenerated.
- The builder can't spawn agents itself, so a human or the orchestrator has to run the "does the REPL really appear" check. The PR has to report the result.
- Rated high risk: every later factory job runs on these agent definitions, and the change decides which tools (including a shell) each agent gets.

## Scope
Build:
- In `.claude/agents/`, set `tools:` to `REPL, Poll` for `builder`, `spec-writer`, `approver`, `planner`, `reviewer-code`, `reviewer-security`, `reviewer-ui` and `reviewer-ux`. Keep the planner's `mcp__plugin_figma_figma__*` entries. Keep the rest of the frontmatter as it is, including the builder's `Stop` hook (`factory/bin/gate --hook`).
- `intake.md` stays byte-for-byte unchanged: `Read, Grep, Glob, Write`, no `REPL`, no `Bash`.
- Agent prompt text that tells an agent to call Read/Grep/Glob/Edit/Write/Bash directly (for example the builder's "start every Bash command with `cd <worktree> && `" and "give Read, Edit, Write, Grep and Glob absolute paths") is reworded for Code Mode: shell through the REPL's `$`, file operations from inside the REPL, and the same rules still apply (absolute paths under the worktree, never edit the main checkout, write only the one factory file). The rules themselves don't change.
- `factory/bin/sync-agents` learns `REPL` and `Poll`. Antigravity has neither, and an unknown name can hang an agent. The sensible reading is to map `REPL` to the native tools those agents had before (`view_file`, `write_to_file`, `replace_file_content`, `multi_replace_file_content`, `grep_search`, `find_by_name`, `list_dir`, `run_command`) and to emit nothing for `Poll`. The generated note on tool names is updated to match. One side effect needs a line in the PR: in Antigravity, agents that had no Edit (approver, reviewers, spec-writer, planner) would get the edit tools. If the spec writer finds a cleaner mapping that avoids this, that is fine. Regenerate `.agents/agents/` and keep `sync-agents --check` passing.
- The PR description explains how Antigravity handles `REPL`/`Poll`, and reports two results: (a) a changed agent, when spawned, reports having `REPL` and `Poll`; (b) the builder's `Stop` hook still fires. The builder has no way to spawn a subagent, so the spec should treat (a), and (b) if needed, as a verification step done by the orchestrator or the owner on the branch. If (a) fails, the job stops and reports that instead of working around it, as the issue says.

Left out (as the issue says): permission settings, `CLAUDE.md`, and how the orchestrator spawns agents. No changes to the plates app itself.

## Why
The request is clear and specific, it comes from the repo owner, and it only touches the factory files already in this repo: `.claude/agents/*.md` (where every agent except intake lists `Bash`) and `factory/bin/sync-agents`. One thing the issue doesn't spell out: `render()` in `sync-agents` calls `die()` on any tool missing from its `TOOLS` table, so that script has to change too. Keeping intake off the shell matches its role of reading untrusted issue text.

## Questions
