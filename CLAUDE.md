# Naming convention (non-negotiable)

**Everything that is code/schema structure is named in English.** This
applies to, without exception:

- Database table names and column names (`customers`, `users`,
  `payment_method`, `customer_id` — never `clientes`, `usuarios`,
  `forma_pago`, `cliente_id`).
- File and directory names (`docs/DATABASE.md`, `app/schema_migrate.py`).
- Python identifiers: modules, classes, functions, variables, constants
  (`resolve_or_create_customer`, `PAYMENT_METHODS`).
- Git branch names, commit-adjacent identifiers.

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
  Existing role values (`admin`, `producción`, `consulta`) follow the same
  rule — the column and the constant that validates it are English, the
  values are Spanish business terms.
- Code comments explaining a Spanish business rule may quote the Spanish
  term the rule is about.

**Rule of thumb**: if it's something a developer reads (a table name, a
column, a variable, a filename), it's English. If it's something a
Colombian staff member reads or types (a button label, a dropdown value,
a report), it's Spanish. When genuinely unsure which side something falls
on, ask rather than guess.
