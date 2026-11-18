VERDICT: PASS

## Summary
- Eight factory agents move from named tools to `tools: REPL, Poll`; intake, which reads untrusted issue text, stays byte-for-byte unchanged and still has no shell.
- `sync-agents` maps `REPL` to the Antigravity native file tools plus `run_command`, drops `Poll`, and still dies on unknown names, so the allow-list is still enforced.
- The Antigravity read-only agents (approver, reviewers, spec-writer, planner) now get edit tools, but they already had `run_command`, so they can't do anything new.
- No app code, routes, data handling, secrets, dependencies or protected files touched; the builder's `gate --hook` Stop block is intact.

## Findings
None.

## Notes
- Trust boundary: the most important property is that intake, the agent that reads raw issue text, does not get REPL/shell. Verified: `.claude/agents/intake.md` has no diff and `.agents/agents/intake.md` still lists no `run_command`. Keep it that way in future changes.
- The `__name__ == "__main__"` guard added to the Windows UTF-8 re-exec in `factory/bin/sync-agents` only changes behavior on import (tests). It matches `factory/bin/job` and doesn't change CLI behavior.
- Whether Code Mode's REPL is covered by the same permission rules (`.claude/settings*.json`) that applied to Bash/Edit/Write is something the spec leaves to the orchestrator/owner's spawn probe. If REPL gets around deny rules the old tools respected, that would widen what an agent can do. Worth checking during verification item (a). It's outside this diff's scope.
- No `.env`, SSH keys, `dozzle_users.yml`, `data/` or `*.xlsx` in the change. The new test writes only under `tmp_path`.
