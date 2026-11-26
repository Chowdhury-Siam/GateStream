# GateStream on Serv00 / FreeBSD

GateStream v2.0.33 supports Python 3.11+ with a single asyncio HTTP/Telegram
process. No root access, Docker, systemd, Bash, Linux `/proc`, or Linux allocator
is required. The commands below describe Serv00; account limits and allowed
long-running processes are determined by your current hosting plan.

## 1. SSH and executable permissions

Connect using the account and SSH hostname in your Serv00 welcome email:

```sh
ssh LOGIN@SSH_HOST
```

Enable execution of your own programs using DevilWEB → Additional services →
Run your own applications, or:

```sh
devil binexec on
```

Disconnect and reconnect SSH after changing Binexec. Use your existing account;
`sudo`, package-manager changes, and privileged ports are unnecessary.

## 2. Install or upgrade

For a new installation:

```sh
cd ~
git clone https://github.com/Chowdhury-Siam/GateStream.git
python3.11 -m venv ~/.virtualenvs/gatestream
. ~/.virtualenvs/gatestream/bin/activate
cd ~/GateStream
python -m pip install --upgrade pip
python -m pip install --no-cache-dir -r requirements.txt
cp .env.example .env
chmod 600 .env
```

Use the provider's Python 3.11 executable if its command name differs. The POSIX
`.` activation command also works in `sh`; `source` is equivalent in Bash.
Dependencies already installed successfully do not need rebuilding unless their
pinned version changed. This release adds `python-dotenv`.

For an upgrade, stop your running instance, preserve your existing `.env`,
`config.env`/launcher if used, `bot_main.session*`, and `cache/`, then overlay the
updated source and install requirements in the same virtual environment. Never
replace your filled configuration with `.env.example`. No database migration,
record deletion, token reset, or link regeneration is needed. Keep the same
`DATABASE_URL`, `DATABASE_NAME`, `OMNI_MONGO_URI`, primary token and owner ID;
the repair-session encryption key depends on the existing credentials.

## 3. Reserve a TCP port

Use DevilWEB → Port reservation, or substitute your own available port below:

```sh
devil port add YOUR_PORT TCP GateStream
devil port list
```

Use an existing reservation if you already have one. Set the same port in `.env`
and the website's proxy target. Serv00's documented reservation range is
1024–64000; application validation also accepts other legal TCP ports for other
hosts. The application never chooses a Serv00 port automatically.

## 4. Configure `.env` once

Edit `~/GateStream/.env` with your editor. Supply the Telegram API ID/hash,
primary BotFather token, positive `OWNER_ID`, reachable MongoDB URI, existing
database name and storage channel. Example resource settings for the reported
512 MiB RAM / 3 GiB disk account are below. Replace `YOUR_PORT` before starting:

```dotenv
BIND_ADDRESS=127.0.0.1
PORT=YOUR_PORT
BASE_URL=https://your-public-host.example
MULTI_BOT_TOKENS=""
ZIP_CACHE_MAX_MB=512
MEDIA_CACHE_MAX_MB=64
CACHE_FREE_MB=128
HANDLER_WORKERS=2
IO_WORKERS=2
MOTOR_MAX_WORKERS=2
MAX_TRANSMISSIONS=2
MEMORY_CLEAN_MB=350
MEMORY_RESTART_MB=440
```

These are optional tuning examples, not hardcoded platform defaults. With no
extra tokens only the primary client is used. More bots increase connections,
Telegram session overhead and memory; tune against your account's process and
thread limits. Do not start multiple application processes for the same bot.

Configuration is resolved **per key**, in this order:

1. System environment, including a value explicitly set to an empty string.
2. `.env` in the working directory (normally the project directory).
3. `config.env` in that same directory, for legacy deployments.
4. Literal assignments / `export KEY=value` in `start.sh`, for legacy deployments.
5. Application defaults.

If none of those files exists in the working directory, the source project
directory is used. Launch from the project as shown below. No parent-directory
search and no automatic execution or sourcing of shell files occurs. Put active
settings in `.env`; remove obsolete duplicate exports from your process manager
when they should no longer override the file. You never need to export values
manually before each start. Restart after editing configuration.

Comments and empty lines are ignored. Duplicate keys within a file use the last
assignment. `KEY=` and `KEY=""` explicitly set an empty value; bare `KEY` without
`=` is not an assignment. Invalid assignments identify their filename and line,
without displaying the line's contents. Single/double quotes, multiline quoted
values, UTF-8 BOM, Unix LF and Windows CRLF are supported by python-dotenv's
parser. Double quotes support dotenv escapes; single quotes preserve ordinary
backslashes. An unquoted `#` starts a comment when preceded by whitespace:

```dotenv
# This PORT=7860 is documentation, not configuration.
PORT=YOUR_PORT
EXAMPLE_SECRET='spaces # hashes = equals $dollars ! punctuation'
EXAMPLE_VALUE=abc#def=ghi
```

Values are not expanded as shell code or general `${VARIABLE}` templates. The
legacy literal `${NAME:-fallback}` form is supported against the system
environment. Shell command substitution and command chains in `start.sh` are
ignored. Use literal values in `.env`. Percent-encode special characters in
MongoDB usernames/passwords as required by MongoDB URI syntax.

An empty required value (such as `PORT` or the primary token) causes a clear
validation failure. Empty optional tokens disable extra workers, empty repair
credentials disable the legacy override, and empty `BASE_URL`/cache paths choose
automatic defaults. Zero ZIP/media cache limits disable that cache. Negative
limits are invalid. `MEMORY_RESTART_MB=0` disables memory-triggered restarts.

Worker tokens accept commas, semicolons or whitespace. Duplicates, the primary
token, malformed values and common placeholders are ignored. A syntactically
valid token can still be revoked or invalid at Telegram: that worker is isolated,
logged by number/error class, and skipped for media retrieval. Other workers and
the primary continue. Fix its token and restart to reconnect it. Existing worker
checks, slow repair, grants, sessions and database records are preserved.

## 5. Start and verify

After configuration, these are the only normal startup commands needed:

```sh
cd ~/GateStream
source ~/.virtualenvs/gatestream/bin/activate
python -m bot
```

In a POSIX `sh` session replace `source` with `.`. Logs show the application
version, OS/Python version, bind address/port, ZIP limit, MongoDB ping result and
connected/configured client counts. `HTTP server ready` appears only after the
listener and ASGI startup succeed. Test from another SSH session:

```sh
curl -f http://127.0.0.1:YOUR_PORT/health
```

Expect `OK` and an `X-GateStream-Version` header with `curl -i`. The listener is
reserved before Telegram startup; a port conflict/permission failure exits with
a nonzero status and closes resources. Ctrl+C or `kill -TERM APP_PID` drains
HTTP requests up to `SHUTDOWN_TIMEOUT` (default 20 seconds), stops background ZIP
work and Telegram clients, flushes counters and closes MongoDB. Allow cleanup to
finish before restarting. SIGKILL cannot perform cleanup. `/restart` and the
memory guard run the same shutdown before replacing the process.

## 6. Reverse proxy and SSL

Create a **Proxy** website in DevilWEB with your chosen hostname, target
`127.0.0.1` (or the server-local address required by your panel) and your reserved
TCP port. The application speaks plain HTTP to that proxy; do not enable HTTPS
for the upstream target. Enable a certificate/HTTPS for the public website
separately using SSL → WWW websites → Generate Let's Encrypt certificate, then
enable force SSL when the certificate is ready. DNS must point to the account's
web IP. An equivalent proxy creation example is:

```sh
devil www add YOUR_DOMAIN proxy localhost YOUR_PORT
```

Do not add a second website with the same hostname if one already exists; edit
its proxy settings instead. If your proxy connects over IPv6, configure its target
explicitly as IPv4 or adjust `BIND_ADDRESS` to match. Only expose a public bind
address if the hosting setup requires it. `TRUSTED_PROXY_IPS` defaults to
`127.0.0.1`; add a trusted proxy's actual address if necessary. Never trust
forwarded headers from arbitrary clients on an exposed origin.

For an existing Cloudflare Worker, retain its routes and credentials. Set its
origin to the **public HTTPS reverse-proxy address**, and set the bot's `BASE_URL`
to the public Worker/custom domain used in file links. The Worker cannot reach
Serv00's private loopback address. Preserve forwarding of `Range`, `If-Range`,
`Content-Range`, `Content-Disposition` and anonymous download-group headers in
your existing Worker. Check `/health`, a small single-file download and a resumed
ZIP through the public domain. This GitHub package includes the Worker in `cloudflare/` and its deployment
workflow in `.github/workflows/`. See [CLOUDFLARE.md](CLOUDFLARE.md) for deployment.

## 7. Persistent operation and recovery

Use only process persistence and Cron methods allowed for your account. No root
service is required. With your virtual environment active, a basic background
start is:

```sh
cd ~/GateStream
nohup python -m bot </dev/null >/dev/null 2>&1 &
```

Application logs still rotate in `event-log.txt` (2 MiB each, two backups).
Record the process ID printed by your shell; stop that specific application with
SIGTERM. Avoid broad `pkill python` commands on shared accounts.

For automatic recovery where permitted, use the supplied watchdog **instead of**
a separate manual/background instance. In `crontab -e`, insert one line with
absolute paths (replace LOGIN and the actual home path):

```cron
*/5 * * * * /bin/sh /usr/home/LOGIN/GateStream/scripts/watchdog.sh
```

The launcher locates `.venv`, then `venv` in the project, then
`~/.virtualenvs/gatestream`. For a different environment, set `GATESTREAM_PYTHON`
to its absolute Python path in the Cron entry. The watchdog holds a project-local
advisory lock while its child runs, so subsequent Cron invocations exit. It uses
one extra supervisor process and does not grow a separate unbounded log. After a
crash, the next Cron invocation retries; it does not spin in a restart loop.
Disable that Cron entry before intentionally stopping the application, and stop
both supervisor and child gracefully. Review failures in `event-log.txt` before
allowing repeated retries. Hosting suspension/account expiry is outside the
application's recovery control.

## 8. Limits and troubleshooting

The ZIP limit is a disk budget, not an allocation of RAM. Keep ZIP and media
limits below **remaining account quota**, after the virtual environment, source,
sessions, logs and other applications. Disk-free APIs can report shared filesystem
space rather than your individual quota. Writes can still hit `EDQUOT` or
`ENOSPC`; partial cache files are removed and the transfer reports failure rather
than publishing a corrupt ready ZIP. Media caching is optional and failed cache
writes do not invalidate the source download. ZIPs exceeding admission capacity
retain the existing direct-stream fallback. A cached ZIP that fails mid-build
cannot transparently change to a different byte source under active range reads;
retry with a new link after fixing quota. Completed ZIPs stay available for their
existing lifetime to preserve resume; they are not evicted just to admit new work.

| Symptom | Check |
| --- | --- |
| Bind `operation not permitted` / Errno 1 | `devil port list`; configured/reserved port must match. Check Binexec and reconnect SSH. Startup logs show the effective port. |
| Port stays at an old value | A system variable/process-manager export has priority; remove/update it once. Comments cannot supply values. Check duplicate active assignments. |
| Address already in use | Stop the previous instance gracefully; avoid simultaneous watchdog and manual launches. |
| Proxy 502 | Local `/health`, target port/address, app readiness; upstream must use HTTP, public SSL is separate. |
| `ACCESS_TOKEN_INVALID` | Replace that worker's actual BotFather token; blank `MULTI_BOT_TOKENS` disables extras. No commented sample is loaded. |
| No-plugin warning on an old deployment | Upgrade the whole source. Active plugins use Hydrogram class decorators and explicit discovery, with commands/callbacks registered once. |
| `No space left` / `Disc quota exceeded` | Check account quota in the panel, reduce both caches, remove unused files when the bot is stopped; shared disk free is not account quota. |
| Compiler/TgCrypto/cryptography installation errors | Verify Binexec and virtualenv Python; use provider-supported build tools. Already installed extensions need no OS replacement. |
| Too many processes/threads / memory kill | Reduce extra workers, HTTP demand, IO/Motor/handler workers and transmission concurrency; leave RAM margin for the host. |
| Shutdown executor error | Use `python -m bot` from this release, allow SIGTERM cleanup; do not embed the old `idle()`/manual loop launcher. |
| MongoDB connection fails | Check the existing URI, DNS, allowlist, reachable port and TLS; do not rename or recreate your database to fix connectivity. |

## References and verification

Provider instructions were checked against the official documentation:
[Binexec](https://docs.serv00.com/Binexec/),
[Python](https://docs.serv00.com/Python/),
[ports](https://docs.serv00.com/Port_reservation/),
[proxy websites](https://docs.serv00.com/WWW/),
[SSL](https://docs.serv00.com/SSL/) and [Cron](https://docs.serv00.com/Cron/).
Parser grammar: [python-dotenv](https://bbc2.github.io/python-dotenv/).

Run regression tests with `python -m unittest discover -s tests -v`. Tests use
fake Telegram/MongoDB services and real local HTTP listeners for signal shutdown.
FreeBSD allocator/accounting paths and quota errors are simulated in unit tests;
actual Serv00 credentials, live Telegram recovery, public proxy performance and
account-specific limits must be verified on the deployment. This release was
built and tested on Linux, not an actual FreeBSD host.
