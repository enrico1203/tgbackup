# tgbackup

Keeps one or more local folders, or rclone remotes, mirrored inside private Telegram channels.
Uploads new files, deletes from the channel the ones that disappeared from the source, re-uploads
the modified ones, and can reassemble files that were split into parts.

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
- **Restore**: downloads all the parts and rebuilds the original file.
- **Export and import**: the index of a channel travels to another machine in one file, so that
  machine can restore everything the channel holds.

Photos and videos are always sent as documents, never as media: no recompression.

## Sources

**Local folder** mounted read-only into the container.

**rclone remote** read directly through the API, with no mount: `lsjson` to list and
`cat --offset --count` to read exact byte ranges. On a remote holding 12 TB across 16,709 files this
listed everything in 11.6 seconds against roughly 5 minutes walking a FUSE mount, and read at
43.5 MB/s against 22 MB/s. Nothing is staged on disk.

Both look the same to a sync job: split, deletion of what disappeared, re-upload of what changed and
restore work identically whichever the source is.

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

## Setup

```bash
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

Finally:

```bash
docker compose up -d --build
```

The interface answers on `http://127.0.0.1:8081` and on the Cloudflare tunnel hostname.
Initial credentials `admin` / `admin`, password change is mandatory on first sign in.

## Updating

```bash
git pull
docker compose up -d --build
```

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
   often to run and the scan rate. The job starts on its own, or with Run now.
5. **Files and restore**: everything tracked, grouped by channel, with parts and message ids linking
   straight to Telegram. Restore rebuilds a file into `data/restore/`.
6. **Export**: moves a channel to another installation.

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
want is to restore, leave them disabled and use Files and restore, which needs no run at all.

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
