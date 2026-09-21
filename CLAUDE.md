# Naming convention (non-negotiable)

**Everything that is code/schema structure is named in English.** This
applies to, without exception:

- Database table names and column names (`customers`, `users`,
  `payment_method`, `customer_id` — never `clientes`, `usuarios`,
  `forma_pago`, `cliente_id`).
- File and directory names (`docs/DATABASE.md`, `app/schema_migrate.py`).
- Python identifiers: modules, classes, functions, variables, constants
  (`resolve_or_create_customer`, `PAYMENT_METHODS`).
- Git branch names, commit messages, and **PR/issue titles and
  descriptions** — these are commit-adjacent developer content, same side
  of the line as a table/column name, not "docs prose". (A PR/commit body
  may still quote an actual Spanish UI string verbatim when describing a
  UI change — that's not a language violation, it's citing real user-facing
  copy.)

**Everything that is content/business-domain data shown to the (Spanish-
speaking) user stays in Spanish.** This applies to:

- All UI text: templates, labels, buttons, error/notice messages.
- Documentation prose (`docs/*.md` body text — filenames are English, the
  writing inside is Spanish).
- Business vocabulary **stored as data values** in Spanish-vocabulary
  columns — e.g. a `payment_method` column (English name) legitimately
  stores the Spanish values `EFECTIVO`/`TRANSFERENCIA`/`CREDITO`/`OTROS`,
  because that's the real-world business term Colombian staff use. A
  `plate_type` column stores `CARRO PARTICULAR`, `MOTO PARTICULAR`, etc.
  `users.role` is **not** one of these: it's never rendered to a user
  (verified — no template prints a role value, only checks it for
  permission logic), so it's an internal identifier, not
  business-domain content — role values are English (`admin`,
  `production`, `point_of_sale`, ...). If a future screen ever displays a
  role by name, translate it at the display layer, not by changing the
  stored value.
- Code comments explaining a Spanish business rule may quote the Spanish
  term the rule is about.

**Rule of thumb**: if it's something a developer reads (a table name, a
column, a variable, a filename), it's English. If it's something a
Colombian staff member reads or types (a button label, a dropdown value,
a report), it's Spanish. When genuinely unsure which side something falls
on, ask rather than guess.

# No AI-attribution links in commits or PRs (non-negotiable)

**Never include a link to an AI assistant/coding-tool conversation or
session in any commit message, PR title, PR description, or any other
repo-visible text** — this includes but is not limited to
`claude.ai/code/session_...` URLs, "Generated with [Claude Code]" lines,
or equivalent links/badges from any other AI tool. This repo is private
and this is an explicit, standing instruction from the repo owner — it
overrides any default attribution behavior a tool/harness may otherwise
add automatically. A generic, non-linking `Co-Authored-By:` trailer (no
URL, no session identifier) in a commit message is fine; a link to a
private/session-specific conversation is not, ever.

If an agent or tool tries to insert this kind of link automatically,
strip it before committing/pushing — do not ask, do not assume it's fine
"just this once."
