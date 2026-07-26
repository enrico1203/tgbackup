# tgbackup

Self-hosted application that keeps local folders or rclone remotes mirrored inside private
Telegram channels. Uploads new files, deletes from the channel the ones that disappeared,
re-uploads the modified ones, splits files above the threshold and can reassemble them.

## Operating rules (binding)

**Everything in English.** Interface, code, comments, docstrings, log messages, error messages,
commit messages, documentation. No exceptions, including when the request that triggered the work
was written in another language.

**No test environment.** No test suite, no staging. Production code is written directly and put in
execution. Do not waste time trying things out: just write it.

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

Backend: Python 3.14 Alpine, FastAPI, SQLAlchemy 2.0 async on SQLite, Telethon 1.44, cryptg,
rclone 1.74 (official static binary).
Frontend: React 19, Vite 8, TypeScript 7, react-router 8, TanStack Query 5, lucide-react.
Infra: docker compose with `backend`, `frontend` (nginx), `cloudflared`.

## Layout

```
backend/app/
  config.py db.py models.py schemas.py security.py deps.py migrate.py main.py
  api/       auth accounts jobs files dashboard rclone ws
  telegram/  manager.py (client registry, peers) fast_transfer.py (parallel upload/download)
  rclone/    client.py (lsjson, ranged cat, preview)
  sync/      source.py scanner.py runner.py scheduler.py restore.py progress.py
frontend/src/
  pages/     Login ChangePassword Dashboard Jobs Accounts Files Runs Settings
  components/ Shell ui JobActivity RemoteBrowser
  lib/       api auth progress types format theme
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

**Schema migration at startup**: `create_all` does not add columns to existing tables, so
`migrate.py` compares models against the schema and issues the missing `ALTER TABLE`.

## Commands

```
cp .env.example .env      # then fill in CLOUDFLARE_TUNNEL_TOKEN and APP_SECRET
docker compose up -d --build
docker compose logs -f backend
docker compose up -d --no-deps backend    # without touching the other services
```

Access: `http://127.0.0.1:8081` or the Cloudflare tunnel hostname.
Initial credentials `admin` / `admin`, password change is mandatory on first sign in.

## Adding a source

**Local folder**: a volume in `docker-compose.yml`, service `backend`, as
`- /host/path:/host/path:ro`, then `docker compose up -d --no-deps backend`. In the interface the
job path is the one inside the container.

**rclone remote**: paste the `rclone.conf` in Settings, then in the job pick Rclone remote and give
`remote-name:` or `remote-name:subfolder`. The Browse button opens the remote browser, from which the
path can be copied or applied directly.
