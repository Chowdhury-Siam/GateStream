# Automatic channel indexing

Add the bot as an admin to the channel. In your private bot chat, send one
command with the link to the channel's most recent post:

```text
/index https://t.me/c/1234567890/5000
```

This scans message IDs 1–5000 automatically. There is no manual 200-message
batch splitting or category argument. Deleted/non-media posts are skipped.
Files appear under the **public Index tab** as they are saved. The bot reports
progress, added/updated/existing files, skipped IDs and unclassified files.
Only `OWNER_ID` and `AUTH_USERS` can start or cancel scans.

You can also reply `/index` to a forwarded channel post. Forwarding must retain
the originating channel and message ID; otherwise use the post link.

Telegram's history API is restricted to user accounts. A bot cannot reliably
find the last historical message in a channel it has just joined. GateStream
therefore needs the last post link once; it reads known message IDs using the
bot-compatible `get_messages` method. It does not create marker posts in your
channel or require a personal Telegram session.

The bot remembers new channel post IDs received while it is running. After the
first scan, continue with:

```text
/index @YourChannel
/index -1001234567890
```

If the bot has not observed the latest post, provide its link again. A scan
uses a snapshot of the known last ID; posts arriving during it are handled by
the next scan. Public channel links and private `t.me/c/...` post links are
accepted. Invite links cannot supply a channel message range.

## Resume, cancel and reclassify

```text
/cancelindex
/index https://t.me/c/1234567890/5000
/index https://t.me/c/1234567890/5000 rescan
```

Normal scans resume from the saved MongoDB `index_channels.last_scanned`
checkpoint after cancellation, a restart, or a failure. `rescan` rechecks the
range from the beginning, reusing saved download codes. Use it after adding
metadata credentials to retry unknown titles. Re-running a rescan starts that
recheck from the beginning; it does not erase the normal scan checkpoint.
Only one scan runs per bot process. Requests contain at most 50 IDs; Telegram
FloodWait delays are respected and can be interrupted with `/cancelindex`.

Existing `/link` records remain private. Scanning the same post creates a
separate public Index record instead of exposing that private record. Repeated
automatic scans reuse their public record and code. Older manually created
batches remain intact and may overlap files subsequently scanned individually.

`/catalog`, or `/index` without a target/reply, opens the web catalog and help.
The existing `/batch` commands remain available for explicit category/range
control. `/link` without a category now uses the same metadata classifier;
`/link movie ...`, `/link anime ...` and `/link series ...` explicitly override it.

## Media identification

The classifier cleans release-group tags, resolutions, codecs and episode
markers, then compares titles and release years against:

| Provider | Setup | Used for |
| --- | --- | --- |
| AniList | None | Anime matches, AniList and linked MyAnimeList IDs |
| MyAnimeList API v2 | Your own `MAL_CLIENT_ID` | Fallback anime matches |
| OMDb | `OMDB_API_KEY` | Movies/series, IMDb IDs, and available ratings including Rotten Tomatoes |

Add optional credentials as environment variables or Hugging Face Secrets and
restart the bot. Get a MyAnimeList application client ID at
<https://myanimelist.net/apiconfig> and an OMDb key at
<https://www.omdbapi.com/apikey.aspx>. No credentials are bundled.

AniList anime films stay in **Anime**. OMDb movies use **Movies**; series and
episodes use **Web series**. Without an OMDb key, non-anime movie/series
classification is unavailable. Non-video files, low-confidence results,
conflicting anime/live-action matches, failed provider calls and rate-limited
lookups remain under **Direct files**; the bot does not guess a movie category
from the channel ID. Add credentials or wait for the provider to recover, then
run `rescan`. Explicit `/link` categories remain an override for individual links.

The per-scan title cache is bounded to 512 entries and reuses episode lookups.
AniList requests are paced to one every 2.1 seconds; provider cooldowns are
respected. Provider failures do not publish credentials or stop an otherwise
valid channel scan. Cleaned media titles and release years are sent to the
configured providers; channel IDs and media bytes are not sent to them.

IMDb IDs and Rotten Tomatoes ratings come **through OMDb when available**.
GateStream does not claim direct access to IMDb or Rotten Tomatoes' licensed
APIs, and does not scrape their websites. Rotten Tomatoes integration has a
separate licensing process. Metadata matching is conservative, not infallible.

References:
- <https://core.telegram.org/method/messages.getHistory>
- <https://docs.anilist.co/guide/graphql/queries/media>
- <https://myanimelist.net/apiconfig/references/api/v2>
- <https://www.omdbapi.com/>
- <https://www.rottentomatoes.com/help_desk/licensing>
