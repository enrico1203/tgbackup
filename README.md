# tgbackup

Keeps one or more local folders, or rclone remotes, mirrored inside private Telegram channels.
Uploads new files, deletes from the channel the ones that disappeared from the source, re-uploads
the modified ones, and can reassemble files that were split into parts. It also goes the other way:
a download job pours a whole channel back into a folder or an rclone remote.

## Start here

Nothing to clone and nothing to build. The images are published for `linux/amd64` and
`linux/arm64`, so an x86 server, a NAS and a Raspberry all pull the same tag. Save this as
`docker-compose.yml`:

```yaml
services:
  backend:
    image: enrico1203/tgbackup-backend:latest
    restart: unless-stopped
    environment:
      APP_SECRET: ${APP_SECRET}
      TZ: ${TZ:-Europe/Rome}
      DATA_DIR: /data
    volumes:
      # Database, rclone configuration and the files rebuilt by a restore
      - ./data:/data
      # The folders to back up, always read-only. The path inside the container is the
      # one to type into the job, so keeping the two sides identical helps.
      - /mnt/documents:/mnt/documents:ro
      # Destination of a download job: the only volume that is written, so without :ro,
      # and never one of the folders above.
      # - /mnt/restored:/mnt/restored

  frontend:
    image: enrico1203/tgbackup-frontend:latest
    restart: unless-stopped
    depends_on:
      - backend
    ports:
      # Only the machine itself. To reach it from the local network, drop the 127.0.0.1
      # and put something that terminates TLS in front of it.
      - "127.0.0.1:8081:80"
```

Then, in the same folder:

```bash
echo "APP_SECRET=$(openssl rand -hex 32)" > .env
docker compose up -d
```

The interface answers on `http://127.0.0.1:8081`, with `admin` / `admin` and a mandatory
password change on the first sign in. Keep `APP_SECRET`: it encrypts the Telegram sessions and
the rclone configuration in the database, and changing it means linking the accounts again.

That is the whole installation. The Setup section further down covers the same file with the
Cloudflare tunnel and with a version pinned instead of `latest`, and building from the sources
for changing the code.

## What it does

- **Low impact scanning**: a file's identity is `(path, size, mtime)`, read with a single `stat`
  locally or from the listing on remotes. File contents are never read, no hashing.
- **Fast upload**: up to 20 parallel MTProto connections on the same account.
- **Automatic split**: files above the threshold (3.9 GB with Telegram Premium, 1.9 GB without) are
  divided into parts, each with its own message id stored in the database.
- **A real mirror**: a renamed or modified file means deletion from the channel and a fresh upload,
  so the channel always matches the source.
- **Parallel jobs**: several folders, channels and Telegram accounts at once. The same job never runs
  twice concurrently, even when a run takes days.
- **Live progress**: upload speed, remaining files and estimated time over a WebSocket.
- **File explorer**: a channel browsed as a folder tree, read from the index, with search and any
  file sent to the browser, to a folder on the server or to an rclone remote.
- **Restore**: downloads all the parts and rebuilds the original file.
- **Download jobs**: a whole channel back into a folder or an rclone remote, on a schedule, skipping
  what is already there and deleting nothing.
- **Filters**: include and exclude patterns and a size ceiling per job, so temporary files, sample
  folders and anything too large stay out.
- **Check and rebuild**: the index can be verified against the channel, and rebuilt from it when the
  database is gone.
- **Notifications**: a report at the end of a run, in the Saved Messages of a Telegram account.
- **Export and import**: the index of a channel travels to another machine in one file, so that
  machine can restore everything the channel holds.
- **Update notice**: the dashboard says so when the images published on Docker Hub are newer than
  what this installation runs, naming which of the two is behind.

Photos and videos are always sent as documents, never as media: no recompression.

## Sources

**Local folder** mounted read-only into the container.

**rclone remote** read directly through the API, with no mount: `lsjson` to list and
`cat --offset --count` to read exact byte ranges. On a remote holding 12 TB across 16,709 files this
listed everything in 11.6 seconds against roughly 5 minutes walking a FUSE mount, and read at
43.5 MB/s against 22 MB/s. Nothing is staged on disk.

Both look the same to a sync job: split, deletion of what disappeared, re-upload of what changed and
restore work identically whichever the source is.

## Download jobs

A download job is a sync job the other way round. It takes what a channel holds and writes it to a
local folder or an rclone remote, on the same kind of schedule, and it is what turns an imported
index into files again: export the channel on the old machine, import it here, point a download job
at a folder and everything comes back, split files reassembled and dates restored.

What it can write is what the index knows: the files uploaded by a sync job of this installation, or
those of an index imported from another one. Every run lists the destination, compares by size and
downloads only what is missing, so a run with nothing to do costs one scan. Interrupt it and the
next run picks up where it stopped: a local file only takes its final name once complete, and a file
left short on a remote does not match the expected size.

**It never deletes anything at the destination.** A sync job deletes from the channel what has gone
from the source, because the channel is a copy this application owns. Your folder is not, and a file
the channel does not know about is not a mistake to correct.

**The destination has to be writable.** The folders to back up are mounted `:ro` on purpose, so a
download destination needs its own volume in `docker-compose.yml` without the `:ro` suffix, and it
should never be a folder a sync job reads. The form refuses a path that does not exist or cannot be
written, when the job is saved rather than in the middle of the night.

On a remote nothing is staged on disk either: the parts arrive from Telegram already in order and go
straight into `rclone rcat`.

## File explorer

Pick a channel and browse it as a folder tree: enter a folder, walk back up, follow the path in the
bar at the top, and use the back and forward arrows the way any file manager works. It reads well on
a phone, which is the point of it: getting one file back should not need a computer.

What is browsed is the index in the database, not Telegram. Opening a folder is one query and no API
call, so a channel with 200,000 files opens as fast as an empty one, and the size next to a folder
counts everything below it, not just what sits directly inside. Only files that reached Telegram
appear: something still waiting to be uploaded is not there to be opened.

**Search** looks through the folder you are in and everything below it, by name, ignoring case, and
finds folders as well as files: a folder deep in the tree is worth finding too. It reads the rows
already loaded for the listing, so it costs no second query and no call to Telegram. Opening a
result leaves the search, the way a file manager does.

**A file split into parts is one file.** The split belongs to the transport, and the explorer shows
what was backed up.

Downloading asks where it should go:

- **This device**: the parts are fetched in parallel, joined in order and streamed to the browser as
  they arrive, so nothing is written to the server first and a 40 GB file needs no 40 GB of free
  space anywhere. There is no resume, an interrupted download starts again from the beginning, which
  is the price of never staging the file.
- **A folder on the server**: written in the background, so you can leave the page and follow it in
  the panel at the top. The folder has to be a writable volume, which is what stops a download from
  landing inside what a sync job reads: those are mounted read-only and are refused here.
- **An rclone remote**: straight into the remote through the API, nothing staged on disk. The Browse
  button opens the remote and picks a folder.

The last two are the same write a download job performs, for one file instead of a channel, and in
both the file keeps the path it has inside the channel. Restore on the Files page still exists and
still rebuilds into `data/restore/`: it is the same operation with the destination decided for you.

A download spends from the same budget of 20 connections per data center that the jobs share, so it
queues behind a running upload on that account exactly as a second job would.

The link the browser follows carries a pass valid for that one file and five minutes, not your
session: a URL ends up in the browser history and in the nginx log, and a session token has no
business being in either.

## Rclone remotes

Any backend rclone supports can be a source, `crypt` included, without installing rclone yourself:
the official static binary is in the image.

**Configuration**: paste your `rclone.conf` in Settings. On save rclone reloads it and the remotes
are listed right away, so a broken configuration is rejected instead of being discovered by a job at
three in the morning. It is stored encrypted in the database with the key derived from `APP_SECRET`,
and the database is the source of truth: the file on disk exists only because rclone wants a file,
it is rewritten at every start with permissions `0600`, and it never leaves for the browser except
when you press Edit.

Edit the configuration keeps what is there and lets you fix or add a section; Rewrite from scratch
starts from an empty box. Remove deletes it, and any job using a remote stops working.

**Browsing**: pressing a remote in Settings opens a browser that walks the folders and shows the
first entries of each, with the full path ready to copy. From the job form the Browse button does the
same and Use this path fills the field. Reading stops at the first twenty entries, so opening a folder
holding tens of thousands of files costs the same as opening an empty one.

**In a job**: pick Rclone remote as the source and give a path in rclone's own form, `remote-name:`
for the whole remote or `remote-name:subfolder` for part of it. The remote is contacted when the job
is saved: if it does not answer, the job is not created.

**During a run**: listing a large remote takes minutes, and the progress shows how many files have
been found so far and where it is, instead of staying silent until the end. Files are then read in
the exact byte ranges each Telegram part needs, straight from the stream to the upload.

The timeouts on the rclone commands are safety nets against a remote that never answers, not limits
on how much work is allowed: a full listing may take up to six hours, browsing and checks ten minutes.

## Filters

Every sync job can be told what to leave out, in the job form. One pattern per line:

```
*.tmp              the name, at any depth
.DS_Store          same
sample/            everything under a folder with that name
**/Trash/**        crosses folders
Films/*/sample.mkv the whole relative path
```

`*` stays inside one path segment, `**` crosses them, `?` is one character, and case is ignored, so
`*.mkv` also catches `FILM.MKV`. Include works the same way: leave it empty for everything, fill it
in and only what matches is backed up. Exclude always wins. There is also a ceiling on the size of a
single file.

The same matcher runs whichever the source is. rclone has filters of its own, but a pattern that
meant one thing on a remote and another on a local folder would show up only as files quietly
missing from a backup.

One thing to know: a filter is not only about what comes next. A file already in the channel that
the filter now leaves out is missing from the listing, so the next run sees it as removed and
deletes it from the channel, exactly as if it had disappeared from the source. That is how you clean
up a channel that has years of `.DS_Store` in it, and it is also how you lose something if a pattern
has one character too many.

## Schedule windows

Every job, sync or download, carries a grid of seven days by twenty-four hours in its form. Drag
across it to paint the hours the job is allowed to run in, click a day or an hour to toggle the whole
line, or start from one of the presets: always, nights, outside office hours, weekend only.

The window does not replace the interval, it gates it. A job still becomes due every so many hours
from the end of its previous run, and the window decides whether it may start at that moment: if the
moment falls outside, the job waits for the next opening rather than losing the run. Pressing Run now
ignores the window entirely, because that is an explicit order.

A run that started inside the window and is still going when the window closes is left alone by
default. Tick "Stop a run in progress when the window closes" and it stops at its next checkpoint
instead: the parts already sent stay on Telegram, the next run is not pushed forward by a whole
interval, and the job picks up again at the next opening. This is what turns a window into a real
constraint on when your line is busy, and it is safe because every part is recorded as it is sent.

The hours are read in the timezone set in Settings, which also decides how the window follows the
change of season. Everything else, run times included, is kept in UTC.

## Notifications

A report at the end of a run, in the Saved Messages of a Telegram account. Nothing to install: the
account is already connected, and every account has a chat with itself. In Settings, choose whether
to send nothing, only failures, or every run, and which account carries them.

A failure is a run that ended in error or that left files behind. The message says which job, what
it was working on, what it examined and uploaded, how long it took and what went wrong.

## Checking and rebuilding a channel

The files live on Telegram, but the knowledge of what they are lives in this database. The
Maintenance page keeps that relationship honest, in both directions. Pick an account and a channel,
any channel of that account, even one with no job.

**Check** goes from the index to the channel. It asks Telegram for every message the index records
and reports the ones that are gone or that carry a file of the wrong size, which is how you find out
that something was deleted by hand before the day you need it back. It can mark the damaged files in
the same pass, and the sync job uploads them again on its next run.

**Rebuild** goes the other way. It reads every message of the channel and puts the index back
together out of the captions, which carry the file name, the folder and the part number. This is
what a lost database needs: the files are still up there, and without the index nobody knows what
they are. The entries go into a new job, which arrives disabled and with no source, or into an
existing job on that channel. Paths the index already knows are left alone, so it can be run again
safely.

An export is still the better way to move a channel, because it carries the dates as well and takes
one second. A rebuild only exists for when there is no export.

What the channel cannot give back is the modification time: it is nowhere in a message. Rebuilt
files are marked as having an unknown date, and the first scan of the job takes the date of the
source instead of treating everything as modified and uploading the whole channel a second time.

Neither operation can run while a sync job is uploading to that channel, since they change the
index that job is writing. A check that only reports is always allowed.

## Setup

Two ways in. The published images ask for nothing but Docker and two files; building from the
sources is for changing the code.

### From the published images

The compose file at the top of this page is enough to start. The one in the repository is the
same with the tunnel and the version pinning already written out:

```bash
curl -LO https://raw.githubusercontent.com/enrico1203/tgbackup/main/docker-compose.hub.yml
curl -L -o .env https://raw.githubusercontent.com/enrico1203/tgbackup/main/.env.example
```

Fill in `.env` as described below, list the folders to back up in `docker-compose.hub.yml`, then:

```bash
docker compose -f docker-compose.hub.yml up -d
```

The images are `enrico1203/tgbackup-backend` and `enrico1203/tgbackup-frontend` on Docker Hub.
`latest` follows the releases; `TGBACKUP_TAG=0.1.0` in `.env` pins the installation to one version,
and `TGBACKUP_TAG=0.1` keeps it on the fixes of that minor. The image tags carry no `v`, unlike the
git tag they are built from.

The Cloudflare tunnel is optional here and stays down unless it is asked for, since without it the
interface is still on `127.0.0.1:8081`:

```bash
docker compose -f docker-compose.hub.yml --profile tunnel up -d
```

### From the sources

```bash
git clone https://github.com/enrico1203/tgbackup.git
cd tgbackup
cp .env.example .env
```

Fill in `.env`:

- `CLOUDFLARE_TUNNEL_TOKEN`: from Cloudflare Zero Trust, Networks, Tunnels. The tunnel must point at
  `http://frontend:8081`.
- `APP_SECRET`: generate with `openssl rand -hex 32`. It signs the tokens and encrypts the Telegram
  sessions and the rclone configuration in the database. Changing it means linking the accounts again.

Then add the folders to back up in `docker-compose.yml`, service `backend`, as read-only volumes:

```yaml
      - /mnt/documents:/mnt/documents:ro
```

The destination of a download job is the one volume that goes in without `:ro`, and it must not be
one of the folders above:

```yaml
      - /mnt/restored:/mnt/restored
```

Finally:

```bash
docker compose up -d --build
```

The interface answers on `http://127.0.0.1:8081` and on the Cloudflare tunnel hostname.
Initial credentials `admin` / `admin`, password change is mandatory on first sign in.

## Updating

The dashboard carries a banner when a newer release exists: the backend reads the tags of the two
Docker Hub repositories, at most once every six hours, and each image knows the version it was built
from. Backend and interface are reported separately, since they are pulled separately and one can
be older than the other. Nothing about the installation is sent. The exact versions are
on the Settings page, banner or no banner.

From the published images:

```bash
docker compose -f docker-compose.hub.yml pull
docker compose -f docker-compose.hub.yml up -d
```

From the sources:

```bash
git pull
APP_VERSION="$(git describe --tags --always --dirty | sed s/^v//)" \
  docker compose up -d --build
```

`APP_VERSION` is what the images tell the update banner they are. It is taken from git rather than
written down, so it can never claim something the working copy has stopped being: on a checkout
sitting exactly on a release tag it is that version, and anywhere else it is a description of the
commit, which is not a version and is compared with nothing. Leaving it out builds the images as
"dev", which works exactly the same and simply never mentions updates.

The database migrates itself. The backend runs the pending Alembic revisions at start, so a version
that adds a column or an index needs nothing beyond the rebuild, and starting a version that changes
nothing does nothing. Databases created before Alembic was introduced are adopted at the first start
on the new version, with no dump and no reload.

Migrations run before the interface answers: if one fails the backend does not start, and the reason
is the last thing in `docker compose logs backend`.

## Usage

1. **Telegram account**: link an account with the api_id and api_hash from `my.telegram.org`, then
   the phone number, the code you receive and, if enabled, the two-step password. The session stays
   valid until you disconnect the account.
2. **Channels**: once linked, the account's private channels show up.
3. **Settings**: to use an rclone remote, paste your `rclone.conf` here. It is stored encrypted, and
   the remotes it declares can be browsed from the same page.
4. **Sync jobs**: pick the source (local folder or rclone remote), the account, the channel, how
   often to run, the scan rate and what to leave out. The job starts on its own, or with Run now.
5. **Download jobs**: pick the channel to bring back, the destination (writable folder or rclone
   remote) and how often to run. Nothing is ever deleted at the destination.
6. **File explorer**: a channel browsed as a folder tree, with any file downloaded straight to the
   browser, phone included.
7. **Files and restore**: everything tracked, grouped by channel, with parts and message ids linking
   straight to Telegram. Restore rebuilds a single file into `data/restore/`, while a download job
   brings back a whole channel.
8. **Export**: moves a channel to another installation.
9. **Maintenance**: checks a channel against the index, or rebuilds the index by reading it.

## Changing the account of a job

The Telegram account a job runs with can be changed after the job was created: open the job, pick
another account and save. The channel goes with it, and so does everything already in it. Nothing is
uploaded again and nothing is downloaded again, because what the index holds are message ids and
those belong to the channel, not to the account that sent them.

Two conditions on the new account. It has to be a member of the channel, the permission to read one
being issued per account: the save reads that account's own channel list to find it and refuses if it
is not there. And to remove files it has to be able to remove them, which for messages another
account sent means being an admin with the right to delete messages. Uploading and downloading need
only membership.

An automatic check configured on that channel follows the job, as long as the job was the last one
writing there.

## Moving a channel to another machine

The files live on Telegram, the knowledge of what is up there lives in this database. Export carries
that knowledge across, one channel at a time.

On the machine that has the channel, open Export, press Export next to it and keep the
`.json.gz` file. It holds the channel coordinates, the jobs writing to it and, for every file, its
path, size, modification time and the message ids of its parts. It holds no file content and no
Telegram credentials: 2,500 files with 7,500 parts weigh 65 KB.

On the other machine, link the Telegram account that is a member of that channel, then open Export,
choose the file and press Import. The file is read first and shows what it holds before anything is
written.

Two things to know.

**The imported jobs arrive disabled, on purpose.** Their source is a path on the machine they came
from. Point each one at the right folder or remote before enabling it: a job whose source exists but
holds something else sees every file as removed and empties the channel on its first run. If all you
want is the files back, leave them disabled and create a download job on that channel, which reads
the index and writes nothing to Telegram. A single file is quicker through Files and restore.

**The Telegram account matters.** The permission to read a channel is issued per account, so the
import wants an account that is a member of it, ideally the same one. If it is connected the channel
is verified against Telegram there and then; if it is not, the import still goes through and says so.

Importing the same file twice creates a second copy of the jobs. To take an updated export instead,
tick "Merge into jobs with the same name": paths already known are left untouched and only the new
ones are added.

## Checks

Every push and pull request runs two GitHub Actions workflows.

`CI` lints the backend with Ruff, type-checks and builds the frontend, builds both images, and boots
the backend on an empty data directory to confirm it answers and reaches the latest schema revision.
It starts it a second time to confirm the migrations are idempotent. It runs `alembic check`, which
fails when a model has changed and no revision was written, and it applies the migrations to a
database seeded with rows, because SQLite accepts on an empty table plenty of things it refuses on a
full one.

`Vulnerability scan` runs Trivy over the dependency manifests, the Dockerfiles and both built
images, and repeats every Monday because advisories appear against code that has not changed. All
findings land in the Security tab; the build fails only on the serious ones that already have a fix
available. Exceptions live in `.trivyignore`, each with a reason and an expiry date.

There is no test suite. Nothing here asserts what a job does with a file, which would need a real
Telegram account and a real remote.

`Publish images` is not run by a push. It runs on a `v*` tag, or from the Actions tab, and it builds
each architecture on a runner of that architecture, `linux/amd64` and `linux/arm64`, pushing both
under one manifest per image. A tag `v1.2.3` publishes `1.2.3`, `1.2` and `latest`; the button
publishes `latest` alone.

## Technical documentation

Implementation choices, protocol constraints and project rules are in `CLAUDE.md`.

## License

GNU Affero General Public License v3.0, in `LICENSE`.

Copyright (C) 2026 Enrico, <https://github.com/enrico1203>

Use it, run it, change it, for yourself or inside your organisation, with nothing asked in return.
The one obligation appears when you pass it on: if you distribute a modified version, or let other
people use one over a network, that version's source has to be available to them under this same
licence. Running the stock version for yourself, which is what this project is for, carries no
obligation at all.

The dependencies are all permissive, MIT, BSD, Apache 2.0, ISC and CC0, so they are free to be used
here. That works in one direction only: code from this project cannot be moved into a closed one.
