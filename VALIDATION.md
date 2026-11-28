# v2.0.40 validation

All 112 Python tests passed locally. Live stats coverage includes topic routing,
owner-only access, changes to transfer counters, concurrent dashboard moves,
failed deletion, restart recovery, and Telegram flood waits. The tests mock
Telegram and MongoDB.

All seven Cloudflare Worker tests passed locally.

# v2.0.39 validation

All 106 Python tests passed locally. The suite checks that Hydrogram parses
the upload message into separate quotations and a copyable URL block, including
Unicode filenames. It also verifies the download, stream, and revoke buttons
and that `/link` retries use the same layout.

All seven Cloudflare Worker tests passed locally.

# v2.0.38 validation

The Python regression suite passed all 106 tests on Linux. It covers the
separate welcome and help commands, retired web routes and plugin registration,
worker diagnostics, and existing download and playback behavior.

All seven Cloudflare Worker tests passed locally.

# v2.0.36 validation

The GitHub package passed 119 Python tests on Linux with Python 3.11.13 and 3.12.14.
Checks cover single-process launch, duplicate prevention across restarts, cache
cleanup, legacy statistics and individual download ranges.

The median idle footprint fell from 95.8 MiB to 80.3 MiB PSS over three runs per
build on Linux using Python 3.11.13. The bot loaded its real handlers and HTTP
server and answered `/health`. Telegram and MongoDB were mocked. These are local
process measurements. Serv00 account memory depends on live clients, MongoDB,
traffic, and other processes on the account.

All eight Cloudflare Worker tests passed on Node.js 24.
