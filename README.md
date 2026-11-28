<div align="center">

<img src="bot/server/templates/brand.svg" alt="GateStream gateway logo" width="112">
<h1>GateStream</h1>

**Gateway to your media.**

Stream and download Telegram files from your browser.<br>
The Cloudflare Worker, deployment workflow, and Python source.

<p>
  <a href="CHANGELOG.md"><img alt="Python release v2.0.40" src="https://img.shields.io/badge/Release-v2.0.40-D6AD60?style=for-the-badge"></a>
  <img alt="Python 3.11 or later" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white">
  <img alt="MongoDB storage" src="https://img.shields.io/badge/Storage-MongoDB-47A248?style=for-the-badge&logo=mongodb&logoColor=white">
  <br>
  <a href="CLOUDFLARE.md"><img alt="Cloudflare Workers" src="https://img.shields.io/badge/Proxy-Cloudflare_Workers-F38020?style=for-the-badge&logo=cloudflareworkers&logoColor=white"></a>
  <img alt="Telegram bot" src="https://img.shields.io/badge/Media-Telegram-26A5E4?style=for-the-badge&logo=telegram&logoColor=white">
</p>

[**Deploy the Worker**](CLOUDFLARE.md) · [**Serv00 Origin**](https://github.com/Chowdhury-Siam/GateStream-Lite) · [**Changelog**](CHANGELOG.md) · [**Report a Bug**](https://github.com/Chowdhury-Siam/GateStream/issues)

</div>

> [!TIP]
> In the Serv00 setup, **GateStream-Lite** runs the bot and Python server. Deploy the public Cloudflare Worker from **GateStream**.

---

## 🧭 Explore

- [Meet GateStream](#meet-gatestream)
- [Features](#features)
- [Get started](#get-started)
- [Telegram and File To Link](#telegram-access-and-commands)
- [Configuration](#configuration)
- [Cloudflare Worker](#cloudflare-worker)
- [HTTP endpoints](#http-endpoints)
- [Upgrade an existing deployment](#upgrading)
- [Development and checks](#development)
- [Troubleshooting](#troubleshooting)
- [Project map and credits](#developers)

---

<a id="meet-gatestream"></a>

## ✨ Meet GateStream

Send or forward a Telegram file to the bot to get a direct download link. Audio and video uploads also get a **Stream** button that opens the browser player. GateStream stores file records in MongoDB and retrieves media from Telegram as viewers open links.

| Package | Role in the Serv00 setup |
| --- | --- |
| [GateStream-Lite](https://github.com/Chowdhury-Siam/GateStream-Lite) | Runs the Python bot and HTTP origin on Serv00 |
| [GateStream](https://github.com/Chowdhury-Siam/GateStream) | Deploys the public Cloudflare Worker that proxies the origin and includes the full Python source |

---

<a id="features"></a>

## 💎 What You Can Do

### 📎 Turn files into links

Get reusable links from private uploads or from a configured **File To Link** group topic. Use **Revoke link** to invalidate a link you shared.

### 🎬 Play, seek, and resume

Watch in the browser or open supported links in VLC or MX Player. HTTP byte ranges support seeking and resuming partial downloads.

### 📊 Follow transfers live

Open the home page to see active media links, HTTP connections, bytes sent, download totals, and transfer progress. Owners can also inspect host resources and check which Telegram bot workers can post to the storage channel.

### 💾 Keep your file records

Back up or restore file records and manage saved titles and durations. The origin can cache opening and ending media chunks on disk and use extra bot tokens for parallel retrieval.

---

<a id="get-started"></a>

## 🚀 Get Started

1. Extract the GateStream GitHub ZIP and put the contents of its `GateStream/` folder, including `.github/`, at your repository root.
2. Start the Python origin with [GateStream-Lite](https://github.com/Chowdhury-Siam/GateStream-Lite) and make it reachable at a public HTTPS address. Check `/health` on that address.
3. Connect GitHub Actions to your Cloudflare account and deploy the Worker. See [Deploy the Cloudflare Worker](#cloudflare-worker).
4. Set `BASE_URL` on the origin to the public Worker URL, restart the origin, and test a generated file link.

The Python source in this package can also run directly. See [Development](#development), the [Serv00 guide](SERV00.md), or the [Hugging Face Spaces guide](HUGGING_FACE.md).

---

<a id="telegram-access-and-commands"></a>

## 💬 Telegram and File To Link

The bot starts with access for `OWNER_ID`. The owner can grant access with `/auth` and revoke it with `/unauth`. Use a positive Telegram ID for a user or a negative ID for a group. You can also reply to a user's message or run the command in the group. Private uploads by other users require an individual grant. For uploads in either chat, set `TELEGRAM_CHANNEL_ID` and give the main bot permission to post there.

| Command | What it does |
| --- | --- |
| `/start` | Opens the welcome menu |
| `/help` | Shows instructions for the current chat and includes owner commands when applicable |
| `/filetolink` | Lets the owner select the current group topic for uploads |
| `/link` | Retries the link for your own file when used as a reply in the configured topic |
| `/stats` | Opens the live dashboard in the owner's private chat, group, or forum topic |
| `/workers [CHANNEL_ID]` | Checks bot posting permissions from the owner's private chat |

### Set up the group topic

1. In Telegram, open your **File To Link** topic and send `/filetolink` as the owner. Run it again in the new topic if you move uploads.
2. Send `/auth` in the topic if the group still needs a grant.
3. Give the bot access to ordinary group messages. Make it a group admin or disable group privacy in BotFather and re-add it to the group.
4. Post a file to receive a link. To retry, reply to one of your own files in the topic with `/link`.

### Open live stats

Send `/stats` as the owner to see transfers, worker health, uptime, and host storage. The bot checks live metrics every five seconds and samples host resources and upload totals every 30 seconds. A new request deletes the previous dashboard before posting in the new chat or topic. The bot resumes the saved dashboard after an origin restart. In groups with bot privacy enabled, address the command to the bot as `/stats@YourBotUsername`.

### Check your bot workers

`/workers` asks each connected bot to post a silent test message to `TELEGRAM_CHANNEL_ID`. The private result reports failures, including permission errors, offline workers, and timeouts. Supply a channel ID to check a different channel already used for GateStream storage.

Other owner tools include `/log`, `/sync_duration`, `/set_title`, `/revoke`, `/clean`, `/backup`, `/restore`, `/purge`, and `/restart`.

---

<a id="configuration"></a>

## ⚙️ Configuration at a Glance

Start with [`.env.example`](.env.example). Get your Telegram API credentials from [my.telegram.org](https://my.telegram.org), a bot token from [BotFather](https://t.me/BotFather), and a MongoDB server your host can reach.

| Setting | Purpose |
| --- | --- |
| `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` | Required Telegram API credentials |
| `TELEGRAM_BOT_TOKEN` | Required primary BotFather token |
| `OWNER_ID` | Required positive numeric Telegram user ID |
| `DATABASE_URL` | MongoDB URI. The local default is `mongodb://localhost:27017`. |
| `DATABASE_NAME` | Main database name. The default is `OmniArchiver`. |
| `OMNI_MONGO_URI` | Optional separate catalog URI. The default catalog database is `OmniArchiverDB` on `DATABASE_URL`. |
| `TELEGRAM_CHANNEL_ID` | Storage channel required for uploads. Also used for notices and the default `/workers` check. |
| `MULTI_BOT_TOKENS` | Optional extra bot tokens, separated by whitespace, commas, or semicolons. Leave empty to disable extras. |
| `TELEGRAM_BOT_USERNAME` | Optional fallback if automatic username detection is unavailable |
| `BASE_URL` | Public URL used in file links. Spaces can derive it from `SPACE_HOST`. |
| `BIND_ADDRESS` and `PORT` | Origin listener. Defaults to `0.0.0.0:8080` locally and `0.0.0.0:7860` in the Docker image. Set the reserved loopback port on Serv00. |

Configuration precedence is system environment → `.env` → `config.env` → literal `start.sh` assignments → defaults. Explicit empty values in the environment take priority. See the [Serv00 guide](SERV00.md) for parsing and resource tuning.

Run one deployment per primary bot token. On Hugging Face Spaces, enter credentials under **Settings → Variables and secrets** and keep your existing README front matter with `sdk: docker` and `app_port: 7860`.

---

<a id="cloudflare-worker"></a>

## ☁️ Cloudflare Worker

The Worker forwards requests and streams responses from the origin. It preserves HTTP Range requests and partial responses used for seeking and resuming.

Under **Settings → Secrets and variables → Actions**, configure the repository.

| Type | Name | Value |
| --- | --- | --- |
| Secret | `CLOUDFLARE_API_TOKEN` | Cloudflare token with Workers deployment access |
| Secret | `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account ID |
| Variable or secret | `CLOUDFLARE_ORIGIN_URL` | Bare public HTTPS origin of the Serv00 reverse proxy, such as `https://your-origin.example` |
| Optional variable | `CLOUDFLARE_WORKER_NAME` | Your existing Worker name. The default is `ftl-stream-proxy`. |

Open **Actions → Deploy GateStream Worker → Run workflow**. The optional name entered in the run form takes precedence for that run. The workflow also deploys changes to the Worker or its workflow on `main`. Set the origin's `BASE_URL` to your public Worker URL or custom domain.

See the [Cloudflare setup guide](CLOUDFLARE.md) for deployment details and range checks. Use your own Serv00 HTTPS origin in place of the Spaces example in that guide.

---

<a id="http-endpoints"></a>

## 🔗 HTTP Endpoints

| URL | What it serves |
| --- | --- |
| `/` | Landing page and live transfer dashboard |
| `/health` and `/ping` | `OK` and the `X-GateStream-Version` header |
| `/stream/<code>` | Browser player with playback resume |
| `/dl/<code>` | Direct download. Add `?stream=1` for inline media. |
| `/api/file/<code>` | File details and playback/download URLs |
| `/api/batch/<code>` | Details and episode links for existing batch records |
| `/api/activity` | Aggregate transfer counters and anonymous progress rows |

Anyone holding a valid media URL can open it. Telegram access grants control who can use the bot.

<details>
<summary>How the dashboard counts downloads</summary>

GateStream combines overlapping byte ranges for the same anonymous client and file. A download counts when the server has handed off the full file to HTTP responses. HEAD requests and inline playback are excluded. A restart clears in-flight range coverage.

Active rows show transferred bytes, progress, speed, elapsed time, and ETA. The dashboard refreshes every two seconds while visible and displays up to 20 active rows, while aggregate counters include all active transfers. The public activity response omits file names, URLs, file codes, credentials, and user IDs.

Cumulative totals use the MongoDB `analytics.transfer_activity` document, checkpoint about every five seconds, and flush on graceful shutdown. Older counters can be seeded from `analytics.bandwidth`. Use the same MongoDB URI and database name to keep totals. Counters expect one writer per deployment, and a full database backup is required to back up analytics.

</details>

---

<a id="upgrading"></a>

## 📦 Upgrade an Existing Deployment

1. Back up file records with `/backup`. Use a full MongoDB backup for analytics and other database state.
2. Stop the bot gracefully and extract the correct package into a clean project directory. Bring over your existing configuration, Telegram session files, and any media cache you want to reuse. Install the packaged requirements before starting.
3. Keep your MongoDB URI and names, bot tokens, owner ID, Worker name, and public `BASE_URL` for existing links and settings. This release uses the existing database schema, file codes, session names, and browser resume keys.
4. Check `/health`, try an existing file link, generate a new link, then verify playback and seeking.

For deployment-specific steps, see [Serv00 setup](SERV00.md) and the [changelog](CHANGELOG.md).

---

<a id="development"></a>

## 🛠️ Development and Checks

GateStream uses **Python 3.11+**, Hydrogram, Quart, and MongoDB. For a local test instance or VPS:

```bash
git clone https://github.com/Chowdhury-Siam/GateStream.git
cd GateStream
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

On Windows, run `.venv\Scripts\activate.bat` in Command Prompt and copy `.env.example` to `.env`. Fill in the settings, then run `python -m bot`.

<details>
<summary>Run with Docker</summary>

```bash
docker build -t gatestream .
docker run -d --name gatestream -p 8080:7860 --env-file .env -e PORT=7860 -e BIND_ADDRESS=0.0.0.0 --restart unless-stopped gatestream
```

Set `BASE_URL` to the public address that reaches the container.

</details>

```bash
python -m unittest discover -s tests -v
node --test cloudflare/worker.test.mjs
```

Python tests use mocked Telegram and MongoDB services. See [release validation](VALIDATION.md).

---

<a id="troubleshooting"></a>

## 🩺 Troubleshooting

| Problem | First check |
| --- | --- |
| The bot ignores a group upload | Check the group grant, the configured topic, and the bot's access to ordinary messages. If the upload fails after the bot replies, check its storage-channel access. |
| A worker cannot post | Run `/workers` in the owner's private chat and check the worker's channel permissions and token. |
| The Serv00 origin fails to start | Check the reserved port, Binexec, the effective environment, and the app log. |
| The Worker returns `502` or `503` | Check the direct HTTPS origin's `/health` and the repository's `CLOUDFLARE_ORIGIN_URL`. |
| Playback will not seek or downloads will not resume | Request a small byte range from `/dl/<code>` and check for `206 Partial Content` and `Content-Range`. |
| Old links stop working after an upgrade | Check the public hostname, existing MongoDB URI and database names, and the bots' storage-channel access. |

Check the [Serv00 troubleshooting steps](SERV00.md#8-limits-and-troubleshooting) for startup, proxy, and resource errors.

---

<a id="developers"></a>

## 👩‍💻 Project Map and Credits

| Path | Responsibility |
| --- | --- |
| `bot/plugins/` | Telegram commands, callbacks, and upload handling |
| `bot/server/` | Landing page, browser player, file routes, and HTTP range delivery |
| `bot/config.py` and `bot/database/` | Centralized configuration and MongoDB records |
| `scripts/` | Watchdog and bot-token management |
| `tests/` | Python regression tests |
| `cloudflare/` | Proxy implementation and Worker tests |
| `.github/workflows/` | Cloudflare Worker deployment |

GateStream is Siam's rebrand of OmniArchiver-F2L. See the [upstream acknowledgements](ACKNOWLEDGEMENTS.md) and the [branding guide](BRANDING.md).

<p align="center"><sub>Gateway to your media.</sub></p>
