# tgbackup

Self-hosted application that keeps local folders or rclone remotes mirrored inside private
Telegram channels. Uploads new files, deletes from the channel the ones that disappeared,
re-uploads the modified ones, splits files above the threshold and can reassemble them.
Download jobs go the other way, pouring a whole channel back into a folder or a remote.

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

**Never write to the user's data.** Folders to back up are mounted `:ro`. The one exception is the
destination of a download job, which exists to be written: it needs its own volume without `:ro`,
it must never be a folder a sync job reads, and even there nothing is ever deleted.

**No destructive tests against production.** There is no separate environment, so before writing or
deleting anything through the APIs (rclone configuration, accounts, jobs) check whether a real value
is already there. A user's rclone configuration was lost exactly this way. Verifications that write
run in a throwaway container: `docker run --rm ... tgbackup-backend python ...` with a temporary
`DATA_DIR`.

**Careful with `docker compose up -d <service>`**: because of `depends_on` it also restarts the
backend and interrupts running jobs. Use `--no-deps`.

**Every change ends the same way, in this order.** A change that works on this machine and nowhere
else is half done: the images on Docker Hub and the release are what other installations see.

```
docker compose up -d --no-deps --build backend frontend   # deploy, then read the log
git push origin main                                      # after committing
git tag -a vX.Y.Z -m "tgbackup X.Y.Z ..." && git push origin vX.Y.Z
gh release create vX.Y.Z --verify-tag --title "tgbackup X.Y.Z" --notes-file <notes>
```

The tag is what publishes: `release.yml` builds amd64 and arm64 and pushes `X.Y.Z`, `X.Y` and
`latest`, then rewrites the Docker Hub pages from the README. A new feature moves the minor, a fix
the patch. Check the run is green before writing the release, and say in the notes what changed and
what it means for somebody upgrading, not the list of commits. If jobs are running, say so before
deploying and let the user decide: a run interrupted by the restart resumes on start without losing
an interval, but it starts again from the scan.

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
    transfer.py (export and import of a channel index)
    maintenance.py (check of a channel, rebuild of the index from it)
    notify.py (report at the end of a run, to Saved Messages)
    api/       auth accounts jobs downloads files explorer dashboard export maintenance
               preferences rclone ws
    telegram/  manager.py (client registry, peers) fast_transfer.py (parallel upload/download)
    rclone/    client.py (streaming lsjson, ranged cat, rcat, touch, config on disk)
    sync/      source.py destination.py scanner.py filters.py runner.py download.py
               scheduler.py restore.py progress.py
frontend/src/
  pages/     Login ChangePassword Dashboard Jobs Downloads Accounts Explorer Files Runs
             Export Maintenance Settings
  components/ Shell ui JobActivity DownloadActivity RemoteBrowser ChannelPicker
  lib/       api auth progress types format theme channels
.github/
  workflows/ ci.yml trivy.yml release.yml
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
total bandwidth, it spreads the same bandwidth across more jobs. `manager.transfer_lock` is shared
between uploads and downloads: the ceiling does not care which direction the bytes go.

**Sources**: `sync/source.py` hides local folders and rclone remotes behind the same interface
(`list_files`, `reader`). The uploader does not know where the bytes come from.

**Destinations**: `sync/destination.py` is the mirror image, `list_files` plus `sink`. The one
difference it cannot hide is `random_access`: a local file is written with pwrite at the offset of
every part, so the parts download in parallel, while `rclone rcat` is a pipe and takes the bytes in
order. `stream_document` in `fast_transfer.py` bridges the two, downloading in parallel and yielding
in order, with early chunks held in a window of 32 parts, that is 32 MB whatever the file size. It
cannot deadlock: a sender only waits when it is a whole window ahead, and the part being waited for
always belongs to a sender that is not ahead at all, because one sender delivers its own parts in
order.

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
on restart instead of slipping a whole interval. The account semaphore covers only the transfer
phase: scanning and cleanup run in parallel, and queued jobs show a `waiting` phase. Everything is
keyed by `(kind, id)`, `sync` or `download`: the two tables are numbered independently and job 3 and
download job 3 have to be able to run at the same time.

**Download jobs** (`sync/download.py`): the inverse of a sync job. What the channel holds is the
index in the database, that is the `file_entries` of the sync jobs writing to that channel, so a
channel whose index arrived through Import works exactly the same, and that is the point of the pair.
No file table of its own: every run lists the destination and downloads the difference, so the
destination is the state and nothing can drift away from it. The comparison is by size alone, not by
the identity triple: the date of a file at the destination says when it was written there, and some
backends do not store one at all. A local file is written to `<name>.tgpart` and renamed on
completion, because it is sized upfront and a half written one would otherwise look complete; a
remote is written straight to its final name, since rclone finalises an object only at the end of the
stream and a leftover name on a remote is far harder to clean up. The original mtime is restored,
with `os.utime` locally and `rclone touch` on a remote, where it is best effort because plenty of
backends refuse it.

**Filters** (`sync/filters.py`): include and exclude patterns plus a size ceiling, per job.
Translated to regular expressions once when the job starts and applied to the relative path of
every file, for both kinds of source. A pattern with no slash matches the name at any depth, one
with a slash matches the whole path, one ending with a slash matches everything under it, `*` stays
inside a segment and `**` crosses them, and case is ignored. A filter is not only about what comes
next: a file already in the channel that the filter now leaves out is missing from the listing, so
the diff sees it as removed and deletes it from the channel. That is the intended behaviour and the
form says so.

**Channel check and index rebuild** (`maintenance.py`): the two directions of the same relationship.
The check goes from the index to the channel, asks for every recorded message and reports the ones
gone or carrying a document of the wrong size; with `repair` the damaged files go to `stale`, which
is the state the runner already uses for parts that have to be replaced. The rebuild goes from the
channel to the index, reads every message and puts the entries back together out of the captions,
which is what makes a lost database an inconvenience rather than a disaster. Both run as background
tasks with their progress in memory, because on a large channel they take minutes and there is
nothing worth keeping across a restart: running them again repeats the same work. Neither may run
while a sync job is uploading to that channel, since they change the index that job is writing; a
check that only reports is always allowed.

**The channel cannot give back the mtime**: a message carries name, folder and part number, never a
date. Entries written by a rebuild get `MTIME_UNKNOWN` (-1) and the first scan of the job adopts the
date of the source when the size matches, instead of taking every file for modified and re-uploading
the whole channel. Same compromise a download job makes: size is what the channel actually knows.

**File explorer** (`api/explorer.py`, page `Explorer.tsx`): the index browsed as a folder tree. The
listing reads the database and never Telegram, so opening a folder costs one query whatever the size
of the channel. The rows are selected on `prefix <= rel_path < prefix + "\U0010ffff"` and not with
`LIKE 'prefix%'`, which returns the same rows but cannot be answered from the index, because LIKE
ignores the case of ASCII letters and the index does not. Folders are assembled in Python from the
first segment after the prefix, with the count and the bytes of the whole subtree; only entries in
`uploaded` are shown, since anything else is not in the channel to be opened. A file split into parts
is one row: the split belongs to the transport. Search reuses those same rows instead of a second
query: it keeps a match at any depth below the current folder, files by name and folders by their own
name, which is why it costs the same as the listing it replaces.

The destination of a download is chosen in a dialog: the browser, a folder on the server or an rclone
remote. The last two go through `restore_file`, which now builds a `LocalDestination` or an
`RcloneDestination` exactly as a download job does, calls `prepare()` before starting so a wrong path
is an error on the button, and writes through the same sinks, local by part at its offset and remote
in order through `rcat`. `os.access(W_OK)` is what stops a download landing in a folder a sync job
reads: those are mounted `:ro` and fail the check. Their progress travels as a restore because that
is what it is, and `RestorePanel` shows it on both pages. The download is a `StreamingResponse` fed by
`stream_document`, the same parallel-in, ordered-out mechanism a download job uses for `rclone rcat`,
so nothing is staged on disk and a 40 GB file needs no 40 GB anywhere. It holds the account transfer
semaphore for its duration, acquired inside the generator because that is the only place whose exit
is guaranteed when the browser goes away halfway. `nginx.conf` turns `proxy_buffering` off on
`/api/explorer/download/`, or nginx would try to hold the whole file before the browser saw a byte.

**Notifications** (`notify.py`): a report at the end of a run, sent to the Saved Messages of an
account. The client is already connected and every account has a chat with itself, so there is no
webhook to configure and no second service to keep alive. The preferences are two rows in `settings`
rather than columns of their own, which is why they needed no migration. Off, errors only or every
run, and a failure to send is a line in the log: a notification must never be able to break a job.
Nothing is sent when the process is shutting down, or every deploy would produce a message per
running job.

**A download job deletes nothing**: a sync job deletes from the channel what disappeared from the
source, because the channel is a copy this application owns. The destination of a download job holds
the user's own files, and a file the channel does not know about is not a mistake to correct. Two
jobs with the same rel_path in one channel are resolved by keeping the most recently uploaded, which
is the same rule the file browser applies.

**Durability**: every part is recorded in the database as soon as its message is sent. A crash in the
middle of a large file leaves no orphan messages: on the next scan the file goes through `stale`, the
already sent parts are deleted from the channel and the file restarts clean.

**Channel export** (`transfer.py`, Export section): one channel at a time, index only. The file
is gzipped JSON holding the channel coordinates, the jobs writing to it and, per file, the identity
triple plus the message ids of the parts, which is exactly what a restore needs. 2,500 files with
7,500 parts compress to 65 KB. It carries no session and no `api_hash`: the other machine signs the
account in itself. Import links the channel to an account of the target instance, in `create` mode
(a new job per exported job) or `merge` mode (matches an existing job by name on that channel and
inserts only the paths it does not have, so re-importing the same file changes nothing). Files
inserted in bulk, ids read back once per job rather than one round trip per row. Uploads arrive as
multipart, which is why `client_max_body_size` in `nginx.conf` is 64 MB and no longer 1.

**`access_hash` does not travel well**: it is issued per user, so the exported one is only valid if
the import lands on the same Telegram account. On import the value is taken from the target
account's own dialog list when it is reachable, and the exported one is the fallback. Both the
mismatch and the failure to verify are reported as warnings on the import result rather than
blocking it: the index is worth having even when the channel cannot be checked right then.

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

**Imported jobs arrive disabled, and their name is kept as exported** (since 2026-07-26). The
source of an imported job belongs to the machine that produced the export. If that path does not
exist here the run fails cleanly, but if it exists and holds something else, the diff sees every
known file as removed and the first run deletes the whole channel message by message. Enabling is
left to the user, after the source has been pointed at the right place. The name is not decorated
with an "(imported)" suffix either, however tempting: `merge` matches jobs by name, so a rename
would make a second import of the same file miss the job the first one created and duplicate
everything.

**A download job compares by size and never deletes** (since 2026-07-27). Mirroring in reverse, that
is deleting from the destination what the channel does not hold, would mean this application deleting
the user's own files on the strength of an index that may have been imported from another machine.
The comparison could have used the identity triple instead of size alone, but the mtime at the
destination is the moment the file was written there, unless the restore of the date worked, which on
a remote is not guaranteed: comparing dates would re-download everything on every run.

**`--rev-id 0002` is the second revision, `download_jobs` and `download_runs`** (2026-07-27). Two new
tables, no column added to the existing ones, so a database with rows goes through untouched.
`0003` adds `include_globs`, `exclude_globs` and `max_file_size` to `sync_jobs`, three ADD COLUMN
with the DDL default that `process_revision_directives` filled in by itself.

**One filter engine, not rclone's** (since 2026-07-27). rclone has `--exclude` and it would have
cost nothing to pass the patterns down, but then a pattern would mean one thing on a remote and
another on a local folder, and that difference would only ever show up as files quietly missing
from a backup. The patterns are matched in Python for both sources. The listing is not made cheaper
by filtering earlier anyway: on a remote the cost is the API call, and everything is listed either
way.

**A rebuilt index arrives in a disabled job** (since 2026-07-27), for the same reason an imported
one does: the job has no source yet, and a run against the wrong folder would see every file as
removed and empty the channel message by message. The rebuild also skips paths already indexed
anywhere on that channel, not only in the target job, so the same messages never get two entries and
running it twice writes nothing the second time.

**A browser download travels on a ticket, not on the session token** (since 2026-07-27). A download
is a plain navigation, where no Authorization header can be set, so the credential has to be in the
URL, where the browser history and the nginx log keep it. `create_download_ticket` mints a JWT for
one file and five minutes, and `decode_token` now refuses any token carrying a `scope`, so a ticket
picked out of a log cannot be replayed as a session. The alternative, fetching the body with the
header set and handing it over as a blob, would hold the whole file in the memory of the tab, which
for the files this application moves is not an alternative at all.

**The explorer download has no Range support** (since 2026-07-27). An interrupted download starts
again from the beginning. Range would mean either staging the file, which the whole design avoids, or
mapping an arbitrary byte range onto the parts and restarting the parallel senders at an offset
inside one of them, for a case that a self-hosted explorer meets rarely. The account is connected
before the response starts, though: once a `StreamingResponse` has sent its 200 and its length there
is no way to report an error, so a disconnected account is a 409 at ticket time and the interface
shows the reason.

**The images are published on Docker Hub, multi-arch, on a tag** (since 2026-07-27).
`release.yml` builds `enrico1203/tgbackup-backend` and `enrico1203/tgbackup-frontend` for
`linux/amd64` and `linux/arm64`. Each architecture is built on a runner of its own architecture,
`ubuntu-latest` and `ubuntu-24.04-arm`, and never under QEMU: cryptg is compiled with Rust and
emulated that build takes the better part of an hour. The two halves are pushed by digest with no
tag, then a merge job assembles one manifest list and puts the tags on it, which is the only way two
runners can produce a single multi-arch tag. The trigger is a `v*` tag or the button, never a push:
`latest` is what a user pulls and it must not move on every commit. `TARGETARCH` in the backend
Dockerfile picks the matching rclone archive, whose names happen to be amd64 and arm64 as well.
`docker-compose.hub.yml` pulls those images and is what somebody who never clones the repository
uses; `docker-compose.yml` still builds from the sources and the two are not meant to be mixed.
The Cloudflare tunnel is behind a `tunnel` profile there, because an empty token would otherwise
leave a container restarting forever on an installation that does not want a tunnel.
The credentials are two repository secrets, `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`, the second
a Docker Hub access token with Read and Write: an account created through GitHub has no password to
use in its place. A last job rewrites the page of each repository on Docker Hub from the README, in
curl and jq rather than a third-party action, because that page is the only documentation somebody
who never opens GitHub will read and kept by hand it would drift.

**AGPL-3.0, chosen 2026-07-26**. This is a self-hosted network application, which is the case the
Affero clause exists for: with plain GPL somebody could run a modified version as a hosted service
and owe nothing, since they would never be distributing it. The comparable projects settled the same
way, Immich, Nextcloud and Mastodon are all AGPL-3.0. Every dependency is permissive, MIT, BSD,
Apache 2.0, ISC and CC0, so nothing forced the choice and nothing conflicts with it. Compatibility
runs one way: permissive code can come in, this code cannot go into a closed project. As sole
copyright holder the licence can still be changed or dual-licensed later, but only for versions not
yet published.

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

## Adding a download destination

**Local folder**: a volume in `docker-compose.yml` **without** `:ro`, as `- /host/path:/host/path`,
then `docker compose up -d --no-deps backend`. Never a folder a sync job reads. The form refuses a
path that does not exist or is not writable, `os.access(W_OK)` returns false on a `:ro` bind mount
even for root, so the mistake is caught when the job is saved and not at three in the morning.

**rclone remote**: nothing to mount, same form as a source. The bytes go from Telegram into
`rclone rcat` through a pipe, nothing is staged on disk.
