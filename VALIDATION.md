# GateStream 3.0.0 validation

- 34 top-level Go tests passed with race detection on Linux AMD64, Go 1.25.4.
- `go vet ./...` passed.
- 9 Cloudflare Worker tests passed on Node.js 24.
- Linux AMD64 and FreeBSD AMD64 binaries compiled with CGO disabled.
- The Linux launcher prints GateStream 3.0.0 through the packaged entry point.
- Shell syntax checks passed for the start and cron launchers.

The tests exercise HTTP downloads over a real local TCP server, exact byte
ranges, HEAD, overlapping completion accounting, APK response metadata, bounded
reads, reference refresh and the primary/foreign data-center media-pool split.
They also cover configuration grammar and precedence, cache budgets and cleanup,
legacy backup validation, nested BSON map decoding, encrypted credentials, Hydrogram session conversion,
command registration, legacy browser cookie migration, HTTP bind failure, transfer cancellation and lock retention
across an exec restart with the same PID.

Telegram RPC responses and stored file lookups are deterministic test fixtures.
Full Telegram authentication, owner login and repair, MongoDB persistence round
trips, FreeBSD execution and real Serv00 memory/speed need deployment verification.
A local MongoDB process could not start in this execution environment because
its startup returned an operation-not-permitted error. The packaged application
uses the official MongoDB Go driver and existing BSON record representations.

The supplied binaries contain the same application code in both packages.
The GitHub package additionally contains the Cloudflare proxy and workflows.
