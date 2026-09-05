# Parallel development with worktrees

Each worktree has its own Python environment, Node dependencies, local development database,
optional test database, and API/frontend ports. The lifecycle works with repository-created
worktrees and existing Codex or Claude worktrees, including detached checkouts and external paths.

## Start a work stream

```bash
./scripts/worktree-create.sh feature-auth
# Creates .claude/worktrees/feature-auth on codex/feature-auth from freshly fetched origin/main.
# An explicit base is supported; origin/<branch> is fetched, local refs are used as-is:
./scripts/worktree-create.sh follow-up codex/existing-feature
```

Run from the main checkout **or another linked worktree**. Default creation fails if the fetch
fails, rather than silently starting from stale local main. It never includes uncommitted changes.
An existing branch or directory is an error; use the provisioner for a checkout that already exists.

```bash
./scripts/worktree-provision.sh /absolute/path/to/existing/worktree
cd /absolute/path/to/existing/worktree
pnpm dev-with-fastapi
```

Setup prints the exact local web and API URLs. `pnpm dev` and `pnpm fastapi-dev` also work separately.
The combined launcher stops both process groups when interrupted or when either server exits.
No editor is launched and no workspace file is modified.

## Prerequisites

- macOS or Linux, Git, Python 3.11+, `uv`, Node **22.13+**, and the pnpm version pinned in `package.json`.
  With nvm, run `nvm install` / `nvm use` from the checkout (`.nvmrc` selects Node 22).
- Docker Desktop / Docker Compose for the existing `portfolio-postgres` service, or that local
  Postgres already running. PostgreSQL client tools (`psql`, `pg_dump`, `pg_restore`) must be on PATH
  and at least as new as the local server (currently PostgreSQL 17).
- The main checkout's root `.env` and `apps/web-react-router/.env` hold backend and frontend
  credentials. They are copied file-to-file when the worktree files are absent. Missing files do
  not block database/dependency setup, but authenticated app features still need their keys.
  See the root README and `.env.example` for obtaining integration credentials.

The pnpm 11 workspace settings keep package overrides in `pnpm-workspace.yaml`, preserving the
committed lockfile and allowing `pnpm install --frozen-lockfile`.

## What provisioning owns

State is stored as `portfolio-worktree.json` in the checkout's **private Git directory**, alongside
`portfolio-setup.log`. It never appears in `git status` and contains no credentials. Resource names
include a readable slug and a Git metadata fingerprint, so separate Codex worktrees both named
`portfolio` cannot share a database. The recorded identity survives `git worktree move`.

| Resource | Main checkout | Linked worktree |
|---|---|---|
| API / frontend ports | 8000 / 5173 | 8000 + 10 × slot / 5173 + 10 × slot |
| Development database | Existing configuration | `portfolio_wt_<identity>` on local Postgres |
| Test database | Existing pytest behavior | `portfolio_test_wt_<identity>` via the test wrapper |
| Dependencies | Existing environments | Worktree-local `.venv` and `node_modules` |

Port allocation takes a shared lock, scans **all Git-registered worktrees**, includes legacy `.env`
port settings, and skips listening ports. Assigned ports remain stable on repeat setup. Run the
provisioner before starting a newly created checkout; do not manually choose another worktree's ports.

Setup copies the **local** `portfolio` database through a consistent `pg_dump` snapshot (it never
reads the main checkout's potentially remote database URL). An absent source database or an Alembic revision absent from this branch produces a
fresh seeded database; the source is preserved. Each owned database carries an ownership comment; an existing database with the
same name but a different owner marker is refused. Migrations and the idempotent seed always run
after initial cloning and on explicit setup retries, so the source can lag the branch's schema.
Snapshot restore is transactional; failed setup remains retryable and is never marked ready.

Only missing environment files are copied. Repeat setup preserves custom values, and rewrites the
managed database/port settings without accumulating duplicate assignments. Files are written with
mode 0600. Symlinked destination `.env` files are refused to protect the source checkout. Main `.env`
files are never changed. Local `RAILWAY_ENVIRONMENT_NAME` is cleared so cloned schedules do not
activate hosted-only jobs. Other application integrations keep their existing development behavior.

Local database management is deliberately fixed to the repository's Docker Postgres on
127.0.0.1:5432. It does not create, switch, or delete Neon branches. Use the `load-prod-data` skill
when sanitized production context is needed; PR environments continue to own their Neon lifecycle.
The standalone `apps/sernia_mcp` service retains its separate setup described in its README.

## Isolated tests

```bash
./scripts/worktree-test.sh                         # full default non-live pytest suite
./scripts/worktree-test.sh api/src/tests/test_worktrees.py
```

The wrapper creates the worktree's test database on demand, migrates/seeds it, and points both
SQLAlchemy URLs at it before pytest imports the application. Development data is left alone.
Dotenv loading is disabled for this test process and dummy AI credentials are supplied; the usual
conftest defaults cover other integrations. Run live tests through the documented explicit pytest
flow with intentionally configured credentials instead. Direct `pytest` retains its existing
behavior and uses the current database configuration; use the wrapper for worktree isolation.

Lifecycle regression tests also run without application dependencies or Postgres:

```bash
python3 -m unittest discover -s api/src/tests -p test_worktrees.py
```

## Codex and Claude

[Codex local environments](https://learn.chatgpt.com/docs/environments/local-environment) run setup
when a worktree is created. `.codex/environments/environment.toml` wires macOS setup to
`worktree-provision.sh "$CODEX_WORKTREE_PATH" --once`, provides dev/test actions, and invokes
`worktree-cleanup.sh --codex "$CODEX_WORKTREE_PATH"` before Codex removes a managed checkout.
Cleanup requires that exact variable and releases only recorded local resources; Codex owns the
Git checkout deletion. The local environment configuration follows the existing Ventures setup.

Claude's SessionStart hook calls the same `--once` provisioner. It quietly skips the main checkout,
non-Git directories, cloud sessions, temporary `wf_*` worktrees, and completed setups. A failed run
is retried next time. A per-worktree lifecycle lock prevents setup, tests, and cleanup from running
over one another. Existing cloud bootstrap scripts remain responsible for their cloud containers.

## Audit and cleanup

```bash
python3 scripts/worktree.py audit
./scripts/worktree-remove.sh /exact/worktree/path --dry-run
./scripts/worktree-remove.sh /exact/worktree/path
```

Removal refuses the main/current checkout, Git-locked worktrees, dirty/untracked files, and
unmerged commits. It fetches the default branch before verifying merge ancestry. Names are accepted
only when unambiguous; old sibling names such as `portfolio-feature-auth` still resolve through
Git's registry. Legacy databases without ownership metadata are deliberately left alone.

For a squash merge, record the merge commit from inside the finished checkout, then clean it from
another checkout:

```bash
./scripts/worktree-remove.sh --mark <merge-commit-sha>
# From main or another checkout:
./scripts/worktree-remove.sh --eligible --dry-run
./scripts/worktree-remove.sh --eligible
```

The marker records both the verified merge commit and current worktree HEAD. Later commits or
uncommitted changes prevent cleanup. `--eligible` reports held worktrees and continues with others.
`DRY_RUN=1` is equivalent to `--dry-run`.

Cleanup preflights database ownership and active connections, and refuses listening development
ports. Stop servers first. It never terminates someone else's database connections, force-deletes
files, or drops a shared database. Database failures stop removal so ownership metadata remains
available for a retry. Git removes the checkout; the local branch is retained for recovery and can
be deleted separately after review.

## Troubleshooting

- **Node version error:** activate Node 22 (`nvm use`) before invoking pnpm or the provisioner.
- **Install/migration failure:** setup prints the private log path. Inspect it locally without
  copying credentials into chats. Resolve the dependency/schema error and rerun provisioning.
- **Snapshot error:** check `pg_dump --version` against the local Postgres version. Restore runs in
  a transaction, and the next setup retries the snapshot before migrating.
- **Postgres unavailable:** start Docker Desktop, rerun setup; never substitute a remote URL.
- **Cleanup held:** save/commit work, stop the worktree's servers, or supply the verified squash
  merge commit. Do not bypass the ownership checks with `rm -rf` or `dropdb`.
