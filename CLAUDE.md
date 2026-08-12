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
# The version stamped on the images comes from git, never from a number kept by hand.
APP_VERSION="$(git describe --tags --always --dirty | sed s/^v//)" \
  docker compose up -d --no-deps --build backend frontend # deploy, then read the log
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
cryptg, rclone 1.75 (official static binary).
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
    api/       auth accounts botsets jobs downloads files explorer dashboard export
               maintenance preferences rclone version ws
    telegram/  manager.py (client registry, peers) bots.py (bot clients, the lease pool)
               fast_transfer.py (parallel upload/download)
               patches.py (the changes made to Telethon from the outside)
    rclone/    client.py (streaming lsjson, ranged cat, rcat, touch, config on disk)
    sync/      source.py destination.py scanner.py filters.py runner.py download.py
               scheduler.py window.py restore.py progress.py
frontend/src/
  pages/     Login ChangePassword Dashboard Jobs Downloads Accounts TelegramBots Explorer
             Files Runs Export Maintenance Settings
  components/ Shell ui JobActivity DownloadActivity RemoteBrowser ChannelPicker
              ScheduleGrid UpdateBanner
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

**Telethon gzips every request, and that was the ceiling** (measured 2026-08-05).
`MTProtoState.write_data_as_message` runs each outgoing body through
`GzipPacked.gzip_if_smaller`, which compresses anything over 512 bytes at level 9 and
keeps the result only if it came out shorter. On a 512KB part of a video the answer is
always no: on this image gzip level 9 does 37.7 MB/s on one core and returns 178 bytes
more than it was given, so the copy is built and thrown away for every part uploaded.
With the process at 100% of one core and around 20 MB/s of upload, a second account
changed nothing, because the limit was never the 20 connections per data center. On the
same core AES-IGE through cryptg runs at 298 MB/s and SHA-256 at 1600 MB/s, so with the
gzip gone the Python side is no longer what decides the speed. `telegram/patches.py`
skips compression for bodies above 16 KB, which in this application means media parts and
nothing else, and `main.py` applies it before any client exists. A body that is not
gzipped is always accepted: compressing is an option the client may take, never an
obligation.

**Connection budget** (`manager.account_budget`, `max_connections` on the account, revision `0011`):
the 20 connections are per data center, not per job, and 20 is the ceiling and not the setting. What
an account opens is a column of its own, 1 to 20, 15 by default here and on every account that
predates the column, because the limit Telegram applies is counted per account: one that keeps being
held back is worth running on fewer connections for good, while another on the same host has no
reason to be. `max_concurrent_jobs` divides that budget rather than the ceiling: 15 connections and 2
concurrent jobs is 7 each. Raising the concurrency does not increase total bandwidth, it spreads the
same bandwidth across more jobs. Everything that transfers goes through the one function, sync jobs,
download jobs, restores and browser downloads, so there is a single place where the number is decided
and `FloodGate.allowance` lowers it from there while a run is being cut. `manager.transfer_lock` is
shared between uploads and downloads: the budget does not care which direction the bytes go.

**Bot sets** (`telegram/bots.py`, `bot_sets` and `bots`, revision `0012`): N bots that are admins of
one channel, used by a sync job as one uploader. A bot is a Telegram account of its own, so five bots
are five auth keys, five budgets of twenty connections and five separate flood limits, which is the
whole point: a job on a set uploads **one file per bot at the same time**, and one bot being held
back leaves the other four moving. What a bot cannot do shapes everything around it. It has no dialog
list, so a channel is named and resolved with `channels.getChannels` passing `access_hash=0`, which
the server accepts from a bot for a channel it belongs to and which is therefore both the peer and
the proof of membership; the access hash stored on the row belongs to whichever account discovered
the channel and is worth nothing here. It has no Saved Messages, so the report of a run travels
through an account (`notify._fallback_account`). It is never Premium, so the split is 1.9 GB. And it
only uploads: the explorer, the restore, the browser download, the download jobs and the channel
check all read messages back and still ask for an account that is a member, which
`restore.reader_account` and `maintenance._reader_account` say in one sentence instead of failing
somewhere inside a stream.

**One channel is one row, whoever carries the job** (`channels.account_id` nullable): a bot-set job
points at the same `channels` row an account job would, so switching a job between the two changes
one column and leaves `channel_id` alone, which is what keeps the index from splitting in two and the
explorer, the export and the check from seeing two channels with one title. The row is nullable on
`account_id` only so an installation that has never linked an account can create one by naming the
channel through a bot; `channel_for_account` and the import adopt such a row rather than duplicating
it. Deleting an account is refused while any job writes to a channel it discovered, because those
rows cascade with it.

**The upload phase is a worker pool** (`runner.AccountTransport`, `BotSetTransport`, `_upload_worker`):
the transport is decided once at the start of the run and everything below asks it rather than the
account. An account gives one worker, which is the behaviour that was always there; a set gives
`parallel_files` of them, 0 meaning as many as it has bots. A bot is **leased for one file and handed
back**, never held for the run: that is what stops two jobs sharing a set from deadlocking, since a
worker waits before it holds anything and never holds one bot while asking for another. The claim of
the next `pending` row is under one `asyncio.Lock`, one process and one loop being all it takes, and
without it two workers would upload the same file into two sets of messages of which only the last
would be recorded. A bot that fails to connect or is no longer in the channel is retired from the run
rather than failing the file it was handed, and a run left with no usable bot raises `NoCarrier`,
which puts the file back to `pending` instead of marking every remaining one in error.

**One flood gate per bot, read as one** (`flood.FloodGroup`): the gate exists because twenty
connections meeting one limit is the storm, and that argument stops at the account boundary. Each bot
of a set gets its own gate, so a wait told to one does not hold the others, and the connection budget
it lowers under a limit belongs to that bot alone. The group answers `snapshot` and `events` exactly
as a gate does: held for the longest wait still running, limited if any of them is, events summed,
which is what the progress frame carries and what `limited_events` on the run row records.

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

**Moving a job to another account** (`channel_for_account` in `api/accounts.py`): the account of a job
can be changed after it was created, and the channel goes with it. Channel rows are per account,
the `access_hash` being issued per user, so the move repoints the job onto the row the new account
holds for the same `tg_id`, built from that account's dialogs when there is none and refused when the
account is not in the channel. The index is not touched at all: message ids belong to the channel, so
an entry uploaded by one account is read, downloaded and deleted by another exactly as it was, which
is the same fact the import of a channel already stands on. What membership alone does not give is
the right to delete messages somebody else sent, and that is what a job needs to remove a file that
disappeared from the source, so the form says it. `PATCH` carries `account_id` alone when the browser
has no row of the new account to name, and the two together when the user changed both. The scheduled
check is carried onto the new row when the old one is left with no sync job, since it finds what to
compare through the jobs writing to that row and would otherwise go on checking nothing and reporting
a healthy channel.

**Schedule windows** (`sync/window.py`, `ScheduleGrid.tsx`): 168 characters on the job row, one per
hour of the week, Monday 00:00 first, "1" open. A string and not a table because it is always read
whole and never queried. It gates the interval, it does not replace it: a job becomes due exactly as
before and the window only decides whether it may start then, so a run that falls outside waits for
the opening instead of being lost. Run now ignores it, being an explicit order. With
`stop_outside_window` a run still going when the window closes is cancelled with reason `window`,
which `runner.py` and `download.py` treat like `shutdown`: `next_run_at` is not moved and no report is
sent, so the job resumes at the next opening rather than skipping an interval and the user does not
get a message every morning. Safe only because every part is recorded as it is sent. The hours are
local hours, in one timezone for the installation kept in `settings` (`schedule_timezone`, no
migration needed); arithmetic stays in UTC and only reading the slot converts, so a DST change moves
the window by an hour for a day and can never loop. An unparseable window normalises to always open:
a schedule that cannot be read must never be the reason a backup stops. `window_open` and
`next_window_at` are computed on the job out, since the browser would have to redo the timezone
arithmetic to get the same answer.

**Bandwidth limit** (`telegram/throttle.py`): a token bucket the transfer takes from before the
bytes move. There is exactly one place where the speed of an upload can be decided, the reader loop
in `upload_slice` that every chunk passes through whatever the source and however many connections
are open, and one for a download, the part a `_DownloadSender` has just brought back. So the limit is
taken there and nothing else has to know it exists. On the way down the take happens **after** the
part arrived rather than before asking for it: a sender waiting there does not ask for its next one,
which is what holds the average, and the overshoot is bounded by one part per connection.

The rate is a **function returning a number**, not a number, consulted as the bucket refills. That is
what lets an hour boundary change the speed of a run already going, with nothing to restart, and it
is why the window grew a third state instead of a second grid: `schedule_hours` is now "0" closed,
"1" open, "2" open at the speed set on the job. `normalise` keeps the "2", `is_open` treats it as
open because it changes the speed and not the permission, and `stop_outside_window` still means "0"
alone, so every existing window reads exactly as it did. A job with a ceiling and no hour painted
"2" is limited at every hour, which is the plain "never faster than this" case. The installation
limit is one `settings` row, kept in a module level value read at startup and rewritten when saved,
because a query per chunk is not an option; the two apply together through `ChainedLimiter`, and
whichever is tighter is what happens. A restore and a browser download carry the installation limit
only, since no job owns them.

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

**Deletion guard** (`sync/runner.py`, `guard_verdict`): a ceiling on how much of a backup one run
may delete. A source that fails to mount is not an error, it is an empty folder: `list_files`
returns nothing, the diff concludes that every file was removed and the channel is emptied message
by message. The guard is per job, a percentage of the indexed files and an absolute number, either
one enough to stop the run, both off at zero. It is evaluated inside `_diff` **before anything is
committed**, so a run stopped there has neither marked a deletion nor recorded a new file and the
index is exactly as it was; had it run after the states were written, the `to_delete` rows would
already be there waiting for the next run to apply them. The percentage is not applied below
`GUARD_MIN_ENTRIES` (10) files, where any single deletion is a large share of the whole and the
ratio says nothing. Existing jobs get 20% from the DDL default, which is deliberate: the feature is
worth nothing if it has to be turned on first. A trip is a failed run with `status = "error"`, so
the notification already carries it, and the only way through is `POST /api/jobs/{id}/allow-deletions`,
which sets a flag the next run consumes whether or not it needed it: an acknowledgement is worth one
execution, not a permanent disarming. `stale` entries are re-uploads and are not counted.

**Trash instead of immediate deletion** (`trash_days` on the job, `trashed` state, `trashed_at` on
the entry): a file that disappears from the source stops being deleted within the run that noticed
and becomes `trashed`, keeping every one of its parts. Telegram charges nothing for the space, so
the only cost of holding it is an index row. Three things follow. The file stays downloadable and
restorable, which is what somebody wants from it in the first place, so `READABLE_STATES` and not
`state == "uploaded"` is what the explorer, the restore, the ticket and the channel check ask for.
A file that comes back to the source unchanged is **revived**, `trashed` back to `uploaded` with
`trashed_at` cleared and not one byte uploaded, because its messages were never deleted; only one
that came back different goes through `stale`. And the channel can be browsed as it stood on a past
day, `as_of` in `api/explorer.py`: a file was there if it had been uploaded by then and had not yet
been trashed, which is the two dates on the row and nothing more. The purge is `_purge_trash` in the
runner, which only moves expired entries to `to_delete` and lets the one deletion path in the
application do the work; with the retention set back to zero the whole trash expires at once, which
is what turning the feature off has to mean. Only a file in `uploaded` is worth trashing: a pending
one has no message to hold on to. A damaged file found in the trash by a check is reported but never
marked `stale`, since the source cannot upload it again.

**What the trash does not keep is the previous version of a modified file.** A modification still
deletes the old parts and uploads new ones, so `as_of` gives back everything that was deleted since
that day but shows the current content of anything modified since. Real versioning needs the parts
of several versions of one path to coexist, which the `(job_id, rel_path)` unique constraint forbids
and a `file_versions` table would have to carry. Deliberately not done: the interface says so where
the date is picked.

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

**Scheduled maintenance** (`scheduled_check` in `maintenance.py`, `_check_channels` in the
scheduler): the check the user had to remember to press, run by the installation instead. Per
channel, every N days at a local hour, 0 days off. It obeys the rules the manual endpoint already
enforces, never two tasks on one channel and never a repair while a sync job writes the index that
repair would change, except that instead of refusing it drops the repair and reports anyway: an
unattended check must not skip a month because a job happened to be running. The outcome goes on the
channel row, `last_check_at` and `last_check_result` as JSON, and not into the in-memory registry,
which keeps twenty tasks and forgets them at every restart while a monthly check has to be readable
the month after. The report is a failure only when something is broken, so `errors` mode stays quiet
on a healthy channel and `all` mode gets the confirmation. The hourly slot is compared with an hour
of slack, because demanding the full N days on an hourly tick would push every check one hour later
than the last and drift a day out within a month.

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

**A folder is restored as one operation** (`restore_folder` in `sync/restore.py`): the same machinery
with more than one file in the list, and not a hundred restores started by the browser. The
destination is prepared once, so a wrong path is an error on the button; a file that fails increments
a counter and the folder carries on, because the alternative is a restore of 4,000 files that stops
on the third; the progress is one bar with a file count and a current path; and the whole thing can
be stopped, which a folder measured in terabytes has to be. The files are the ones the explorer would
list at that path, the trash included, and each keeps its path **relative to the chosen folder**, so
`Photos/2024` restored into `/mnt/restored` gives `/mnt/restored/january/...` and not the original
tree repeated underneath. The account transfer semaphore is taken **per file** and not around the
batch: holding the connection budget for the hours a folder takes would leave every job of that
account in `waiting` for all of it. The same change gave the single-file restore the budget division
it never had, `MAX_CONNECTIONS // max_concurrent_jobs` instead of the whole ceiling. Finished restores
are forgotten by the hub after `RESTORE_LINGER` (5 minutes), long enough to see how it went and short
enough that the panel is not a log.

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

**Silence alarm** (`_check_silence` in `sync/scheduler.py`): a job that stops succeeding says
nothing on its own. A failed run sends a report, but a job disabled by mistake, or one whose window
never opens, produces no event at all, and the absence of a message is not something anybody
notices. The watcher is a second scheduler loop on an hourly tick, deliberately apart from the 10 s
one, which has to stay cheap. The threshold adapts: the longer of the configured days and **twice
the interval of the job**, because three days mean nothing to a monthly job and are an eternity for
an hourly one. The reference is the last `ok` run read from `job_runs` / `download_runs`, not a
column, since the runs table already holds the truth; a job that never succeeded is counted from its
creation, so one that has been broken since the day it was made is reported too. Disabled jobs are
included, that being the case the feature exists for, and the escape hatch is `silence_alerts` on the
job. `silence_alerted_at` stops it repeating: another whole threshold has to pass before the same job
speaks again, and a run that ends `idle` clears it. The alarm is sent as a failure so the `errors`
notification mode delivers it, which is the mode of somebody who only wants to hear about problems.

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

**No Telegram call may hang for ever** (`telegram_request_timeout`, 10 min). Telethon returns a bare
future for every request, resolved by the receive loop when the answer arrives and by nothing at all
when it never does. A connection dropped at the wrong moment leaves the call pending: the reader
stops being read, the source blocks with a full buffer, and the job holds its account slot at zero
bytes with no error to show for it. `call_with_timeout` wraps every request that carries a transfer,
the upload part, the download part and the message that publishes them, turning the silence into an
exception `_upload_with_retry` already knows how to handle by rebuilding the reader and starting the
slice again. The value has to clear what Telethon may legitimately spend inside one call: it retries
`request_retries` times, 5 by default, sleeping up to `flood_sleep_threshold`, 60 s by default, on
each flood wait, so around 300 s of honest waiting.

**A part that is never answered is sent again, not thrown away** (`telegram_part_timeout`, 2 min,
measured 2026-08-12). Ten minutes is the right length for "this call is never coming back" and the
wrong one for a part. Observed on this installation, on both accounts and with no flood wait
anywhere in the log: Telegram takes two or three hundred megabytes on a fresh set of senders and
then stops answering `upload.saveBigFilePart`, with the twenty connections established, their send
and receive queues empty and the event loop answering the API in twenty milliseconds. The transfer
sat at zero for the whole ten minutes, gave up the slice and read it again from its first byte, so a
file of several gigabytes never reached the end. So a part has a deadline of its own, and silence at
that deadline is answered by `renew`, which drops that one connection and sends the same part on a
new one: re-sending is free of consequence, the server keys a part by file id and index. The old
connection goes before the new one is built, or the twenty per data center would become twenty one.
A second silence in a row is not one unlucky connection, so it closes the `FloodGate` for the whole
run through `stalled()`, the same ladder a flood wait climbs, because that is what an unannounced
limit is. After `SILENCE_RETRIES` (3) the slice is given up, which is where the old behaviour begins.
Two minutes is a floor, not a guess: twenty connections that slow are moving under a hundred
kilobytes a second between them, and below that a re-send costs more than it saves.

**A limited account is said out loud, and answered by opening fewer connections**
(`FloodGate.cut`, `allowance`, `limited_events` on the runs, revision `0010`). Held and limited are
not the same state and the interface needs both. Held is the transfer at zero waiting out a wait, red,
with a countdown. Limited is the account being held back while the bytes still move, yellow: parts
taken and never answered, connections cut every few minutes, no error anywhere, which until now was
visible only in the log and made a job at a quarter of its speed look like a slow job. The gate is
where both arrive, so it is where the count lives: `waits` plus `cuts`, and a cut is coalesced over
`CUT_COALESCE_SECONDS` because twenty connections meet the same cut within milliseconds and "held
back 3 times" must not read as 60. Two events inside `LIMIT_MEMORY_SECONDS` (15 min) is what turns
the mark on, one lost part in an hour being a lost part and not a pattern, and the mark goes out by
itself once the run has been quiet, since it describes the present. The count is written on the run
row in the `finally` of the run, so all three endings carry it, and it is what the history and the
report say the morning after.

The connection budget answers the same events. `allowance(ceiling)` is read when a slice starts and
never while one runs, because the senders of a running slice are already dividing its parts between
them: a cut takes a quarter of the connections away, five clean minutes give one back, and the floor
is two, one connection being no parallel transfer at all. Additive up and multiplicative down, which
is the right asymmetry when being wrong upwards costs another cut. Whether fewer connections make
Telegram friendlier is not knowable from here: the run tries and keeps what works, and nothing is
lost if the answer is no, since the bytes move at every count. Measured 2026-08-12 on an account with
22.9 TB uploaded in eighteen days, cut every two to four minutes on twenty connections.

**Stop is a request the transfer has to be able to hear** (`StopSignal.wait`, `call_with_timeout`).
The signal is read between one part and the next, which is exactly what is never reached while a
part hangs: pressing Stop on a job in that state did nothing at all, for up to ten minutes, on a job
already showing zero bytes. The pending call is now raced against the signal, so a stopped transfer
leaves at once and nothing is lost, since the parts already sent are recorded and an abandoned slice
is one the next run starts again. It leaves as `CancelledError` and every path that can see one
turns it into `JobCancelled`, `_upload_with_retry` and `_publish_part` on a sync job, the transfer
loop on a download job, the file loop on a restore: a bare cancellation escaping `execute_job` would
skip the block that writes the status back and leave the job `running` with nothing running.

**A one second wait is a pace, not a limit** (`SHORT_WAIT_SECONDS`, measured 2026-08-12). The ladder
below was built for an account told to wait sixteen seconds over and over, and applying it to
everything was wrong in the other direction. Five bots uploading parts get `FLOOD_WAIT_1` and
`FLOOD_WAIT_2` constantly while Telegram keeps taking the bytes: that is the server giving the
transfer its pace. Answered with the ladder it became thirty seconds of silence, then sixty, then a
hundred and twenty, per bot, and the run spent its time inside a backoff of its own making rather
than inside anything Telegram had asked for. So a wait of five seconds or less is obeyed for exactly
as long as it says, climbs nothing, and is not counted as an event: a transfer being paced is a
transfer that is working, and marking it limited would put it in the same state as one that has
stopped. It is held apart from `remaining` as well, or the interface would paint the job red once a
second. What pacing that never lets up gets is `cut()`, one per minute of it, which lowers the
connection budget: the answer to being paced is to ask for less, not to sleep longer.

**A flood wait is an instruction, not an error** (`telegram/flood.py`, measured 2026-08-08). Telethon
sleeps a flood wait shorter than `flood_sleep_threshold` (60 s) and **counts the sleep as one of
`request_retries`** (5), so an account limited at a steady 16 s exhausts its attempts and the call
dies as `ValueError: Request was unsuccessful 6 time(s)`, which names nothing: the
`except FloodWaitError` written for exactly this case never fires, the slice is destroyed, the file
ends in `error`. Observed on the AnimeSUB job, a non-Premium account limited after 1.8 TB in eleven
days: two days, 138,732 rejected requests, zero bytes uploaded, 72 files in `error`, while a Premium
account on the same host kept going at 20 MB/s. So the transfer owns the policy.
`send_transfer_request` bypasses `client._call` for the part requests and awaits the raw sender,
which raises the real error. It takes back three things: a flood wait no longer consumes an attempt,
it is no longer recorded in `_flood_waited_requests`, a per-client map keyed by request type that
made every later part of every job sleep up front for a limit it had not hit, and the delay is no
longer decided per request when the thing being limited is the account. `FloodGate` is one gate per
run shared by all 20 connections, because twenty connections meeting the same limit one after
another is the storm and not the cure: the first one held closes it for everybody, the ladder is
30 s, 1, 2, 5, 10, 15, 30, 60 min and never shrinks while the waits keep coming, and it resets only
after five clean minutes. Deliberately unbounded: a limit that lasts hours is answered in hours,
because the alternative is the file marked `error` and a re-upload from nothing. What keeps that
from being silent is the silence alarm, which reports a job that has stopped succeeding whatever the
reason. `raise_last_call_error=True` on the client is the other half: it makes the handlers that
were already written, in deletions, maintenance and downloads, see the error instead of the
`ValueError`. The gate travels in the progress frame, `flood_wait_seconds`, and the interface paints
the bar, the speed and the pill red, since a held transfer at zero bytes looked exactly like a slow
one.

**Update banner** (`api/version.py`, `components/UpdateBanner.tsx`): an installation nobody
updates is the normal outcome of self-hosting, because nothing on the machine knows a new image
exists. The version each image was built from is baked in at build time, `APP_VERSION` as a build
arg that becomes an environment variable on the backend and `VITE_APP_VERSION` in the bundle on the
frontend, filled in by `release.yml` from the git tag. The backend reads the tags of the two Docker
Hub repositories, keeps the highest that reads as X.Y.Z and caches the answer for six hours, in
memory and not in a column: a restart costing one extra pair of requests is cheaper than a
migration. The two halves are compared separately because they are pulled separately, and the
banner names the one that is behind. What a build from the sources stamps comes from
`git describe` on the deploy command and from nowhere else. A number kept in `.env` was the obvious
place for it and is exactly wrong: it is right until the first release that does not think to copy
it, and from then on the installation announces an update to the version it is already running,
which is the one failure mode a banner must not have. `git describe` cannot drift, because it
describes the tree being built: on a working copy sitting exactly on `v0.6.0` it says `0.6.0`, one
commit further on it says `0.6.0-1-gabc1234`, which does not read as X.Y.Z and is therefore compared
with nothing. So the banner reaches an installation that stayed on a release while a newer one was
published, and stays quiet on a working copy that is ahead of every release, which is the honest
answer in both cases. A check that cannot reach Docker Hub keeps the previous
answer, carries the reason on the response and retries in fifteen minutes rather than six hours; the
banner simply does not appear, because a warning built on a doubt is a warning nobody trusts.

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
it, so nothing is hidden from the Security tab. The file is empty since 2026-08-06: every entry it
held was a Go module inside the rclone binary waiting for an rclone release, and 1.75.0 was it. The
counterpart is that a finding **with** a fix is never ignored, it is applied, which is what moved
`cryptography` to 50.0.0.

**Bumping rclone is a byte-for-byte check, not a version bump** (done 2026-08-06 for 1.75.0). The
uploader reads through `rclone cat --offset --count` and the scan parses `lsjson` one line at a
time, so a change in either would corrupt what reaches Telegram or silently empty a listing, and
neither would show up as an error. What was verified against the real remotes, in a throwaway
container: `version`, `listremotes`, `check_remote` and `preview` still behave, and three 512KB
slices read through a crypt remote at offsets 0, 1 MB and 2 GB hash identical under 1.74.4 and
1.75.0. Building the old version into a temporary tag to compare against is the cheap way to get
that answer.

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

**`0004` adds the schedule windows** (2026-08-05), `schedule_hours` and `stop_outside_window` on
both `sync_jobs` and `download_jobs`: four ADD COLUMN with the DDL default filled in by
`process_revision_directives`, so a database with rows goes through untouched and every existing job
comes out with an always-open window. Verified with `check_migrations.py`, which seeds a row into
every table before upgrading.

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
