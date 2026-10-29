<div align="center">
  <img src="bot/server/templates/brand.svg" width="88" alt="GateStream gateway logo">
  <h1>GateStream</h1>
  <p><strong>Gateway to your media.</strong></p>
  <p>Index your Telegram library. Open a gate. Press play.</p>
</div>

GateStream turns media in your Telegram channels into browser playback,
direct downloads, and API links. The bot manages your catalog in MongoDB;
the server retrieves media from Telegram as it streams. A dark interface,
warm gold accents, and a gateway mark carry the identity from bot to browser.

## Choose your package

| Package | Purpose |
| --- | --- |
| `GateStream-GitHub.zip` | Full source, Cloudflare Worker, GitHub Actions deployment, and tests |
| `GateStream-HuggingFace.zip` | Docker Space hosting files, bot, player, and Python tests |

Extract the ZIP and upload the **contents** of its project folder, so `README.md`,
`Dockerfile`, and `bot/` sit at the repository root.

**Deploy the bot:** [Hugging Face guide](HUGGING_FACE.md) or the local/Docker steps below.
**Deploy the edge proxy:** [Cloudflare guide](CLOUDFLARE.md), included in the GitHub package.
**Change the Telegram profile:** [Branding guide](BRANDING.md).
**Transfer a direct file to Drive:** [Google Drive setup](GOOGLE_DRIVE.md), optional and disabled by default.

## What you can do

- **Index your channels.** Add one file with `/link`, or an episode range with `/batch`.
- **Browse two file categories.** `/index` is public for indexed batches and episodes; the **Link** tab protects single-file links with a password. Select multiple files and download one ZIP. Initial Link password: `Siam`; the owner changes it with `/setpassword <new password>`.
- **Find your media.** Search movies, anime, series, and direct files from Telegram.
- **Follow transfers live.** The home page shows active links, HTTP connections, Drive downloads/uploads, byte counts, progress, speed, elapsed time and ETA.
- **Watch your way.** Play in the browser, resume playback, or open VLC or MX Player.
- **Share a link.** Create stream, download, and API URLs; revoke access by removing a link.
- **Manage the library.** Inspect server stats, backfill durations, and back up or restore records.
- **Send a direct file to Drive.** Admins can use `/drive <url>` with a trusted OAuth `token.pickle`; enable only on hosting that permits file transfers.
- **Add an edge entry point.** Deploy the optional Cloudflare streaming reverse proxy from GitHub Actions.

Media remains in Telegram. Selected opening and ending chunks are cached on
the host's disk. Extra bot tokens support parallel retrieval; actual speed
depends on Telegram, your host, and the network. The Worker passes media through
without buffering the whole file; it does not create a distributed media cache.

## Quick start on a local machine or VPS

Use Python 3.11 or newer, a MongoDB endpoint reachable from the host, and a
Telegram bot created through BotFather. Give the bot access to your source channels.

```bash
git clone https://github.com/Chowdhury-Siam/GateStream.git GateStream
cd GateStream
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

On Windows, activate with `.venv\Scripts\activate` and copy `.env.example` to `.env`.
Fill in the required values below, set `BASE_URL` to your public server or Worker
address, then start the bot:

```bash
python -m bot
```

Project repository: [Chowdhury-Siam/GateStream](https://github.com/Chowdhury-Siam/GateStream).
Run only one deployment per main Telegram bot token.

## Configuration

Use `.env` locally. On Hugging Face, use **Settings → Variables and secrets**;
do not upload a filled `.env` file.

| Setting | Required | Purpose |
| --- | --- | --- |
| `TELEGRAM_API_ID` | Yes | Numeric Telegram API ID |
| `TELEGRAM_API_HASH` | Yes | Telegram API hash |
| `TELEGRAM_BOT_TOKEN` | Yes | Main BotFather token |
| `OWNER_ID` | Yes | Your positive numeric Telegram user ID |
| `DATABASE_URL` | Yes for hosted deployments | MongoDB URI; local default is `mongodb://localhost:27017` |
| `BASE_URL` | Outside Spaces | Public URL used in generated links; Spaces otherwise uses `SPACE_HOST` |
| `DATABASE_NAME` | No | Catalog database; legacy default retained as `OmniArchiver` |
| `MULTI_BOT_TOKENS` | No | Additional bot tokens, separated by spaces or commas |
| `AUTH_USERS` | No | Additional administrator IDs |
| `ALLOWED_USER_IDS` | No | Telegram users allowed to issue commands; blank allows everyone |
| `TELEGRAM_CHANNEL_ID` | No | Storage channel for files uploaded directly to the bot |
| `TELEGRAM_BOT_USERNAME` | No | Username fallback; detected from the bot when connected |
| `OMNI_MONGO_URI` | No | Compatibility setting for a separate upstream catalog |
| `PORT` | No | `8080` locally; `7860` in the Docker image and on Spaces |
| `BIND_ADDRESS` | No | Listening address; defaults to `0.0.0.0` |

### Public host and Cloudflare

On the bot host, `BASE_URL` is the address users should open. For your existing
Worker deployment:

```dotenv
BASE_URL=https://ftl-stream-proxy.ftl-556.workers.dev
```

In GitHub Actions settings, `CLOUDFLARE_ORIGIN_URL` is the **direct hosting
address**, not a `/stream/...` link and not the Worker URL. Example:

```text
https://siamreal-hfvps.hf.space
```

The GitHub version includes a workflow that accepts that setting as a repository
Variable or Secret. Cloudflare credentials belong in GitHub Secrets. See the
Cloudflare guide for all settings and the deployment steps.

## Commands

| Command | What it does |
| --- | --- |
| `/start`, `/help` | Show GateStream's command guide |
| `/search <name>` | Search the media catalog |
| `/search_api <name>` | Return API links for matching media |
| `/link [category] <message_link>` | Index a channel file |
| `/batch anime <start_link> <end_link>` | Index anime episodes |
| `/batch series <start_link> <end_link>` | Index series episodes |
| `/episodes <batch_code>` | List a batch's episodes |
| `/index <last_post_link>` | Automatically scan a channel, resume progress and identify media categories |
| `/index @channel` | Continue up to the latest post observed by the bot |
| `/cancelindex` | Admin only: stop the current scan and retain progress |
| `/catalog`, `/index` without arguments | Open public Index and protected Link files |
| `/setpassword <new password>` | Owner only: change the shared Link password and invalidate existing sessions |
| `/privacy` | Explain stored data and link access |
| `/drive <url>` | Admin-only: download a direct file URL and upload it to Google Drive when enabled |

Administrator commands include `/stats`, `/workers`, `/log`, `/sync_duration`,
`/set_title`, `/revoke`, `/clean`, `/backup`, `/restore`, `/purge`, `/restart`,
and `/sh`. `/sh` executes host commands, so grant administrator access only to
people you trust with the server.

Automatic channel indexing: add the bot as an admin and send the latest post link. See [CHANNEL_INDEX.md](CHANNEL_INDEX.md) for setup, metadata credentials, resume and cancellation.

```text
/index https://t.me/c/1234567890/5000
/index @YourChannel
/cancelindex
```

### Optional Google Drive settings

See [GOOGLE_DRIVE.md](GOOGLE_DRIVE.md) for token setup, limits and hosting requirements.

| Setting | Default | Purpose |
| --- | --- | --- |
| `DRIVE_ENABLED` | `false` | Enable admin-only Drive transfers on permitted hosting |
| `DRIVE_TOKEN_PATH` | `token.pickle` | Path to an owner-provided OAuth token file |
| `DRIVE_TOKEN_PICKLE_B64` | Empty | Host Secret containing the token encoded as base64; takes precedence over the file |
| `DRIVE_FOLDER_ID` | Empty | Destination folder ID; blank uses My Drive root |
| `DRIVE_MAX_MB` | `1024` | Maximum downloaded file size in MiB |

## HTTP endpoints

| Endpoint | Response |
| --- | --- |
| `/` | GateStream landing page |
| `/api/activity` | Live aggregate transfer counters and anonymous progress rows |
| `/health`, `/ping` | `OK` when the web server is responding |
| `/stream/<code>` | Browser player |
| `/dl/<code>` | Download; add `?stream=1` for inline media |
| `/api/file/<code>` | File details and links |
| `/api/batch/<code>` | Batch details and episode links |
| `/index` | Public Index tab and password-protected Link tab, search, pagination and multi-file ZIP downloads |
| `/api/movies` | Movie catalog response; requires a Link login cookie |

Anyone holding a valid media URL can access it. Telegram user restrictions
apply to bot commands, not HTTP playback. Index and share media you have
permission to distribute.

## Docker

```bash
docker build -t gatestream .
docker run -d --name gatestream -p 8080:7860 --env-file .env -e PORT=7860 --restart unless-stopped gatestream
```

Set `BASE_URL` for the public address mapped to this container. For Spaces, use `GateStream-HuggingFace.zip`; its README includes the Docker
SDK metadata and port `7860` configuration.

## Public Index and protected Link

Open `/index` on your server or Worker, or send `/index` to the Telegram bot.
There are two tabs:

| Tab | Files | Access |
| --- | --- | --- |
| **Index** (`/index`) | Automatically scanned channel files plus existing indexed batches and episodes | Public; no password or Telegram login |
| **Link** (`/index?section=link`) | Single-file links created by `/link`, its movie/anime/series aliases, and direct bot uploads | Shared password required |

Existing records without a section flag keep their original rule: batches go
to Index and single-file records go to Link. New `/index` scans create public
records, while `/link` records stay private. This keeps old `/link` files out of the public
listing. New records also store their section explicitly. Media category filters
(Movies, Anime, Web series, Direct files), filename/batch-title search and
pagination work within each tab, with 30 files per page.

Select individual checkboxes or **Select this page**, then press **Download
selected** to receive up to 30 files in one ZIP. Selection applies to the current
page; changing the page or filters resets it. The server checks every selected
code against its section, so a public ZIP request cannot include private Link
records. ZIP64 supports large files. Files stream sequentially into the ZIP with
no whole-archive RAM buffer or temporary media copy. Each active ZIP source
uses up to four concurrent Telegram chunk reads, including with a single bot,
and emits them in order. This overlaps request latency; total throughput still
depends on Telegram, the host and the network. ZIPs are uncompressed and
cannot be resumed; for resumable downloads use the individual Download buttons.
Selection now creates a temporary GET URL ending in `.zip`, with a filename,
Content-Length and HEAD support. It works in an external download manager
without copying browser cookies or POST data. Use one connection; ZIP ranges
and resume are unavailable. The **Download ZIP** and **Copy download link**
controls let you retry or hand off the URL. The link is valid for one hour;
anyone holding it can download the selected files during that time. Changing
the Link password also invalidates outstanding private ZIP links. Expiry is
checked when starting a request; an active transfer can finish afterward.

If a source transfer fails, the ZIP stops; retry it or download files
individually. Keep the download running until the ZIP finishes.

The initial **Link** password is **`Siam`** (case-sensitive). If you previously
changed it, that saved password is retained. The account whose Telegram ID
matches `OWNER_ID` can change it in a private bot chat:

```text
/setpassword Your new password
```

`/indexpassword` is an alias. Passwords can contain spaces and must be 1–256
characters. Other administrators cannot change it. The bot confirms the change
without repeating the password and attempts to delete the command message.

A salted scrypt hash and signing secret persist in the MongoDB `settings`
collection under `index_access`. Signed HttpOnly/SameSite login cookies last
eight hours and are Secure on HTTPS. Changing the password invalidates existing
Link sessions. Login/logout/download forms have CSRF protection, repeated login
attempts are limited, and catalog responses are uncached. Public Index browsing
works without initializing password settings; Link access fails closed if
settings are unavailable.

The bulk `/api/movies` endpoint requires a Link login cookie. Existing per-file
and batch APIs, stream links and individual download URLs retain their sharing
behavior: the password protects Link catalog discovery. Visitors do not need a
Telegram account. The Cloudflare Worker forwards POST forms only for catalog
login, logout and selected downloads; redeploy it with this update.

The `/backup` command exports media collections, including new section fields,
not access settings, scan checkpoints, or temporary download tickets. When
migrating only that backup, set the Link password again and restart any scan;
existing public records are reused. Moving the full database preserves settings
and checkpoints.

## Automatic media categories

AniList identifies anime without a key. Optional host Secrets `MAL_CLIENT_ID`
and `OMDB_API_KEY` enable MyAnimeList fallback and OMDb movie/series matching,
including IMDb IDs and available Rotten Tomatoes ratings. Unknown/conflicting
results remain Direct files. See [CHANNEL_INDEX.md](CHANNEL_INDEX.md).

## Live activity dashboard

Open the server or Worker home page to follow transfers live. The dashboard
refreshes every two seconds while visible and slows polling in background tabs.
Media links are counted separately from HTTP range connections. It shows Drive
downloading/uploading counts, downloaded and uploaded bytes, data sent to viewers,
completed requests, failures, incomplete transfers and server uptime.

Active rows show progress, transferred/total bytes, average speed, elapsed time
and ETA. Unknown-size downloads use an indeterminate progress bar. Summary
throughput covers roughly five seconds. Only the latest 20 active rows are shown;
all active transfers are included in the counters. On a network failure the
previous values stay dimmed and the panel shows disconnected until it reconnects.

The public endpoint exposes aggregate counts and anonymous activity numbers,
without file names, URLs, file codes, Drive IDs, credentials or user IDs.
Cumulative bytes, completed counts and failed/incomplete counts are stored in
MongoDB `analytics`, document `transfer_activity`. They are restored at startup,
checkpointed every five seconds, and flushed during normal web shutdown and
`/restart`. Active connections, active rows, current speeds and uptime remain
live session values and correctly start at zero after a restart. Previously
active transfers from the saved checkpoint are counted as incomplete.

A hard kill or power loss can lose work since the last successful checkpoint
(normally up to five seconds); a database outage can extend that window. Failed
reads do not overwrite stored totals with zeros, failed writes are retried
without adding totals twice, and the dashboard shows a save/restore warning.
Chunks never wait for statistics database writes. Use the same `DATABASE_URL`
and `DATABASE_NAME` to preserve totals. This uses one writer per deployment;
it does not aggregate multiple replicas. Existing media bytes can be seeded
from the older `analytics.bandwidth` record on first upgrade. Historical Drive
counts and other totals that were never saved cannot be reconstructed. The
media-only `/backup` export does not include these analytics records; a full
database backup does.

Media byte totals measure
bytes handed to HTTP responses, not proof a client saved a whole file. Drive
upload bytes are acknowledged chunks; successful uploads are size-verified.
Failed/incomplete transfers retain their partial byte totals. The existing
one-at-a-time Drive limit still applies; no transfer queue has been added.

## Upgrading an existing deployment

1. Back up the database with `/backup` and retain your current hosting settings.
2. Replace the source files with the contents of the appropriate GateStream ZIP.
3. Keep the current database names, bot tokens, and Worker name, then restart the host.
4. Test `/health`, generate a file link, and check playback and seeking.

This release changes the identity and presentation. It retains MongoDB defaults
`OmniArchiver` and `OmniArchiverDB`, `OMNI_MONGO_URI`, file codes, HTTP routes,
Telegram session names, and the browser's existing resume keys. No database
migration is needed. Renaming storage merely for branding could hide existing
records, so these compatibility identifiers remain intentionally.

The existing deployment target remains `ftl-stream-proxy` so the workflow can
update your deployed Worker. To create a separately named Worker, set
`CLOUDFLARE_WORKER_NAME=gatestream-proxy` and update the bot's `BASE_URL` after
deployment; this is optional. You can also enter a Worker name directly under
**GitHub Actions → Deploy GateStream Worker → Run workflow**. The form overrides
the repository variable for that run only; save the repository variable to use
the same name for automatic deployments.

## Checks

```bash
python -m unittest discover -s tests -v
node --test cloudflare/worker.test.mjs
```

The Node check applies to the GitHub package. Python checks use mocked services
and do not need live Telegram or MongoDB connections.

## Project history

GateStream is Siam's rebrand of the supplied OmniArchiver-F2L project.
Original upstream attribution is preserved in [ACKNOWLEDGEMENTS.md](ACKNOWLEDGEMENTS.md).
