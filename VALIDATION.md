# GateStream 3.0.1 validation

- 36 top-level Go tests passed in each package with race detection on Linux
  AMD64, Go 1.25.4.
- `go vet ./...` passed.
- 9 Cloudflare Worker tests passed on Node.js 24.
- Linux AMD64 and FreeBSD AMD64 binaries compiled with CGO disabled.
- The complete regression test binary also cross-compiled for FreeBSD AMD64.
- Both packaged binary hashes passed release verification.
- Both Linux launchers print GateStream 3.0.1, including the watchdog with
  an empty Cron-style environment.
- POSIX shell syntax and launcher checks passed for FreeBSD selection,
  sparse environments, paths with spaces, arguments, diagnostic/exit-code
  preservation, missing binaries and permissions, unchanged process IDs,
  and acceptance/rejection of valid/corrupted release checksums.

The tests exercise HTTP downloads over a real local TCP server, exact byte
ranges, HEAD, overlapping completion accounting, APK response metadata, bounded
reads, reference refresh and the primary/foreign data-center media-pool split.
They also cover configuration grammar and precedence, cache budgets and cleanup,
legacy backup validation, nested BSON map decoding, encrypted credentials, Hydrogram session conversion,
command registration, legacy browser cookie migration, HTTP bind failure, transfer cancellation and lock retention
across an exec restart with the same PID. Additional subprocess checks exercise
foreground/Cron exclusion and recovery after SIGINT, SIGTERM and SIGKILL.

Telegram RPC responses and stored file lookups are deterministic test fixtures.
Full Telegram authentication, owner login and repair, MongoDB persistence round
trips, FreeBSD execution and real Serv00 memory/speed need deployment verification.
The packaged application uses the official MongoDB Go driver and existing
BSON record representations. Deployment verification requires actual services.

The supplied binaries contain the same application code in both packages.
The GitHub package additionally contains the Cloudflare proxy and workflows.

A GitHub CI job is configured to execute the application and regression binary
inside FreeBSD 14.3 AMD64. That remote workflow was not run during this update.
The source-only checkout workflow builds both platform binaries and uploads a
downloadable artifact; deployment ZIPs include both binaries directly.
