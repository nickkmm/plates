VERDICT: PASS

## Summary
- Eight agents now declare `tools: REPL, Poll` (planner keeps its five Figma tools in order). intake is untouched, and the builder's `hooks:` block is not in the diff.
- The tool-naming prompt lines are reworded for the REPL. The `cd <worktree> && ` rule, the absolute-path rule and the one-file rule are kept. The reviewers now say "Write the file from the REPL".
- `sync-agents` maps `REPL` to the eight native tools and `Poll` to nothing. The docstring and `NOTE` are updated, and `NOTE` still lists the Read/Write/Edit/Grep/Glob/Bash names.
- `tests/test_sync_agents.py` covers the three required cases and writes only under `tmp_path`.
- `AGENTS.md` gains the one required sentence.

## Findings
None.

## Notes
- The `__main__` guard on the Windows UTF-8 re-exec is a small change the spec didn't list. It is needed for the test to import the script, and it matches `factory/bin/job`, so it's fine.
- Several reworded paragraphs have a short line break before "folder is in the main checkout". This only affects wrapping and doesn't matter.
- The spawn probe and the Stop-hook check are left to the orchestrator or owner, as the spec says.
