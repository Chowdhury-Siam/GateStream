---
title: GateStream
emoji: ✦
colorFrom: yellow
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---

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
- **Find your media.** Search movies, anime, series, and direct files from Telegram.
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
git clone https://github.com/Chowdhury-Siam/FTL.git GateStream
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

The repository may retain its existing `FTL` name on GitHub; the product is GateStream.
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
| `/privacy` | Explain stored data and link access |
| `/drive <url>` | Admin-only: download a direct file URL and upload it to Google Drive when enabled |

Administrator commands include `/stats`, `/workers`, `/log`, `/sync_duration`,
`/set_title`, `/revoke`, `/clean`, `/backup`, `/restore`, `/purge`, `/restart`,
and `/sh`. `/sh` executes host commands, so grant administrator access only to
people you trust with the server.

Example channel indexing:

```text
/link https://t.me/c/1234567890/42
/batch anime https://t.me/c/1234567890/10 https://t.me/c/1234567890/25
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
| `/health`, `/ping` | `OK` when the web server is responding |
| `/stream/<code>` | Browser player |
| `/dl/<code>` | Download; add `?stream=1` for inline media |
| `/api/file/<code>` | File details and links |
| `/api/batch/<code>` | Batch details and episode links |
| `/api/movies` | Movie catalog response |

Anyone holding a valid media URL can access it. Telegram user restrictions
apply to bot commands, not HTTP playback. Index and share media you have
permission to distribute.

## Docker

```bash
docker build -t gatestream .
docker run -d --name gatestream -p 8080:7860 --env-file .env -e PORT=7860 --restart unless-stopped gatestream
```

Set `BASE_URL` for the public address mapped to this container. On Spaces,
the existing Docker metadata and port `7860` are already configured.

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
deployment; this is optional.

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
