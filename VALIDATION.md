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
