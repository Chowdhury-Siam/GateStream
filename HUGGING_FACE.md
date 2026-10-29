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
See [CHANNEL_INDEX.md](CHANNEL_INDEX.md). Bulk downloads now hand off one-hour
GET ZIP links to download managers; use a single connection for each ZIP.

Version 2.0.2 uses a continuous Telegram source stream for each ZIP entry,
matching the individual-download path. Stalled source reads time out after
18 seconds and retry at the next undelivered chunk, without repeating earlier
bytes. Keep one connection per ZIP in the download manager; ZIP ranges and
resume are unavailable. Actual speed depends on Telegram, host resources and
network conditions. Upload the updated source, restart the Space and generate
a fresh ZIP link. The `/health` response header `X-GateStream-Version` should
report `2.0.2`. A complete real-host transfer is still needed to verify whether
this resolves the observed host/network stall.

## Dashboard totals after a restart

Cumulative transfer totals are checkpointed in your external MongoDB database
every five seconds and restored after the Space restarts or rebuilds. Keep the
same `DATABASE_URL` and `DATABASE_NAME`. Current connections, speeds and uptime
reset because they describe the new running process. No local persistent disk
is required for these statistics. A hard kill can lose the most recent unsaved
interval; database failures are reported on the dashboard. Earlier unsaved
counts cannot be recovered, except existing media bytes in the legacy bandwidth
record where available.
