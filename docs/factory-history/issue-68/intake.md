DECISION: ACCEPT
RISK: low
SURFACES: none
RESCOPED: no

## Summary
- Accepted as written: a docs-only rewrite of `README.md`, plus the intro paragraph of `docs/README.md`.
- All four dependencies (#60, #61, #66, #67) have landed on `main`. The features the README must describe are in the code: `/usuarios`, the production board, notarization, CodeQL, Dependabot and the protected deploy.
- The current README is out of date. It says there is no admin UI for users, and it lists the deploy secrets with `root` as the SSH user.
- No code, schema or business-rule changes, so the risk is low.

## Scope
Rewrite `README.md` in English with the eleven sections the issue lists, in that order: pitch, Features grouped by role, How it works (mermaid lifecycle diagram plus an architecture paragraph), Tech stack, Quality, CI/CD, Local development, Software factory, Documentation, Security, License / status. Describe only what is on `main` today. Every route, screen, command and file it names must exist in the repo, and every relative link must resolve.

Remove the "Required GitHub secrets" table and any host name, IP, username, `/opt/...` path or secret name. `docs/ARCHITECTURE.md` already names the secrets, so nothing has to move there. Acceptance criterion 3 greps for the bare word `root`, so the README must avoid it everywhere, including phrases like "repo root".

Also replace the stale intro of `docs/README.md`, which still describes three fixed roles with production read-only. The new intro is two or three sentences of Spanish prose on the current permission model and the production flow, and the rest of that file stays as it is.

Left out, as the issue says: screenshots and GIFs, adding a LICENSE file or choosing a license, any code change, and edits to other docs.

## Why
This is a well-specified documentation task with checkable acceptance criteria. Its prerequisites are on `main`: `app/web/blueprints/users.py`, `production.py`, `declarations.py`, `public_declaration.py`, `.github/workflows/codeql.yml` and `.github/dependabot.yml` all exist. The current `README.md` contradicts that code, and its secrets table (lines 98-105) exposes deploy details the issue wants removed.

## Questions
