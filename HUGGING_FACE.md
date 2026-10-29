# Deploy GateStream on Hugging Face Spaces

## 1. Prepare a reachable MongoDB database

The bot needs external MongoDB storage. Start with the normal connection string
provided by your MongoDB host, replacing its password placeholder. Store the full
URI as the `DATABASE_URL` Space secret. URL-encode special characters in the
password and keep TLS certificate verification enabled. For Atlas, the URI is
typically in this form:

```text
mongodb+srv://USER:URL_ENCODED_PASSWORD@YOUR_CLUSTER.mongodb.net/
```

The application performs an actual database ping before starting Telegram.
If startup reaches **Starting Main Telegram Bot** and **MongoDB database indexes
ensured**, the database is reachable in that deployment; keep that working URI.

If the connection times out, check the database's IP access list, credentials,
DNS, TLS, and the Space's outbound networking. Hugging Face documents outbound
ports **80, 443, and 8080**, while standard Atlas uses **27017**. Where this
restriction is enforced, a MongoDB-compatible TCP gateway on an allowed port or
a different host is required. Simply changing an Atlas URI's port to 443 does
not create such a gateway, and an HTTPS REST endpoint is not a MongoDB URI.
The Space does not include a database or external gateway provisioning.

## 2. Create the Space and upload the files

1. Create a Space at <https://huggingface.co/new-space>.
2. Select **Docker**, then **Blank**, and CPU hardware. Check the current account
   requirements shown by Hugging Face for creating Docker Spaces.
3. Choose **Public** if stream/download URLs should work for anyone with the link.
   A private Space requires Hugging Face access and can prevent playback in
   external players.
4. Extract `GateStream-HuggingFace.zip` on your computer.
5. Upload the extracted contents to the Space's **Files** tab and commit them.
   `README.md`, `Dockerfile`, `requirements.txt`, and the `bot/` folder must be at
   the repository root. Do not upload just the ZIP or place everything inside an
   extra parent folder. Include `.dockerignore`; `.env.example` is only a reference.

The README already selects `sdk: docker` and `app_port: 7860`.

## 3. Add secrets

In the Space's **Settings > Variables and secrets**, add these as **Secrets**:

| Name | Value |
| --- | --- |
| `TELEGRAM_API_ID` | Numeric API ID from my.telegram.org |
| `TELEGRAM_API_HASH` | Your Telegram API hash |
| `TELEGRAM_BOT_TOKEN` | Main bot token from BotFather |
| `OWNER_ID` | Your positive numeric Telegram user ID |
| `DATABASE_URL` | Your MongoDB connection URI from step 1 |

Optional secrets: `MULTI_BOT_TOKENS` (space-separated worker tokens), `AUTH_USERS`
(space-separated admin IDs), `ALLOWED_USER_IDS`, `TELEGRAM_CHANNEL_ID` (for direct
uploads), and `OMNI_MONGO_URI` (a separate upstream catalog database). If using
`OMNI_MONGO_URI`, it must also be reachable on an allowed port. Without it, the
catalog uses the `OmniArchiverDB` database on your own MongoDB server.

Optional **Variables**: `DATABASE_NAME` (default `OmniArchiver`),
`TELEGRAM_BOT_USERNAME` (detected automatically), and `BASE_URL` (only when overriding
the automatically detected public URL, e.g. for a custom domain).

Leave `PORT` and `BIND_ADDRESS` unset: the image already uses `7860` and `0.0.0.0`.
Do not upload credentials, a filled `.env`, Telegram session files, or log files.

## 4. Verify deployment

1. Restart the Space after setting secrets and wait for **Running**.
2. The app page should show **GateStream**, an online status and a Telegram link.
3. Open `https://YOUR-SPACE-HOST.hf.space/health`; it should return `OK`.
   Use the actual app host shown by Hugging Face, not the `huggingface.co/spaces/...`
   repository page URL. The app reads this host from `SPACE_HOST` automatically.
4. Add the main bot and any worker bots to each source Telegram channel with access
   to its media. Send `/start` and `/link <channel-message-url>` to the bot.
5. Open the generated `/stream/...` and `/dl/...` links. If using existing database
   records from another deployment, generate fresh links to use this Space's host.

The application checks the database with a ping before starting Telegram and the
web server. A MongoDB timeout in the logs usually means the endpoint's port, DNS,
TLS, or database network access rules are wrong. Missing required credentials
produce an error listing variable names without printing their values.

## Availability and storage

Space-local sessions, logs, and media cache are temporary and may disappear after
a restart/rebuild. File records stay in your external MongoDB database, and the
media stays in Telegram. The bot reconnects using the configured token after a
fresh start. Keep only one running deployment per main bot token.

Free CPU hardware can sleep when unused. Telegram messages do not constitute
visits to the Space's web app, so do not assume an always-running Telegram bot.
Use appropriate always-on hardware if continuous availability is required.
Anyone holding a generated file URL can access it when the Space is public;
`ALLOWED_USER_IDS` restricts Telegram commands, not HTTP file access.

## Local checks

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

The tests cover settings, missing secrets, the landing page, health checks, and
Space-host URLs without contacting Telegram or MongoDB.

Official references:
- <https://huggingface.co/docs/hub/spaces-sdks-docker>
- <https://huggingface.co/docs/hub/spaces-overview#networking>
- <https://huggingface.co/docs/hub/spaces-overview#lifecycle-management>

## GateStream upgrades

Keep your existing database settings and bot token when uploading this package.
Set `BASE_URL` to your existing Cloudflare Worker URL if using the proxy.
Cloudflare deployment automation is supplied separately in `GateStream-GitHub.zip`.
The Space must be permitted to run: source updates do not clear a paused
`Flagged as abusive` status; request a review from Hugging Face in that case.

## Optional Google Drive transfers

The `/drive` command is included but disabled. HTTPS networking and temporary
disk are technically available on Spaces, but this does not establish that a
general file-transfer bot is permitted. Confirm permission with Hugging Face
before enabling the feature. A paused/flagged Space still requires their review.
See [GOOGLE_DRIVE.md](GOOGLE_DRIVE.md) for details. Never upload `token.pickle`
to the Space repository; if approved, use the base64 host Secret described there.

## Channel indexing and media classification

Use `/index LAST_POST_LINK` to scan a channel automatically, with progress and
resume. AniList needs no key. Optional Space Secrets `MAL_CLIENT_ID` and
`OMDB_API_KEY` enable additional metadata providers; restart after setting them.
See [CHANNEL_INDEX.md](CHANNEL_INDEX.md).

## Immediate and parallel ZIP downloads (2.0.6)

**Start immediately** now supports multiple download-manager connections and
resume. One ZIP fills on disk in the background, while clients start receiving
its available bytes. Every connection reads that same archive, with stable
ETags and HTTP byte ranges. Telegram is fetched once per selected file, not once
per client connection. Later ranges wait until their bytes have been built;
additional connections cannot bypass Telegram throughput. Waiting connections
may need a retry if the client or proxy times them out.

Version 2.0.6 overlaps four continuous 4 MiB Telegram ranges per ZIP source,
with ordered bounded buffers and retry from the last complete chunk of a
stalled range. It avoids reopening a Telegram stream for every MiB and skips
unnecessary detached head/tail prefetch for ZIPs. Source buffering stays around
16–20 MiB per builder. The page now shows ZIP buffer percentage and fill rate,
which helps distinguish slow source delivery from client/proxy download limits.
Constant throughput still depends on Telegram and the host/network. Real-host
throughput must be checked after deployment; local latency simulations do not
predict an exact production speed.

**Prepare with resume** remains available: it waits for the ZIP to finish before
starting the client download, so all ranges are already ready. Both modes can
use four connections as a starting point. Host/proxy/network limits still affect
speed. Create a fresh selection after updating; old immediate-mode links retain
their previous single-connection behavior.

ZIP links last 24 hours. Keep the default `ZIP_CACHE_MAX_MB=8192` (8 GiB shared
disk budget) or set a size that fits your Space's available disk. Large selections
must fit both that budget and free disk. `ZIP_CACHE_DIR` can point to an existing
persistent volume if your hosting provides one. The default directory is
`cache/archives`; do not upload its contents. One prepared-mode build and up to two live builds can run at a time.
Expired ZIPs and interrupted partial files are cleaned when new selections are
made. Unexpired ZIPs stay available for resume instead of being evicted.

Normal process restarts retain resume if the completed file survives. Rebuilds
that clear temporary storage require a new selection; missing archives return
410 and are never silently regenerated for an existing partial download. Changing
the Link password still invalidates protected ZIP links. Upload the updated
source, restart the Space and select files again. `/health` should report
`X-GateStream-Version: 2.0.6`.

## Dashboard totals after a restart

Cumulative transfer totals are checkpointed in your external MongoDB database
every five seconds and restored after the Space restarts or rebuilds. Keep the
same `DATABASE_URL` and `DATABASE_NAME`. Current connections, speeds and uptime
reset because they describe the new running process. No local persistent disk
is required for these statistics. A hard kill can lose the most recent unsaved
interval; database failures are reported on the dashboard. Earlier unsaved
counts cannot be recovered, except existing media bytes in the legacy bandwidth
record where available.
