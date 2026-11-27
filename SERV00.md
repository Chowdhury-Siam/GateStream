# GateStream 3.0.2 on Serv00 / FreeBSD

Run the supplied native FreeBSD AMD64 binary as one application process.
The release ZIP includes `bin/gatestream-freebsd-amd64`, its Linux counterpart,
and `bin/SHA256SUMS`. A Go compiler is needed only when building from source
on a development machine. Use `MIGRATION.md` before upgrading an existing bot.

## SSH, Binexec and installation

Use the SSH hostname and login from the hosting account's welcome message.

```sh
ssh LOGIN@SSH_HOST
devil binexec on
```

Reconnect SSH after enabling Binexec. Extract the project into your chosen
application directory. The examples below use `~/GateStream`. Substitute your
directory consistently, including the absolute Cron path. With a GitHub source
archive or checkout, install the binary using the download steps below. With the
bundled ZIP, run `chmod 700 bin/gatestream-linux-amd64 bin/gatestream-freebsd-amd64`
and verify both binaries with `/bin/sh scripts/check-release.sh`.

```sh
cd ~/GateStream
uname -s
uname -m
chmod 700 start.sh scripts/watchdog.sh
/bin/sh start.sh --version
```

FreeBSD AMD64 reports `FreeBSD` and `amd64`. The launcher automatically selects
`bin/gatestream-freebsd-amd64`, changes to the project root, and uses a private
file-creation mask. It works when invoked from another directory and through
`/bin/sh`. No additional supervisor process remains running.

For a new installation only, create the configuration:

```sh
cp .env.example .env
chmod 600 .env
```

On upgrades, retain your actual configuration, `sessions-go/`, and each original
`bot_main.session` / matching `worker_N.session` if present. Legacy peer tables
are read without modification. Keep the existing database name and contents.

## Download a versioned binary

Choose a published tag from [GitHub Releases](https://github.com/Chowdhury-Siam/GateStream/releases).
Each release has direct downloads for FreeBSD AMD64, Linux AMD64, and the
`SHA256SUMS` manifest. For example, once `v3.0.2` is published:

- [FreeBSD AMD64](https://github.com/Chowdhury-Siam/GateStream/releases/download/v3.0.2/gatestream-freebsd-amd64)
- [Linux AMD64](https://github.com/Chowdhury-Siam/GateStream/releases/download/v3.0.2/gatestream-linux-amd64)
- [SHA256SUMS](https://github.com/Chowdhury-Siam/GateStream/releases/download/v3.0.2/SHA256SUMS)

From the project directory on Serv00, stage the FreeBSD binary and verify its
checksum and version. You can do this before stopping a running bot. Set `tag`
to the published version you want and keep the same SSH session for the next step.

```sh
tag=v3.0.2
asset=gatestream-freebsd-amd64
url="https://github.com/Chowdhury-Siam/GateStream/releases/download/$tag"
mkdir -p bin
stage=$(mktemp -d "$PWD/bin/.gatestream-download.XXXXXX")
(
    set -eu
    cd "$stage"
    curl -fsSL -o "$asset.download" "$url/$asset"
    curl -fsSL -o SHA256SUMS "$url/SHA256SUMS"
    expected=$(awk -v name="$asset" '$2 == name { print $1 }' SHA256SUMS)
    actual=$(sha256 -q "$asset.download")
    if [ -z "$expected" ] || [ "$actual" != "$expected" ]; then
        echo 'FreeBSD binary checksum verification failed.' >&2
        exit 1
    fi
    chmod 700 "$asset.download"
    version=$("./$asset.download" --version)
    if [ "$version" != "GateStream ${tag#v}" ]; then
        printf 'Expected GateStream %s, got %s\n' "${tag#v}" "$version" >&2
        exit 1
    fi
    mv "$asset.download" "$asset"
    printf 'Verified %s in %s\n' "$version" "$stage"
)
```

After verification succeeds, install into a new checkout with
`mv "$stage/$asset" "bin/$asset"`. For an existing installation, follow the
upgrade steps below. The download manifest verifies the selected binary.
`scripts/check-release.sh` verifies both binaries supplied together in a ZIP.

## Reserve the HTTP port

Use DevilWEB's Port reservation page or the provider's command:

```sh
devil port add YOUR_PORT TCP GateStream
devil port list
```

Reuse your assigned port. Set it in `.env`, then use the same value as the
website proxy target. The application validates configuration and binds the
port before starting Telegram clients. No port is specific to this release.

## Configuration

Enter your actual API ID/hash, bot token, owner ID, storage channel, database
connection/name and public URL in `.env`. These optional resource settings are
examples for a small account; replace the port and URL placeholders:

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

Configuration precedence: system environment, `.env`, `config.env`, literal
legacy launcher assignments, defaults. Explicitly empty exported values also
override file values. Comments, blank lines, quoted values, CRLF and UTF-8 BOM
are supported. Keep configuration in `.env` rather than editing the launchers;
manual exports and interactive shell initialization are unnecessary. Restart
after editing. A stale system environment override still takes precedence.

Allow disk space for sessions, logs, backups and both packaged binaries.
`MEDIA_CACHE_MAX_MB` bounds recognized media chunks; `CACHE_FREE_MB` reserves
filesystem free space where available. Account quotas can be smaller than
reported filesystem free space: cache quota failures fall back to Telegram.
Go's soft memory target is not a hard cap on hosting-account RAM. More worker
clients and simultaneous downloads need more memory and sockets. Keep all
resource settings configurable and measure actual usage on your account.

## First start and verification

```sh
cd ~/GateStream
./start.sh
```

Startup reads the configuration automatically. The logs report the version,
platform, effective bind address/port, MongoDB status, configured/connected
workers, media-cache budget and HTTP readiness. Readiness is logged after bind.
The rotating `event-log.txt` has two backups, unless `LOG_FILE` changes the path.
Configuration errors before logging starts remain visible on standard error.

From a second SSH session, use your configured port:

```sh
curl -fsS http://127.0.0.1:YOUR_RESERVED_PORT/health
ps -ax -o pid,ppid,rss,command
```

Locate the actual GateStream process; RSS is KiB. Check `/stats`, `/workers`,
an existing file URL, a newly uploaded file and a resumed range download.
Only after successful verification, enable background recovery.

## Proxy, SSL and Cloudflare Worker

Create a Proxy website in DevilWEB targeting the same server and reserved port.
GateStream serves plain HTTP locally, so leave upstream HTTPS disabled. Public
SSL is configured separately for the website. Loopback address families must
match the configured bind address; the example uses IPv4.

The provider also supports the following proxy command:

```sh
devil www add YOUR_DOMAIN proxy localhost YOUR_PORT
```

Point domain DNS to your hosting web IP, provision a certificate using the
panel's SSL controls, then enable force SSL after the certificate is active.
Configure the Worker's `ORIGIN_URL` as that public HTTPS origin and GateStream's
`BASE_URL` as the public Worker URL. Keep existing public URLs when upgrading.
Check `/health` through both URLs, then test an actual range download. The
GitHub package includes the Worker source and deployment workflow.

Private sign-in and owner setup require HTTPS through the public endpoint.
The proxy must forward cookies, form POSTs, byte ranges and streaming responses.
Do not cache private pages or authenticated API responses.

## Cron recovery and automatic restart

Use the hosting panel's Cron jobs tab or `crontab -e`. Replace `LOGIN` and the
project directory below with the real absolute path. Keep exactly one periodic
recovery entry. FreeBSD uses `/bin/sh`; no login shell profile is required.

```cron
SHELL=/bin/sh
PATH=/bin:/usr/bin:/usr/local/bin:/sbin:/usr/sbin
*/5 * * * * /bin/sh "/usr/home/LOGIN/GateStream/scripts/watchdog.sh"
```

An optional boot entry can start the bot when Cron starts:

```cron
@reboot /bin/sh "/usr/home/LOGIN/GateStream/scripts/watchdog.sh"
```

Use only schedules and persistent operation permitted by the hosting provider.
The periodic entry starts the bot immediately if it is absent. Recovery after
an unexpected exit occurs at the next scheduled run, up to five minutes later
for this example. `@reboot` alone does not recover later crashes. Cron runs the
long-lived application; its next invocation exits quietly if the bot is alive.

`watchdog.sh` uses `exec` through the launcher. The Go process owns an OS advisory
lock in `.watchdog.lock`; foreground, direct binary launches from the project directory, and Cron
starts share it.
There is no shell polling loop, external `flock` command or PID-name search.
A leftover lock file after a crash is harmless: the kernel releases its lock.
Keep the file in place while the application is running.

Owner `/restart` and configured memory restarts drain services, checkpoint
statistics and replace the executable while retaining the PID and lock. Crash
recovery is handled by Cron. Both wrappers preserve arguments, failure exit
codes and diagnostics. Cron can deliver output through its configured mail;
inspect it when startup fails. Application logs also rotate on disk. Avoid
redirecting diagnostics into an unbounded extra log on a quota-limited account.

To start without waiting for Cron, invoke the watchdog from SSH. To leave it
running after SSH disconnects, after the foreground check use:

```sh
nohup /bin/sh "$HOME/GateStream/scripts/watchdog.sh" >/dev/null 2>&1 </dev/null &
```

This explicit redirect discards console diagnostics; inspect `event-log.txt`
and `/health`. The same advisory lock prevents overlap with the Cron entry.

## Stop and upgrade

Disable recovery Cron before intentionally stopping or upgrading. Find the
numeric PID with `ps`, then send `kill -TERM PID`. Ctrl+C stops a foreground
run. The server drains HTTP, cancels tasks and reads, checkpoints statistics,
and disconnects Telegram and MongoDB. Keep the lock file in place.

For an existing Go installation using the current launchers, download and
verify the new FreeBSD binary as shown above. After disabling Cron and waiting
for the old process to exit, back up the old binary and install the verified one.

```sh
cp -p "bin/$asset" "$stage/previous" &&
    mv "$stage/$asset" "bin/$asset" &&
    /bin/sh start.sh --version
```

Start with `/bin/sh start.sh`, then check `/health` and a download from a second
SSH session. Restore the recovery Cron entry after verification. This upgrade
uses the same project directory, configuration, sessions, database and Cron path.
Keep the staging directory for rollback. To roll back, disable Cron and stop
the upgraded process, then run `mv "$stage/previous" "bin/$asset"`, start the
previous version and restore Cron after verification.

For a migration from the Python version or a release that also updates the
launchers, follow `MIGRATION.md`. Extract the full package into a clean directory,
transfer configuration and sessions, and update the Cron path if it changes.

## Publish a release from GitHub

In the GitHub repository, push a new `vX.Y.Z` Git tag on the commit you want to
release. The tag must match the version printed by the app. GitHub Actions
builds both AMD64 binaries, creates `SHA256SUMS`, and publishes the release
after the assets are uploaded.
An existing published tag is left untouched. Use a new application version and
tag for a corrected release. If a run leaves an unpublished draft, review or
remove that draft before retrying.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Permission denied | Enable Binexec, reconnect SSH, and make the binary executable. Running the wrappers via `/bin/sh` does not fix a non-executable binary. |
| Binary missing or exec format error | Check `uname -s` / `uname -m` and verify the FreeBSD AMD64 binary against the matching release manifest. |
| Bind operation not permitted | Confirm the effective `.env` port is reserved with `devil port list`; check environment overrides. |
| Port already in use | Stop the earlier bot gracefully. A different installation directory has a separate lock. |
| Second launch exits silently | The current project already has an instance holding its lock; check the process list and `/health`. |
| Cron does not start the bot | Verify the absolute path and Cron entry with `crontab -l`. Run the exact command manually; inspect Cron mail and application logs. |
| Invalid worker authentication | Check that worker's real token. Empty `MULTI_BOT_TOKENS` disables extras. Other valid workers continue. |
| Old private channel unavailable | Retain matching legacy peer tables and check downloading-bot membership with `/workers`. |
| Commands missing | Check Telegram group permissions, `/filetolink` topic binding, and another webhook/poller using the same token. |
| Proxy 502 | Check local `/health`, address family, reserved port and plain-HTTP upstream setting. |
| Disk quota exceeded | Inspect account usage, backups and old releases; reduce cache and use `/clean`. Keep session/configuration files. |
| Go compilation runs out of RAM | Deploy the precompiled binary. Build the SDK on a larger development machine. |

The regression suite verifies launcher behavior, duplicate prevention, signal
release and lock retention across exec on Linux. FreeBSD cross-compilation and
binary checks are separate from execution. The GitHub workflow also defines a
FreeBSD 14.3 VM job; its result must pass on your repository. Actual Serv00
permissions, Cron scheduling, live Telegram/MongoDB and performance require
verification on the deployed account.

Provider references: [Binexec](https://docs.serv00.com/Binexec/),
[port reservation](https://docs.serv00.com/Port_reservation/),
[Cron](https://docs.serv00.com/Cron/), and
[proxy websites](https://docs.serv00.com/WWW/). Provider rules remain authoritative.
