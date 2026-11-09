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

## What you can do

- **Save Telegram files.** Upload a file directly or collect multiple files into a ZIP. The owner can also link a channel post with `/link`.
- **Browse your uploads.** Sign in through Telegram at `/index` to see your ZIP folders and single files.
- **Follow transfers live.** The home page shows active links, HTTP connections, byte counts, progress, speed, elapsed time and ETA.
- **Watch your way.** Play in the browser, resume playback, or open VLC or MX Player.
- **Share a link.** Create stream, download, and API URLs; revoke access by removing a link.
- **Manage the library.** Inspect server stats, backfill durations, and back up or restore records.
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
| `TELEGRAM_CHANNEL_ID` | No | Storage channel for direct uploads and first-start user notices |
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

## Telegram access and commands

The bot starts with access for `OWNER_ID` only. The owner manages access with
`/auth` and `/unauth`. Grants are stored in the existing MongoDB database.

| Action | Command |
| --- | --- |
| Authorize a user | `/auth USER_ID` or reply to their message with `/auth` |
| Authorize a group | `/auth GROUP_ID` or send `/auth` in that group |
| Revoke a grant | Use `/unauth` with the same ID, reply, or group |
| Open the bot menu | `/start` or `/help` |
| Open your personal library | `/catalog` in a private chat |
| Start or finish a ZIP | `/index` or the **Index** button |
| Finish a ZIP with a custom name | `/index My folder` |
| Link a channel file | `/link [category] MESSAGE_LINK` (owner only) |

Use a positive Telegram ID for a user and a negative ID for a group.
A group grant applies in that group. Group members can open the bot menu
and start and finish their own indexes. Open `/index`, send the files in
the same group, and finish with `/index My folder`. For direct group posts,
Telegram must deliver them to the bot. This works if the bot is a group admin
or group privacy is disabled in BotFather and the bot is re-added to the group.
With privacy mode enabled, send files as replies to the bot's index message.
Outside an index, reply to a bot message to get an individual file link.

Private chat and browser library sign-in require a direct user grant. The
owner can always use the bot.

Owner tools include `/stats`, `/workers`, `/workersetup`, `/workerrepair`, `/log`, `/sync_duration`,
`/set_title`, `/revoke`, `/clean`, `/backup`, `/restore`, `/purge`,
and `/restart`.

## Worker membership and repair

`/stats` shows connected bots, host resources, MongoDB library totals and live
transfers. `/workers` lists channel IDs and reports confirmed present, missing,
blocked, and unverified workers. Use `/workers CHANNEL_ID` for a specific channel.

In the owner's private bot chat, send `/workersetup` or select **Set up worker
repair** from the `/start` menu. Open the one-time HTTPS link and enter your
Telegram API ID, API hash, phone number, login code, and two-step password if
Telegram requests it. The Telegram account must match `OWNER_ID`.

GateStream encrypts the session and its API credentials using a key derived
from the bot token and stores them in MongoDB for immediate use and restart
recovery. Setup does not display the session string. If the bot token changes,
run `/workersetup` again. Existing `WORKER_REPAIR_*` settings still work when
there is no session saved through the bot.

In the owner's private chat with the bot, run `/workerrepair CHANNEL_ID`.
The session account must own the channel or have permission to add admins.
GateStream adds only confirmed missing auxiliary bots, with minimal admin
rights and at least 60 seconds between additions. It leaves existing members
and blocked accounts alone. On a Telegram rate limit, the run stops and saves
the retry time in MongoDB. Check the result with `/workers CHANNEL_ID`.

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
| `/index` | Personal upload library, with Index folders and Single files |

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

## Personal library and ZIP downloads

Sign in at `/index` with a one-time link from the bot's `/catalog` command.
Uploads outside an index session appear under **Single files**. Start and finish
a folder with `/index`. The final command shows the ZIP name, a preview of its
files, a copyable URL, and a **Download** button.
Open the folder in the library to rename it or download the ZIP from the web.

Cached folder ZIPs can be downloaded while they are being built and support
HTTP ranges, resuming, and multiple connections. If the archive cannot fit
in the available cache, GateStream streams it directly in one connection.
Direct streams restart from the beginning on retry. ZIP links expire after
24 hours. Anyone with the share link can download the folder.

Existing channel download and stream links work by file code.

## Automatic media categories

AniList identifies anime without a key. Optional host Secrets `MAL_CLIENT_ID`
and `OMDB_API_KEY` enable MyAnimeList fallback and OMDb movie/series matching,
including IMDb IDs and available Rotten Tomatoes ratings. Unknown/conflicting
results remain Direct files.

## Live activity dashboard

The compact home page shows separate **Single files downloaded** and **ZIPs
downloaded** totals, with live bandwidth cards in an expandable panel.
New totals begin with this update: previous aggregate HTTP request counters
cannot reconstruct how many entire files were downloaded. Completion means
all resource bytes were handed to completed response streams. It is not proof
that the client saved the file to disk. HEAD, inline playback, ZIP building and
range probes do not increment these new totals; overlapping HTTP ranges for
the same anonymous client/resource are combined. A ZIP counts once, not once
per selected file or range connection. 

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
Media links are counted separately from HTTP range connections. It shows data
sent to viewers, completed requests, failures, incomplete transfers and server uptime.

Active rows show progress, transferred/total bytes, average speed, elapsed time
and ETA. Unknown-size downloads use an indeterminate progress bar. Summary
throughput covers roughly five seconds. Only the latest 20 active rows are shown;
all active transfers are included in the counters. On a network failure the
previous values stay dimmed and the panel shows disconnected until it reconnects.

The public endpoint exposes aggregate counts and anonymous activity numbers,
without file names, URLs, file codes, credentials or user IDs.
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
from the older `analytics.bandwidth` record on first upgrade. Historical
counts and other totals that were never saved cannot be reconstructed. The
media-only `/backup` export does not include these analytics records; a full
database backup does.

Media byte totals measure bytes handed to HTTP responses, not proof a client
saved a whole file. Failed/incomplete transfers retain their partial byte totals.
Legacy checkpoint categories are retained for compatibility; their historical
values are not erased and are not shown as active features on the home page.

## Upgrading an existing deployment

1. Back up the database with `/backup` and retain your current hosting settings.
2. Use a clean source directory from the appropriate ZIP. When upgrading an
   existing checkout, remove retired files `bot/plugins/drive.py`,
   `bot/modules/drive.py`, `tests/test_drive.py`, and `GOOGLE_DRIVE.md`. If v2.0.8
   was deployed, also remove `bot/plugins/settings.py`,
   `bot/modules/runtime_settings.py`, `tests/test_bot_settings.py`, and
   `BOT_SETTINGS.md`. Do not remove your database, Telegram session files or
   cached archives that are still needed for resume.
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

## v2.0.9 — v2.0.7 base with Drive integration removed

This release starts from the v2.0.7 source ZIPs. It removes every Google Drive
transfer command (`/drive`, `/gdrive`, `/mirror`, `/clone`, `/drivestatus`,
`/canceldrive`), credential loader, Drive configuration variable, Google API
dependency and Bhadoo index button. The v2.0.8 owner settings module is not part
of this v2.0.7-based package. The bot imports an explicit list of active plugins,
so retired plugins left by an overlay upload cannot register old commands.

Custom ZIP names, compact Library/Private links pages, separate persistent
single-file/ZIP delivery totals, resumable and parallel ZIP sources, indexing,
classification and existing database settings are retained. The home dashboard
now shows file-download activity only. Existing credentials or historical data
in external secrets/database storage are not deleted or used by this release;
remove unused host credential secrets separately if desired.

Removing these features does not establish why an account was locked and does
not guarantee hosting approval or account reinstatement. Telegram streaming
and public downloads still require a host that permits the workload.


## v2.0.10 — New-user channel notices

Set the existing `TELEGRAM_CHANNEL_ID` to the destination channel and give the
bot permission to post there. An allowed user's first private `/start` sends
their Telegram-provided first/last name, user ID, username/profile link, language,
Premium/verified/bot/restricted/scam/fake/deleted flags, available DC ID and
first-start time (UTC). The bot
does not request or collect phone numbers, IP addresses, locations or contacts.
Use a private channel if these notices should only be visible to administrators.

The existing MongoDB database gains a `bot_users` collection containing only the
user ID, first-start time and notice delivery state. Repeated `/start`, `/help`
and normal restarts do not resend a successfully recorded notice. Existing users
are considered new on their first `/start` after this upgrade because older
versions did not record starts. No notice is sent when the channel is unset.

Channel or database errors do not block the welcome; failed notices retry on
the user's next `/start`. A two-minute claim prevents concurrent duplicate
notices and expires after a crash. A crash/timeout between Telegram accepting
a notice and MongoDB recording success can still cause a repeat on retry.
The bot must have posting permission; delivery has been tested with mocks,
not a live channel. All Drive functionality remains removed. Existing database
names, hosting settings and resumable streaming behavior are unchanged.


## v2.0.11 — Visible ZIP preparation progress

ZIP creation now has separate checking/queued/preparing/ready states. While the
server creates a link, an indeterminate bar shows that no byte progress has
arrived yet. Once preparation starts, the page displays a progress bar, percent,
prepared bytes / total ZIP size, and source preparation speed when available.
Progress refreshes every two seconds in Prepare first mode. Start immediately
shows the same byte progress alongside its existing buffer status. Preparation
progress measures the ZIP being built on the server, not bytes downloaded by
the browser. A source stall can leave the percentage unchanged.

Selection queries filter matching documents before expanding batch episodes
and no longer sort the whole catalog; section access checks remain in place.
Network status timeouts retry, and failed link creation shows a clear error.
The short link-creation request has a 25-second server deadline so a stalled
request cannot silently wait for several minutes behind the Worker. ZIP building
continues separately and is not limited to 25 seconds.
The no-JavaScript preparation page also displays prepared/total sizes.

Checks for this release also included
the existing Python suite and the GitHub package's Worker tests. Live host
performance has not been measured. User notices and the complete Drive removal
remain intact; database settings and Hugging Face README metadata are unchanged.


## v2.0.12 — Count downloads closed at Content-Length

The single-file and ZIP totals now record actual byte coverage as chunks are
handed to HTTP responses, including partial open-ended range connections closed
by download managers after their assigned segment. Previously a complete file
could remain uncounted when a connection closed at Content-Length before the
source iterator reached EOF/cleanup. Receiving the entire advertised response
payload now counts as a completed request even if that connection closes then.
Only full resource byte coverage increments the file/ZIP total; overlapping
retries do not multiply totals, gaps stay incomplete, and playback/HEAD/internal
ZIP preparation remain excluded.

Deploy both the origin and the updated Cloudflare Worker. The Worker overwrites
an anonymous visitor/agent hash header to keep all range connections grouped
when origin-side proxy addresses vary. Older Workers can fall back to forwarded
Cloudflare visitor headers; direct hosting falls back to the remote address.
These headers affect analytics only and never authorize access. Raw visitor
addresses and user agents are not stored by this counter or exposed by its API.

Successful download totals still persist in the existing MongoDB analytics
checkpoint. In-progress range coverage is memory-only, bounded to 1024 pairs
and 24 hours, and resets on restart. Changing visitor address or User-Agent
during a download can split its coverage. The totals describe bytes handed
out by the server, not proof of a saved client file. Past completed downloads
missed by the old counter cannot be reconstructed accurately and are not added
retroactively. Existing totals, database names/settings and HF metadata remain
unchanged. Drive is still completely removed.


## v2.0.13 — Refresh expired Telegram media references

Media streams now handle both raised file-reference errors and Hydrogram's
logged-error/early-EOF behavior. A short read refreshes its channel message
through the same downloading bot once, then resumes at the next unread chunk.
Single-chunk video/prefetch reads use the same recovery. Refresh attempts are
bounded; stalls and persistent failures still fall back to the existing worker
rotation logic and retry limits. Continuous-download outer retries also request
a fresh message rather than reusing an expired one-hour cached reference.

Worker media lookups do not silently borrow the primary bot's file reference.
If fallback is needed, the download switches to the primary bot explicitly.
Refreshing one worker's message does not clear other workers' valid cached
references. Indexing and ordinary message lookups retain their existing primary
bot fallback. Cooldown logs no longer describe every media-read failure as
Telegram rate limiting. The upstream SDK may still log the first expired
reference before recovery; persistent errors can indicate lost channel access
or a changed/deleted source message.

Deploy the updated origin; the Worker has no changes in this release. Existing
ZIP preparation, completion counters, user notices and complete Drive removal
remain intact. Database names/settings and HF metadata are preserved. Tests
simulate expired references and interrupted streams; live Telegram recovery
still needs verification on the deployment.

## v2.0.14 — Preserve APK downloads

APK links serve the Android package media type and keep their `.apk` filename.
The download route uses the filename to resolve generic media types, and new
APK uploads retain their Telegram filename even when the message has a caption.
Deploy the updated Python origin to apply the fix to existing APK links.

## v2.0.15 — Personal upload library

1. Send `/index` in a private chat with the bot to start a folder.
2. Send up to 500 files, then send `/index My folder name`. The bot replies
   with a shareable ZIP link. A second `/index` without a name uses the name
   set at the start or the default name.
3. Send `/catalog` for a one-time browser sign-in link. The **Index** tab
   shows your finished folders. Open a folder to download individual files,
   rename the folder and its ZIP, or use the ZIP link.
4. Upload outside an index session to find the file under **Single files**.

Personal uploads use the configured `TELEGRAM_CHANNEL_ID` storage channel.
Earlier direct uploads with a recorded owner in that channel appear under
**Single files**. Anyone with a direct download link or a shared folder ZIP
link can use it. The library listing and folder renaming require sign-in.

ZIP downloads retain preparation progress and HTTP range support. Large
folders need enough disk space and may require a larger `ZIP_CACHE_MAX_MB`.
Deploy the updated Python origin and the updated Cloudflare Worker if you
access GateStream through its Worker.

## v2.0.16 — Personal library only

The web channel catalog, its shared password commands, and its bulk movie
listing API have been retired. Old catalog ZIP tickets expire on upgrade.
The personal library, its folder ZIPs, Telegram channel scanning and search,
and existing individual file links continue to work. Deploy the updated
origin and Worker together. No database migration is needed.

## v2.0.17 — Choose the ZIP mode in Telegram

After the final `/index`, choose **Prepare first** to see animated progress
in Telegram and receive the link when the ZIP is complete. Choose
**Start immediately** to get a direct download link while the ZIP builds.
The Python origin runs both modes through the same resumable archive cache
as the web library.

## v2.0.18 — Start ZIP downloads immediately

After the final `/index`, tap **Start immediately** for a direct ZIP link.
The web library's **Download ZIP** button starts the same resumable download.
Previously issued links can still resume while their archive remains cached.

## v2.0.19 — Copyable ZIP links and large folders

The final `/index` replies with the ZIP URL in a copyable code line. If the
cache cannot reserve enough space, the link streams the ZIP when opened.
Cached ZIPs retain resume and multiple connections.

## v2.0.20 — Owner-managed access and bot menu

Existing deployments start with owner-only Telegram access. The previous
environment-based user lists no longer grant access. Use `/auth` in Telegram
to grant access to each user or group. Existing file links and stored data
remain usable. The bot now shows an Index, Library, and My ID menu, with
an access-management button for the owner.
