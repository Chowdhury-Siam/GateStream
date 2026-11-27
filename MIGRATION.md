# Upgrade from GateStream 2.0.36 to 3.0.0

1. Stop the existing application gracefully and disable its cron entry during
   the upgrade. Check the running PIDs before terminating them.
2. Keep the current source directory as a rollback copy. Extract the appropriate
   Go package into a clean application directory.
3. Copy the current `.env` or `config.env`, primary `bot_main.session`, and
   `worker_N.session` files into the new project root. Preserve each session's
   matching worker number. The Go reader also checks a `sessions/` subdirectory.
4. Copy `cache/media` if you want to retain those files in the configured cache
   budget. Copy an existing `sessions-go/` directory on later Go upgrades.
5. Keep the existing database names, connection strings, tokens, channel ID,
   owner ID, public URL and HTTP port. Review `.env.example` for Go settings.
6. Make the launcher and binaries executable, then run `./start.sh`.
7. Check `/health`, `/stats`, `/workers`, one existing file link, a newly uploaded
   file, and a resumed range download through the public Worker URL.
8. Restore the cron entry using `scripts/watchdog.sh` after verification.

The Go runtime authenticates the configured bots into `sessions-go/`. It reads
legacy SQLite peer tables without changing them, so private-channel hashes can
be resolved by the corresponding bot. Existing MongoDB file codes, access grants,
forum binding, browser session hashes, first-start notices and lifetime transfer
totals retain their representations. Historical fields are retained when current
activity counters are checkpointed.

Owner repair credentials use the existing AES-GCM key derivation and Hydrogram
session-string layout. Existing encrypted repair sessions can be opened by the
Go version. A newly connected owner account is saved in that compatible format.
The setup page requires the account ID to match `OWNER_ID`.

The command `python -m bot` belongs to the earlier runtime. Start the Go version
with `./start.sh`. Bot commands and web routes are implemented in Go. Bot command
updates use Telegram Bot API and media reads use MTProto. An existing webhook or
another Bot API poller can interfere with command reception. Resolve that
conflict before running this installation as the active bot.

To roll back, stop the Go process, restore the previous source and launcher, and
reuse the same configuration and legacy `.session` files. Keep the database and
its records. A library login established in the new version may need a fresh
sign-in through the previous version's bot menu.
