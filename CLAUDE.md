# tgbackup

Self-hosted application that keeps local folders or rclone remotes mirrored inside private
Telegram channels. Uploads new files, deletes from the channel the ones that disappeared,
re-uploads the modified ones, splits files above the threshold and can reassemble them.

## Operating rules (binding)

**Everything in English.** Interface, code, comments, docstrings, log messages, error messages,
commit messages, documentation. No exceptions, including when the request that triggered the work
was written in another language.

**No test environment.** No test suite, no staging. Production code is written directly and put in
execution. Do not waste time trying things out: just write it. CI does not contradict this: it
builds, type-checks, lints and boots the backend, and it runs the migrations against a database
that already holds rows. It asserts nothing about what a job does with a file, because that would
need Telegram and a remote.

**No emoji.** Nowhere: interface, code, comments, logs, commits, documentation. Icons come from
`lucide-react` (SVG).

**Never write to the user's data.** Folders to back up are mounted `:ro`.

**No destructive tests against production.** There is no separate environment, so before writing or
deleting anything through the APIs (rclone configuration, accounts, jobs) check whether a real value
is already there. A user's rclone configuration was lost exactly this way. Verifications that write
run in a throwaway container: `docker run --rm ... tgbackup-backend python ...` with a temporary
`DATA_DIR`.

**Careful with `docker compose up -d <service>`**: because of `depends_on` it also restarts the
backend and interrupts running jobs. Use `--no-deps`.

## Stack

Backend: Python 3.14 Alpine, FastAPI, SQLAlchemy 2.0 async on SQLite, Alembic 1.18, Telethon 1.44,
cryptg, rclone 1.74 (official static binary).
Frontend: React 19, Vite 8, TypeScript 7, react-router 8, TanStack Query 5, lucide-react.
Infra: docker compose with `backend`, `frontend` (nginx), `cloudflared`.
CI: GitHub Actions, Ruff 0.16 for the backend, Trivy for vulnerabilities.

## Layout

```
backend/
  alembic.ini
  alembic/    env.py versions/ (0001_baseline.py, ...)
  app/
    config.py db.py models.py schemas.py security.py deps.py migrate.py main.py
    api/       auth accounts jobs files dashboard rclone ws
    telegram/  manager.py (client registry, peers) fast_transfer.py (parallel upload/download)
    rclone/    client.py (streaming lsjson, ranged cat, preview, config on disk)
    sync/      source.py scanner.py runner.py scheduler.py restore.py progress.py
frontend/src/
  pages/     Login ChangePassword Dashboard Jobs Accounts Files Runs Settings
  components/ Shell ui JobActivity RemoteBrowser
  lib/       api auth progress types format theme
.github/
  workflows/ ci.yml trivy.yml
  scripts/   check_migrations.py
ruff.toml .trivyignore
```

## Key concepts

**File identity**: `(rel_path, size, mtime_ns)`. The content is never read to detect changes: a
single `stat` locally, the `ModTime` field of `lsjson` on remotes. If size or mtime change, the file
is deleted from Telegram and re-uploaded. A rename is a `to_delete` plus a `pending`.

**Split**: 8000 parts of 512KB is the MTProto ceiling, that is 3.90625 GiB. Default 3.9e9 bytes for
Premium accounts, 1.9e9 for the others, detected from `get_me().premium`.

**Parallel upload**: `fast_transfer.py` opens up to 20 `MTProtoSender` on the same auth key; beyond
20 per data center Telegram blocks them all. The reader is sequential and the senders work in
parallel, so a slice of a large file is uploaded without temporary files. Photos and videos always
go as documents (`force_document=True`), never recompressed.

**Connection budget**: the 20 connections are per data center, not per job. `max_concurrent_jobs` on
the account divides the budget: with 2 concurrent jobs each gets 10. Raising it does not increase
total bandwidth, it spreads the same bandwidth across more jobs.

**Sources**: `sync/source.py` hides local folders and rclone remotes behind the same interface
(`list_files`, `reader`). The uploader does not know where the bytes come from.

**rclone without mount**: `lsjson -R` to list, `cat --offset --count` for slices. Measured on a
remote with 12 TB and 16,709 files: listing 11.6 s against roughly 5 minutes walking the FUSE mount,
reading 43.5 MB/s against 22 MB/s. No disk staging. Verified that bytes read through `cat` are
identical to those read from the mount.

**lsjson read as a stream**: `_stream_lsjson` parses one object per line as it arrives instead of
waiting for the whole array. Two consequences. With `max_items` the process is killed as soon as
there are enough entries, so `check_remote` (1 entry) and `preview` (20) cost the same on a folder
with 200,000 files as on an empty one; the non-zero exit that follows the kill is expected and must
not be treated as an error. With `on_item` and no `max_items` the caller collects while reading, and
the job scan reports files found and current folder as it goes. Nothing is kept twice: when the
caller collects through `on_item`, the internal list stays empty. `preview` sorts only what it
received, folders first, and this is deliberately not a global sort of the folder.

**rclone timeouts** (`config.py`): they are safety nets against a remote that never answers, not work
limits. `rclone_list_timeout` is 6 h because a full listing legitimately takes that long, check and
preview 10 min although they stop on their own long before.

**rclone.conf lifecycle**: the database is the source of truth. The file on disk exists only because
rclone wants a file: it is rewritten from the encrypted row at every startup (`main.py`) and chmod
`0600`. Saving from the interface validates by running `listremotes` right after writing, so a broken
configuration is rejected at save time rather than at three in the morning by a job. `check_remote`
runs again when a job is created or its source changes.

**`RemoteSliceReader.read` loops**: `StreamReader.read(n)` returns whatever it has at that moment,
which on a network pipe is almost always less than asked. Without the loop that fills the buffer,
short parts would go to Telegram, and every part but the last must be full. On exit a non-zero return
code only counts when fewer bytes were read than requested: closing early after having everything is
normal.

**rclone stderr is rewritten before being shown**: lines arrive as
`2026/07/25 22:10:43 ERROR : message`; date, level and the `Failed to x:` prefix say nothing to the
user and hide the actual message, so `_clean_error` strips them, dedupes and truncates to 400 chars.

**Telegram peers**: built from the stored `tg_id` plus `access_hash`, never resolved with
`get_entity` on a numeric id. A bare positive integer is read as a user, and `StringSession` does not
keep the entity cache, so resolution would fail after a restart.

**Scheduler**: asyncio supervisor with a 10 s tick. The `running` state lives in the database, so the
same job never overlaps itself even if it runs for days. `next_run_at` is computed from the end of
the run. If a job is stopped by process shutdown, `next_run_at` stays unchanged and the job resumes
on restart instead of slipping a whole interval. The account semaphore covers only the upload phase:
scanning and cleanup run in parallel, and queued jobs show a `waiting` phase.

**Durability**: every part is recorded in the database as soon as its message is sent. A crash in the
middle of a large file leaves no orphan messages: on the next scan the file goes through `stale`, the
already sent parts are deleted from the channel and the file restarts clean.

**Secrets**: `api_hash`, Telegram sessions and `rclone.conf` are encrypted with Fernet derived from
`APP_SECRET`. Changing `APP_SECRET` makes them unreadable. The `rclone.conf` is returned in clear to
the browser only from `GET /api/rclone/content`, that is only when Edit is pressed.

## Settled decisions, do not reopen without rechecking

**Telethon 1.44, not v2** (checked 2026-07-25). v2 was never released: 242 releases on PyPI, none
2.x. The `v2` branch declares `2.0.0a0`, the repository default stays `v1`, and the last commit on v2
replaces the MTProto sender with a Rust implementation. On top of that v2's upload is not parallel,
so it would bring no benefit: the parallelism comes from `fast_transfer.py` using v1 internals that
have been stable for years.

**cryptg must be compiled**: it publishes no musllinux wheel, so the Dockerfile has a builder stage
with Rust. Without cryptg the AES-IGE runs in pure Python and the upload becomes CPU-bound.

**nginx listens on 80 and 8081**: the Cloudflare tunnel ingress points at `frontend:8081`, while the
8081 published on the host maps to the internal 80.

**The build type-checks**: `vite build` transpiles without type checking, which is how an `api.put`
that was never defined reached production. `build` now runs `tsc --noEmit` first.

**The CSS target is pinned**: without it the minifier rewrites media queries into range syntax, which
Safari only understands from 16.4, and on an older phone the whole mobile layout would be ignored.

**Alembic owns the schema** (since 2026-07-26). `create_all` never added columns to existing
tables, and the `ALTER TABLE` loop that replaced it could only ever add them: a rename, a type
change or a new index had no way through. `upgrade_database` in `migrate.py` runs
`alembic upgrade head` at every start, so deploying is migrating. Databases that predate Alembic
have no `alembic_version`: they are aligned by `ensure_schema`, the old loop, kept for exactly this,
and then stamped `0001`. Verified on a copy of the real database, 30,287 file rows, adopted and
upgraded without losing one.

**Autogenerate fills in `server_default` by itself**: `default=` in the models is applied by
SQLAlchemy when it builds the INSERT, so it leaves no trace in the DDL and Alembic cannot see it.
The generated revision would say `ADD COLUMN ... NOT NULL` with no default, which SQLite refuses on
a table that has rows, that is on the only database that matters. `process_revision_directives` in
`alembic/env.py` reads the model default and renders it as a DDL default. When there is no constant
default it raises, while the revision is being written, rather than letting the backend fail to
start on the next deploy.

**`render_as_batch` is on**: SQLite cannot alter a column in place, batch mode rebuilds the table
around the change. Without it anything beyond ADD COLUMN fails.

**CI checks, it does not test**: `ci.yml` lints the backend with Ruff, type-checks and builds the
frontend, builds both images, boots the backend twice against an empty data directory and asserts
the database reaches head, runs `alembic check` so a model change without a revision fails, and
runs `.github/scripts/check_migrations.py`, which seeds one row into every table before upgrading
because a migration that only ever ran on an empty database has proved nothing.

**Only `ruff check`, never `ruff format`**: formatting the existing code would have produced a 310
line diff across files nobody was touching. The lint rules are in `ruff.toml`, `ASYNC109` is off on
purpose, the generated revisions are excluded.

**Trivy fails only on what can be fixed**: every finding goes to the Security tab as SARIF, but the
step that fails the build passes `--ignore-unfixed` and `.trivyignore`. A vulnerability with no
released fix would otherwise block every pull request until an upstream project acts. Entries in
`.trivyignore` carry a reason and an expiry date, and the gating steps are the only ones that read
it, so nothing is hidden from the Security tab.

## Commands

```
cp .env.example .env      # then fill in CLOUDFLARE_TUNNEL_TOKEN and APP_SECRET
docker compose up -d --build
docker compose logs -f backend
docker compose up -d --no-deps backend    # without touching the other services
```

Access: `http://127.0.0.1:8081` or the Cloudflare tunnel hostname.
Initial credentials `admin` / `admin`, password change is mandatory on first sign in.

## Changing the schema

Edit the models, then generate the revision. It is generated in a throwaway container against a
temporary database, never against the real one:

```
docker compose build backend
docker run --rm -v "$PWD/backend:/app" -w /app -e DATA_DIR=/tmp/gen tgbackup-backend sh -c \
  'mkdir -p /tmp/gen && alembic upgrade head &&
   alembic revision --autogenerate --rev-id 0002 -m "what changed"'
```

Two things that are not optional. `alembic upgrade head` first, because autogenerate refuses to
compare against a database that is not already at head. And `--rev-id`, the next free number:
without it the revision gets a hash and the directory listing stops reading as a history.

Read the generated file, it is a draft and not a result, then deploy with
`docker compose up -d --no-deps --build backend`. The container migrates itself at start.

Locally, the same checks CI runs:

```
docker run --rm -v "$PWD:/w" -w /w ghcr.io/astral-sh/ruff:0.16.0 check backend/
docker run --rm -v "$PWD/.github/scripts:/scripts:ro" -e DATA_DIR=/tmp/data \
  tgbackup-backend python /scripts/check_migrations.py
```

## Adding a source

**Local folder**: a volume in `docker-compose.yml`, service `backend`, as
`- /host/path:/host/path:ro`, then `docker compose up -d --no-deps backend`. In the interface the
job path is the one inside the container.

**rclone remote**: paste the `rclone.conf` in Settings, then in the job pick Rclone remote and give
`remote-name:` or `remote-name:subfolder`. The Browse button opens the remote browser, from which the
path can be copied or applied directly. No container restart and no volume: the configuration is
reloaded on save and the remote is contacted when the job is saved, so an unreachable path fails
immediately.
