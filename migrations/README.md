# Database migrations

Numbered SQL scripts in this directory are applied in lexicographic order by
`Database._apply_migrations()` after the base schema, before the app starts.

Naming: `NNNN_description.sql` (e.g. `0002_add_messages_index.sql`).

Rules:
- Scripts must be idempotent where possible (`IF NOT EXISTS` / `IF EXISTS`).
- Each script runs via `executescript`; applied scripts are tracked in
  `schema_metadata` under key `applied_migrations`.
- Bump `CURRENT_SCHEMA_VERSION` in `src/aphrodite/db/schema.py` whenever a
  migration is added. Older databases are upgraded automatically; databases
  from a NEWER version are refused with a clear error.

This directory is packaged into the wheel via `force-include` in
`pyproject.toml` so migrations work identically in installed installs.
