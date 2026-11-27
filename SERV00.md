# GateStream 3.0.0 on Serv00 / FreeBSD

The Lite package includes a FreeBSD AMD64 binary. Run it as one application
process with your existing MongoDB configuration and reserved HTTP port.

## SSH and execution permission

Use the SSH hostname and login from the hosting account's welcome message.

```sh
ssh LOGIN@SSH_HOST
devil binexec on
```

Binexec is also available under DevilWEB's additional services. Reconnect SSH
after changing it. Extract the Lite ZIP into your chosen project directory.
For an existing installation, follow `MIGRATION.md` first.

```sh
cd ~/GateStream
chmod +x start.sh scripts/watchdog.sh bin/gatestream-*
cp .env.example .env
chmod 600 .env
```

On an upgrade, copy the existing configuration instead of creating a fresh one.
Keep the previous bot's `bot_main.session` and matching `worker_N.session` files
for importing private-channel peer hashes. Go stores its native sessions under
`sessions-go/`.

## Reserve the port

Use DevilWEB's port reservation page or the provider's `devil` command.

```sh
devil port add YOUR_PORT TCP GateStream
devil port list
```

Reuse your existing reserved port. Set that value in `.env` and use the same
value for the website's HTTP proxy target. The application validates the port
before starting Telegram clients.

## Configure the application

Fill in the API ID/hash, bot token, owner ID, storage channel, MongoDB URI and
current database name. The following values are optional examples for a small
hosting account. Replace the port and public URL with your own settings.

```dotenv
BIND_ADDRESS=127.0.0.1
PORT=YOUR_RESERVED_PORT
BASE_URL=https://your-worker.example
MULTI_BOT_TOKENS=""
MEDIA_CACHE_MAX_MB=64
CACHE_FREE_MB=128
HANDLER_WORKERS=2
GO_MAX_PROCS=2
MAX_TRANSMISSIONS=2
MAX_HTTP_CONNECTIONS=8
MEMORY_CLEAN_MB=350
MEMORY_RESTART_MB=440
```

Set the cache budget against the total account quota and allow room for the
binaries, Go sessions, logs and backups. More Telegram workers add connections
and memory use. Start with the primary bot and add workers according to measured
use. These values are configuration examples.

System environment takes precedence over `.env`, followed by `config.env`,
literal legacy launcher assignments and defaults. Empty exported values also
have precedence. Commented examples are ignored. Configure the file once and
restart after edits.

## Start and verify

```sh
cd ~/GateStream
./start.sh
```

The launcher chooses `bin/gatestream-freebsd-amd64` on FreeBSD AMD64. It reads
`.env` automatically. Logs show the effective bind address, port, connected
MongoDB, configured and connected workers, cache limit and HTTP readiness.

From a second SSH session, check the local HTTP endpoint with your reserved port.

```sh
curl -fsS http://127.0.0.1:YOUR_RESERVED_PORT/health
ps -ax -o pid,ppid,rss,command
```

Locate the GateStream process in the process list. RSS is in KiB. Compare memory
with the same worker count and similar traffic. `/stats` reports Go runtime
memory separately from hosting account RAM.

## Website proxy, SSL and Cloudflare

Create or edit a proxy website in DevilWEB. Its upstream should be plain HTTP
at `127.0.0.1` and your reserved port. The example uses IPv4 loopback.
A proxy targeting IPv6 loopback needs a matching bind address.

The earlier deployment guide's proxy creation command is retained here.

```sh
devil www add YOUR_DOMAIN proxy localhost YOUR_PORT
```

Configure the domain's DNS to the hosting account's web IP. Generate the public
certificate under SSL → WWW websites → Generate Let's Encrypt certificate, then
force SSL after certificate activation.

Point the Cloudflare Worker's `ORIGIN_URL` to this public HTTPS proxy address.
Set GateStream's `BASE_URL` to the Worker's public URL. Check `/health` through
both the public origin and Worker before testing a download. The Worker streams
ranges and forwards private sign-in and owner setup forms.

The origin uses the actual TCP peer for local analytics fallback. The Worker
supplies its stable anonymous grouping key. Authorization uses Telegram grants
and browser sessions.

## Persistent operation and recovery

Use hosting-supported Cron to recover after a crash, subject to the provider's
current usage rules. Replace the path with your actual application directory.

```cron
*/5 * * * * /bin/sh /usr/home/LOGIN/GateStream/scripts/watchdog.sh
```

The launcher uses `exec` and the Go process holds `.watchdog.lock`. Duplicate
Cron launches exit, and graceful restarts retain the lock and process ID.
After an exit, the next allowed Cron invocation can restart the application.

Disable that Cron entry before intentionally stopping the app. Find the actual
PID with `ps`, then send SIGTERM to that numeric PID. Ctrl+C works in foreground.
The server drains HTTP handlers, cancels reads and repair tasks, checkpoints
statistics and disconnects Telegram and MongoDB.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Permission denied while executing | Enable Binexec, reconnect SSH, and make the launcher and binary executable. |
| Bind operation not permitted | Confirm the configured port appears in `devil port list`. Read the effective bind address/port in the startup error. |
| Port already in use | Inspect the running PID and stop the earlier instance gracefully before launching the new one. |
| Invalid configuration | Read the named setting in the validation error. Check system environment overrides and active duplicate assignments. |
| Worker authentication failure | Check that worker's token. Empty `MULTI_BOT_TOKENS` disables extras. Connected workers continue serving. |
| Channel unavailable on an old link | Preserve each matching `.session` peer table. Check the downloading bot's channel membership and use `/workers`. |
| Commands fail to arrive | Check group privacy/administrator permissions, the `/filetolink` binding, and conflicting Bot API webhook or polling installations. |
| Proxy 502 | Check local `/health`, proxy address and reserved port. Public SSL terminates at the hosting proxy. |
| Disk quota exceeded | Lower the media cache limit and use `/clean`. Check backups, old source copies and other account files. Cache write failures fall back to Telegram reads. |
| Exec format error | Check `uname -s` and `uname -m`. Select the matching binary or cross-compile on a development machine. |
| Build runs out of memory | Use the supplied binary. Compile the generated MTProto SDK on a machine with sufficient RAM. |

FreeBSD binary compilation is checked from Linux. Live Telegram recovery,
provider process accounting and real Serv00 performance require verification on
the deployed account. Provider settings and restrictions remain authoritative.
