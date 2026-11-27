# Changelog

## v3.0.1

- Update Serv00 deployment for the native Go binary, including executable
  checks, checksums, automatic configuration, proxy setup and sparse Cron
  environments. Remove obsolete runtime commands from current instructions.
- Preserve watchdog diagnostics and exit codes; execute through POSIX sh and
  retain one application process. No virtual environment or shell supervisor.
- Share the duplicate-instance lock across foreground, direct and Cron starts.
  Preserve it across exec restarts and release it on signals or crashes so
  the next scheduled Cron invocation can recover.
- Add launcher checks for FreeBSD AMD64 selection, paths with spaces, argument
  forwarding, missing permissions/binaries, startup failures and PID retention.
  Extend signal/crash lock-recovery tests and add a FreeBSD 14.3 CI job.
- Package newly built Linux/FreeBSD AMD64 binaries with matching SHA256SUMS;
  publish CI build artifacts and exclude secrets/state from Docker build input.
- Preserve all file routes, database settings, session migration and bot features.

## v3.0.0

- Rewrite the bot, HTTP origin, personal library, worker repair and activity
  tracking in Go with gotd/td for MTProto media reads.
- Preserve existing MongoDB file codes, collection names, access grants, session
  hashes, encrypted repair credentials and cumulative counters.
- Bound active HTTP media connections and ordered look-ahead alongside global
  Telegram read concurrency. Refresh references on the downloading bot.
- Supply Linux AMD64 and FreeBSD AMD64 binaries, a native process-lock launcher,
  deployment documentation and regression tests.
- Carry the configured filename and MIME type to downloads, including APKs.
- Add authenticated transfer Stop forwarding to the Cloudflare Worker.


## v2.0.36

- Run the cron-managed bot as a single process. The app holds the existing lock
  across graceful restarts, so duplicate cron invocations still exit.
- Defer metadata networking, worker setup encryption, and the optional upstream
  MongoDB connection until needed. Let unused MongoDB data connections expire.
- Cap Hydrogram's message cache at 512 entries for the primary bot and 128 per
  auxiliary worker. Media lookups skip reply history.
- Remove the redundant bandwidth write after transfers and the retired ZIP
  counter from the tracker. Activity checkpoints retain historical MongoDB fields.
- Restore disk cache eviction for changed and revoked files. The owner’s
  `/clean` command clears Telegram caches and local media chunks.

## v2.0.35

- Remove the Telegram `/index` command and its menu button, batch ZIP creation,
  folder sharing, archive routes and archive cache.
- Show files from old folders individually in the signed-in library alongside
  other uploads. Existing single-file URLs and the MongoDB records still work.
- Keep HTTP byte ranges for single-file resume and download managers. A ZIP
  uploaded as an individual Telegram file can still be downloaded.
- Carry forward older ZIP totals in stored checkpoints for compatibility.
  The live dashboard and Telegram stats show individual-file totals.

## v2.0.34

- Check RSS every five seconds in one application task instead of forcing a full
  garbage collection when each HTTP range finishes. Keep memory pressure cleanup
  and cooperative restarts at the configured thresholds.
- Read a download's first chunk through its normal data path and fill the disk
  cache with bytes already being streamed. Keep one speculative tail read at a
  time for browser playback.
- Apply Telegram read deadlines in the existing task on Python 3.11. Keep
  range ordering, timeout recovery, and byte-accurate resume.

## 2.0.33 — 2026-11-26

This full GitHub distribution combines the v2.0.33 configuration, lifecycle and
FreeBSD fixes with the original v2.0.31 GitHub Worker, deployment workflow and
hosting guides. The original GitHub and Lite Python cores were byte-identical.
The fixes were developed against Lite revision
`6ba04417f3309c67328558e3f167c75cd9cb1b21` (application version 2.0.31).

### Configuration

- Replace the regex assignment scanner with python-dotenv's parsing grammar.
  The scanner searched comments and retained the first match, so the commented
  Docker `PORT=7860` example and three example worker tokens could override later
  active values. It also read `start.sh` ahead of `.env` and discarded empty values.
- Centralize settings with per-key precedence: system environment (including
  explicit empty values), `.env`, `config.env`, literal legacy `start.sh`
  assignments, application defaults. Do not execute launchers or shell expansion.
- Support UTF-8 BOM, CRLF/LF, quotes, multiline strings, inline comments and
  literal special characters. Report malformed assignments by file/line without
  including their contents. Duplicate active file keys use the last assignment.
- Validate ports, numeric limits, required settings and URLs before creating
  service connections. Keep existing names/aliases, Spaces defaults and Docker
  environment behavior. Invalid integers no longer silently become defaults.
- Load ZIP/media cache paths and budgets through the same configuration as the
  HTTP/Telegram services. Add optional bounded IO/Motor/handler worker settings,
  Telegram transmission concurrency, memory thresholds and shutdown timeout.
- Make the token editing script use parsed configuration and dotenv updates,
  preserving comments and writing `.env` instead of rewriting example comments
  or launcher scripts. Hidden token input is available without CLI arguments.

### Telegram workers and handlers

- Deduplicate worker tokens, accept whitespace/comma/semicolon separators, and
  skip blank, malformed and placeholder entries. An empty configured token list
  starts no auxiliary bots.
- Isolate failed worker authentication/startup, close its partial transport and
  executor, keep healthy workers available and skip failed clients during media
  selection/heartbeat. Configured clients remain visible to worker diagnostics.
- Preserve existing membership checks, slow repair, encrypted owner session
  setup and all stored worker records. Close temporary repair/setup clients and
  expiry tasks as part of their lifecycle.
- Investigate and fix `No plugin loaded`: bound client decorators registered
  commands immediately but left no discoverable handler metadata. Use Hydrogram
  class decorators with the existing explicit active-module list. The loader now
  registers all 22 command/callback handlers once; retired overlays remain blocked.

### Startup, shutdown and hosting

- Run services in one `asyncio.run` lifecycle, with one owner of SIGINT/SIGTERM.
  Prebind the configured socket before connecting MongoDB or Telegram. Report
  readiness only after Uvicorn has completed actual startup.
- Log version, OS/Python, effective HTTP address, ZIP budget, MongoDB ping and
  connected/configured fleet counts. Redact configured secrets, token patterns
  and database credentials in rendered third-party messages and tracebacks.
- Drain HTTP, cancel/await application jobs, close cached ZIP builders and login
  setup, flush counters, stop all Telegram clients and close MongoDB before the
  event loop/executors are shut down. Attempt each cleanup independently.
- Route `/restart` and memory-pressure restarts through that cleanup, then replace
  the process only after the loop and its executor have finished.
- Use platform-specific optional allocator hooks; correct macOS RSS units, avoid
  Linux filesystem assumptions and make memory thresholds configurable.
- Apply cache budgets before writes. Atomically publish media chunks, remove
  incomplete files after quota/write failures, reconcile missing entries and
  enforce a reduced media budget on startup. Preserve unexpired ZIPs for resume.
  Respect a configurable free-space margin and handle account quota failures
  even when shared filesystem free space appears sufficient.
- Replace the Bash/pgrep watchdog with a POSIX launcher and project-specific
  advisory-lock supervisor. One supervisor is sufficient; optional Cron recovery
  must follow provider rules. Add a dedicated Serv00/FreeBSD deployment guide.

### Compatibility and verification

No database names, collections, schemas, stored records, URL formats or external
Worker routes are migrated. Existing access restrictions, personal folders,
automatic group topic handling, APK names/MIME types, Telegram reference recovery,
streaming/ranges, background ZIP preparation and persistent transfer totals remain.
No Drive functionality is introduced.

Regression tests cover configuration grammar/precedence, worker authentication
isolation and disabling, actual handler discovery, cache limits/quota errors,
portable memory paths, occupied ports, task draining, and real local HTTP
SIGINT/SIGTERM shutdown. See `VALIDATION.md` for the tested environments and limits.
