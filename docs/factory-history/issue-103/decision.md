ESCALATE

## Summary
- Eight factory agents (builder, spec-writer, approver, planner, four reviewers) now declare `tools: REPL, Poll`. The planner keeps its Figma tools. intake is byte-for-byte unchanged and still has no shell.
- Prompt text was reworded for the REPL. The rules stay the same: `cd <worktree> && `, absolute paths, one factory file.
- `sync-agents` maps REPL to the eight Antigravity native tools and drops Poll. `.agents/agents/` was regenerated, and a new test covers the mapping.
- Gate PASS (1839 tests). Code and security reviews both PASS with no findings.
- Two runtime checks are still open on the PR: (a) a spawned agent really gets REPL/Poll, and (b) the builder's Stop hook still fires. The builder couldn't run them.

## Why
The diff is clean, stays inside intake's Scope, and meets every acceptance criterion a file can show. But this change decides which tools, including a shell, every later factory agent gets. That is an access/permissions change. Whether the change works at all depends on the spawn probe (a), which the PR says is a do-not-merge condition and which is still unchecked.

## Acceptance criteria
1. tools lines: met. I ran `grep -n '^tools:' .claude/agents/*.md`: seven agents show `REPL, Poll`, the planner shows `REPL, Poll` plus the five Figma tools in order, and intake shows `Read, Grep, Glob, Write`.
2. intake.md has no diff: met. I checked that `git diff origin/main -- .claude/agents/intake.md` is empty.
3. builder hooks block untouched: met. I read the diff: only line 4 and lines 22-24 change, and the `Stop`/`gate --hook` lines are context only.
4. Old tool-naming phrases remain only in intake: met. I ran the grep and it matches only `intake.md:25`.
5. The `$` rule is reworded with `cd <worktree> && `: met. I saw it in the builder diff, and build.md and review-code confirm it for the others.
6. `sync-agents --check`: met. I ran it and it prints `9 agents up to date`.
7. Antigravity builder/reviewer-code tool lists: met, according to build.md and the gate's sync check.
8. Antigravity intake still has no run_command: met, according to build.md and review-security.
9. The generated note mentions REPL and Poll: met. The NOTE string in the diff covers both.
10. `tests/test_sync_agents.py` passes and covers 3 cases: met. The gate's full pytest run passed, and review-code confirms the coverage.
11. AGENTS.md sentence: met, according to build.md and review-code.
12. PR body sections: met. I checked PR #104: it has the Antigravity explanation, the side-effect note, unchecked items (a)/(b) and the do-not-merge-if-(a)-fails line.

## For the human
Recommendation: don't merge yet

Reasons:
- This changes access: which tools every factory agent except intake gets, including a shell through the REPL's `$`. Only you should approve that.
- PR #104's "Verification by the orchestrator/owner" items are both unchecked. Start a session on branch `factory/015-give-factory-agents-code` and spawn one changed agent (for example `reviewer-code`). Confirm it reports `REPL` and `Poll`. If it doesn't, don't merge; report it instead of adding `Bash` back. Then run the builder once and confirm its `Stop` hook (`factory/bin/gate --hook`) still fires.
- The security reviewer raised an open question. Check whether the REPL is covered by the same `.claude/settings*.json` allow/deny rules that applied to Bash/Edit/Write. If the REPL gets around deny rules, agents can do more than before.
- Side effect you accept by merging: in Antigravity, the approver, reviewers, spec-writer and planner now also get the edit tools. They already had `run_command`, so in practice they gain nothing new.

Files to look at:
- `.claude/agents/builder.md` (tools line, hooks block, reworded "Where you work")
- `.claude/agents/approver.md`, `.claude/agents/reviewer-*.md`, `.claude/agents/spec-writer.md`, `.claude/agents/planner.md`
- `factory/bin/sync-agents` (REPL/Poll mapping, NOTE, `__main__` guard on the Windows re-exec)
- `tests/test_sync_agents.py`
- `.agents/agents/intake.md` (must still lack `run_command`)
