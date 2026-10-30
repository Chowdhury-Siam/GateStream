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
**Bot settings:** Send `/bset` as the owner; `/dset` opens Drive setup. [Settings guide](BOT_SETTINGS.md).
**Mirror or clone to Google Drive:** [Google Drive setup](GOOGLE_DRIVE.md), optional and disabled by default.

## What you can do

- **Index your channels.** Add one file with `/link`, or an episode range with `/batch`.
- **Browse two file categories.** `/index` is public for indexed batches and episodes; the **Link** tab protects single-file links with a password. Select multiple files and download one ZIP. Initial Link password: `Siam`; the owner changes it with `/setpassword <new password>`.
- **Find your media.** Search movies, anime, series, and direct files from Telegram.
- **Follow transfers live.** The home page shows active links, HTTP connections, Drive downloads/uploads, byte counts, progress, speed, elapsed time and ETA.
- **Watch your way.** Play in the browser, resume playback, or open VLC or MX Player.
- **Share a link.** Create stream, download, and API URLs; revoke access by removing a link.
- **Manage the library.** Inspect server stats, backfill durations, and back up or restore records.
- **Mirror and clone to Drive.** Admins can use `/mirror <direct-url>` or `/clone <drive-link>` and receive Google Drive / optional Bhadoo index links. Existing `/drive` aliases still work; enable only on permitted hosting.
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
| `/bset`, `/botsettings` | Owner-only stacked settings menu; persistent runtime config and private token upload |
| `/dset`, `/driveset` | Owner-only Google Drive setup menu |
| `/cancelsettings` | Cancel a pending settings reply |
| `/drive <url>`, `/mirror <url>` | Admin-only: mirror a direct file URL to Google Drive; Drive URLs automatically clone |
| `/clone <drive-link>` | Admin-only: server-side copy a Drive file or folder tree |
| `/drivestatus`, `/canceldrive` | Admin-only: inspect or cancel the current Drive job |

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
| `DRIVE_INDEX_URL` | Empty | Optional HTTPS Bhadoo folder URL corresponding exactly to the destination |
| `DRIVE_CLONE_MAX_FILES` | `1000` | Maximum files + folders per clone snapshot, including the root |
| `DRIVE_MAX_MB` | `1024` | Maximum downloaded file size in MiB |

## HTTP endpoints

| Endpoint | Response |
| --- | --- |
| `/` | GateStream landing page |
| `/api/activity` | Live aggregate transfer counters and anonymous progress rows |
| `/index/archive/<token>/status` | ZIP preparation progress for its temporary link |
| `/health`, `/ping` | `OK` when the web server is responding; `X-GateStream-Version` identifies the deployed release |
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

Select individual checkboxes or **Select all on page**, optionally enter a
**ZIP name** without its extension, then press **Download ZIP** to receive up to 30 files in one ZIP. Selection applies to the current
page; changing the page or filters resets it. The server checks every selected
code against its section, so a public ZIP request cannot include private Link
records. ZIP64 supports large files.

Version 2.0.7 retains two choices before downloading selected files:

| Option | Starts | Resume and multiple connections |
| --- | --- | --- |
| **Start immediately** (default) | Sends available ZIP bytes while the shared archive is still being built | Yes; later ranges wait for their bytes |
| **Prepare first** | Waits for the complete temporary ZIP, showing progress | Yes, with all ranges ready |

The compact Library / Private links controls accept a custom ZIP name before
starting either mode. `.zip` is appended automatically (pasting the extension
also works), Unicode names are supported, and unsafe filename characters are
removed/replaced. Blank uses the previous `GateStream-index.zip` /
`GateStream-link.zip` default. A name is at most 120 characters and 240 UTF-8
bytes before the extension. The canonical name is stored in the download ticket
and used for GET, HEAD, resume/ranges and prepared redirects. It never becomes
a local cache path. Existing download links remain valid.

Immediate mode now writes one shared temporary ZIP in the background. The first
connection can read the opening bytes before the ZIP finishes; concurrent range
requests read the same stable bytes and never start duplicate Telegram fetches.
HEAD, Content-Length, 206 ranges, suffix/open ranges, ETags and If-Range work
before and after completion. Pausing a client does not stop the shared builder.
A connection requesting bytes not yet built waits for them; more connections do
not make Telegram preparation faster. A manager/proxy may time out a waiting
range and retry. Prepared mode avoids these preparation waits during download.

Both modes reserve their total size in the same 8 GiB temporary-disk budget.
Up to two live ZIP builders run independently of the one-at-a-time preparation
queue. Extra live jobs or selections exceeding capacity fail with a clear
message. All clients share one builder per ticket, so connection count does not
multiply Telegram source reads. Since 2.0.6, each builder overlaps up to four
continuous 4 MiB Telegram range streams instead of feeding the ZIP through one
serial source stream. Ordered queues bound buffering to roughly 16–20 MiB per
builder. A stalled range retries from its last complete chunk, preserving byte
order without duplicating already read data. The first real chunk warms each
worker’s media session before overlapping range reads, avoiding concurrent
session-start races. Browser playback and individual
file downloads retain their existing paths. Detached head/tail prefetch is
skipped for internal ZIP reads, avoiding extra competing requests.

The live download panel shows ZIP buffer percentage and its five-second fill
rate. This measures bytes arriving in the ZIP cache, not your phone's download
speed. If the fill rate drops while the client is waiting, the source side is
the limiting stage; if the ZIP is fully buffered, remaining variability is in
disk serving, the proxy, or the client/network. Parallel reads overlap latency
but cannot guarantee constant bandwidth or remove Telegram/hosting limits. GET links last 24 hours and retain the same
section and password-revocation rules. Older streaming links remain single
connection; make a new selection to use the live range support.

The following completed-file details apply to **Prepare first**.

Selected files are prepared into one temporary, uncompressed ZIP before the
client download starts. The page shows queued/preparing progress and
automatically starts the download when ready. Preparation uses continuous
Telegram reads with stalled-read recovery; it does not keep whole files in RAM.
The completed ZIP is immutable and served from disk, so clients can pause,
resume, and use multiple simultaneous connections without rebuilding the ZIP or
rereading earlier Telegram data. Preparation traffic is not counted as bytes
sent to viewers; actual ZIP downloads are counted by the activity dashboard.
Actual download speed still depends on the host, proxy and network.

The **Download ZIP** and **Copy download link** controls provide a GET `.zip`
URL that works without browser cookies or POST data. HEAD, byte ranges, 206
responses, ETags and If-Range validators are supported. Suffix/open-ended ranges
work, and invalid or multipart ranges receive 416. Download managers can use
multiple connections (start with four). The completed archive is retained until its link
expires, 24 hours after selection. Anyone holding the URL can download its
selected files during that time. Changing the Link password invalidates private
ZIP links, including subsequent range requests. Removing or changing a selected
catalog record also prevents new requests.

Both ZIP modes have a shared 8 GiB disk budget, configurable with
`ZIP_CACHE_MAX_MB`; `ZIP_CACHE_DIR` optionally chooses its directory. One prepared-mode ZIP is
built at a time, alongside at most two live builds, to keep memory bounded. Space is
reserved for queued jobs, and requests exceeding the budget or available free
disk fail with a clear message. Unexpired ZIPs are not evicted to make room;
expired files and interrupted preparations are cleaned on subsequent selections.
If capacity is full, select fewer files, wait for expiry, or increase the budget
where disk space permits. A host rebuild can remove temporary storage; completed
ZIPs can resume after a normal process restart when their files remain. If a ZIP
is lost, its old link fails with 410 instead of silently generating different
bytes under the same URL. Choose a fresh selection after a rebuild or failure.
If a live build is interrupted before completion, its partial file is removed;
choose a new selection. A completed ZIP retains the same ETag after a normal
process restart if its cached file survives.

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
not access settings, scan checkpoints, or temporary download tickets and ZIP cache files. When
migrating only that backup, set the Link password again and restart any scan;
existing public records are reused. Moving the full database preserves settings
and checkpoints.

## Automatic media categories

AniList identifies anime without a key. Optional host Secrets `MAL_CLIENT_ID`
and `OMDB_API_KEY` enable MyAnimeList fallback and OMDb movie/series matching,
including IMDb IDs and available Rotten Tomatoes ratings. Unknown/conflicting
results remain Direct files. See [CHANNEL_INDEX.md](CHANNEL_INDEX.md).

## Live activity dashboard

The compact home page shows separate **Single files downloaded** and **ZIPs
downloaded** totals, with live bandwidth/Drive cards in an expandable panel.
New totals begin with this update: previous aggregate HTTP request counters
cannot reconstruct how many entire files were downloaded. Completion means
all resource bytes were handed to completed response streams. It is not proof
that the client saved the file to disk. HEAD, inline playback, ZIP building and
range probes do not increment these new totals; overlapping HTTP ranges for
the same anonymous client/resource are combined. A ZIP counts once, not once
per selected file or range connection. Downloads served by an external Drive
index do not pass through GateStream and are not included in these totals.

In-flight range coverage is kept in memory for up to 24 hours and 1024 anonymous
client/resource pairs (at most 256 disjoint ranges each). It resets on restart,
so a download resumed across a server restart may not count as a new completion.
Client grouping uses a non-public hash of connection address and user agent;
clients sharing both may be grouped. New range sequences starting at byte zero
after a ten-second completion grace can count again. These are server-side
delivery totals, not exact per-person analytics. Completed totals persist using
the same five-second MongoDB checkpoint as existing counters.

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

## Settings inside Telegram (2.0.8)

Open `/bset` in the owner's private chat for Config Variables, Module Settings,
Google Drive, Private Files and Status buttons. `/dset` opens Drive setup.
Upload your own OAuth `token.json` in the prompted private reply, choose its
destination and optional index URL, check access, then enable transfers. The
existing host token.pickle settings remain supported. Private values are encrypted
in the current MongoDB settings collection; runtime overrides restore before
the bot starts accepting commands. Host identity/database settings and HF
metadata are preserved. Read [BOT_SETTINGS.md](BOT_SETTINGS.md) for supported
variables, token conversion, encryption-key rotation and input/privacy limits.
