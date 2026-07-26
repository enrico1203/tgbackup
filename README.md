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

Photos and videos are always sent as documents, never as media: no recompression.

## Sources

**Local folder** mounted read-only into the container.

**rclone remote** read directly through the API, with no mount: `lsjson` to list and
`cat --offset --count` to read exact byte ranges. On a remote holding 12 TB across 16,709 files this
listed everything in 11.6 seconds against roughly 5 minutes walking a FUSE mount, and read at
43.5 MB/s against 22 MB/s. Nothing is staged on disk.

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

## Usage

1. **Telegram account**: link an account with the api_id and api_hash from `my.telegram.org`, then
   the phone number, the code you receive and, if enabled, the two-step password. The session stays
   valid until you disconnect the account.
2. **Channels**: once linked, the account's private channels show up.
3. **Settings**: to use an rclone remote, paste your `rclone.conf` here. It is stored encrypted.
4. **Sync jobs**: pick the source (local folder or rclone remote), the account, the channel, how
   often to run and the scan rate. The job starts on its own, or with Run now.
5. **Files and restore**: everything tracked, grouped by channel, with parts and message ids linking
   straight to Telegram. Restore rebuilds a file into `data/restore/`.

## Technical documentation

Implementation choices, protocol constraints and project rules are in `CLAUDE.md`.
