# GateStream 3.0.0

GateStream connects Telegram files to resumable HTTP downloads and browser
playback. The bot, origin server, personal library, worker management, and
statistics now run in Go.

## Start

The source packages include binaries for Linux AMD64 and FreeBSD AMD64.

1. Extract the package into a clean directory.
2. Copy `.env.example` to `.env` and enter your existing configuration.
3. Start the application.

```sh
chmod +x start.sh scripts/watchdog.sh bin/gatestream-*
./start.sh
```

Use `SERV00.md` for FreeBSD shared hosting and `MIGRATION.md` for an existing
Python installation. Configuration loads automatically at startup. The command
`./start.sh --version` prints the application version.

## Features

- Automatic single-file links in private chat and the configured File To Link
  group topic. Replies include the filename, a copyable URL and a Download button.
- Correct filename and media type, including APK documents.
- HTTP GET/HEAD, exact single byte ranges, resumable downloads, ETag/If-Range and
  multiple client connections. Media bytes use MTProto through gotd/td.
- Bounded concurrent Telegram reads, bounded active HTTP downloads, ordered
  look-ahead, worker cooldowns and bounded reference refresh on the downloading
  account. Playback and HEAD are excluded from completed-download counts.
- A private personal library with one-time Telegram sign-in, search, pagination,
  session expiry and owner-managed authorization.
- Owner channel-file links, AniList classification, and optional MyAnimeList
  and OMDb lookups.
- Worker log-posting checks, private owner-account setup and paced worker repair.
  Owner credentials and sessions use authenticated encryption before MongoDB storage.
- Live transfer statistics, range completion deduplication, five-second MongoDB
  checkpoints, and owner-only Stop controls after library sign-in.
- Database backup/restore, duration refresh, rename, revoke, cache cleanup and
  graceful restart/shutdown.

The public download URLs remain `/dl/CODE`, player URLs remain `/stream/CODE`,
and the file API remains `/api/file/CODE`. Read access to historical episode
records and their existing metadata API remains available.

## Configuration

Precedence is system environment, `.env`, `config.env`, literal assignments in a
legacy `start.sh`, then defaults. An exported empty value overrides file values.
Comments and blank lines are ignored. Both quote styles, literal dollar signs,
inline hashes, equals signs, CRLF and UTF-8 BOM are supported. Duplicate active
keys use the last assignment in their file. Configuration files are parsed as
data. Restart after changing settings.

Keep your current `DATABASE_NAME`, `DATABASE_URL`, `OMNI_MONGO_URI`, bot tokens,
`OWNER_ID`, `TELEGRAM_CHANNEL_ID`, `BASE_URL` and reserved port when migrating.
The defaults retain `OmniArchiver` and the existing upstream configuration key.

`GO_MAX_PROCS` caps Go scheduler CPUs, with legacy `IO_WORKERS` as an alias.
An exported `GOMAXPROCS` takes precedence.

`MAX_TRANSMISSIONS` bounds reads globally. `MAX_HTTP_CONNECTIONS` bounds media
responses and their buffered look-ahead. `MEDIA_CACHE_MAX_MB` includes recognized
legacy media chunks in its disk budget. Cache writes reserve `CACHE_FREE_MB`
where filesystem information is available and tolerate quota errors.

`MEMORY_CLEAN_MB` sets Go's soft runtime memory limit. It guides garbage
collection. `MEMORY_RESTART_MB` triggers a graceful restart if Go runtime memory
exceeds the configured threshold. Set the latter to zero to disable restart.
Go runtime memory statistics describe the application runtime. Hosting account
RAM accounting can include other allocations and processes.

## Telegram access

Only `OWNER_ID` has access initially. Use `/auth USER_ID` or `/auth GROUP_ID`
to grant access. In a group, `/auth` grants that group. `/unauth` removes access.

Open the File To Link forum topic and send `/filetolink` as the owner. Files
posted there receive links automatically. `/link` retries work in that topic.
The bot needs permission to see ordinary group files. Grant appropriate group
administrator permissions or configure privacy through BotFather.

Use `/catalog` in private chat for the personal library. Group authorization
allows file tools in that group. A direct user grant is required for that
member's personal-library sign-in.

Send `/workersetup` in the owner's private chat to connect the owner account.
The HTTPS page accepts API ID, API hash, phone, login code and two-step password.
It saves the encrypted session automatically. `/workerrepair` adds confirmed
missing workers with at least 60 seconds between attempts. Telegram rate limits
pause repair and preserve the next permitted attempt time.

`COMMANDS.txt` can be copied into BotFather. The application also sets separate
command menus for ordinary private users and the owner.

## Cloudflare Worker

The GitHub package includes `cloudflare/` and the existing deployment workflow.
Set the Worker `ORIGIN_URL` to your public HTTPS origin and set the bot's
`BASE_URL` to the Worker URL. Cookies, byte ranges and anonymous completion
keys pass through the proxy. The origin enforces session and CSRF checks on
private forms and transfer Stop actions.

See `CLOUDFLARE.md` for repository variables and secrets. Keep those existing
values when changing the origin runtime.

## Build from source

Go 1.25 or later is required to build. Build on a machine with sufficient RAM
for the generated Telegram SDK. Use the supplied binary on small shared hosts.

```sh
go mod download
CGO_ENABLED=0 GOGC=20 go build -p 1 -buildvcs=false -trimpath \
  -ldflags='-s -w' -o bin/gatestream-linux-amd64 .
```

Cross-compile for Serv00 from a development machine.

```sh
GOOS=freebsd GOARCH=amd64 CGO_ENABLED=0 GOGC=20 \
  go build -p 1 -buildvcs=false -trimpath -ldflags='-s -w' \
  -o bin/gatestream-freebsd-amd64 .
```

The runtime uses the Go HTTP server, gotd/td, the official MongoDB Go driver and
a pure Go SQLite reader for importing existing Hydrogram peer tables. Database
names, collection names, saved file codes, existing access grants, library
sessions and transfer totals retain their existing representations.

## Docker

```sh
docker build -t gatestream .
docker run --rm --env-file .env -p YOUR_PORT:8080 \
  -v gatestream-sessions:/app/sessions-go \
  -v gatestream-cache:/app/cache gatestream
```

Match the container's `PORT` to the port mapping if `.env` overrides it. The
image runs as an unprivileged user. Mount the previous `.session` files readably
inside `/app` when importing private-channel peers from a Python installation.
Hugging Face Spaces can use the Dockerfile with its required bind address and
port set through the platform environment. AlwaysData and Linux VPS hosts can
run `start.sh` or a binary directly.

## Test

```sh
go test -race ./...
go vet ./...
node --test cloudflare/worker.test.mjs
```

See `VALIDATION.md` for what was verified and what requires a live deployment.
Upstream attribution is in `ACKNOWLEDGEMENTS.md`. Dependency notices accompany
the supplied binaries under `third_party_licenses/`.
