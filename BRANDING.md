# GateStream identity

**GateStream** · **Gateway to your media.**

A gateway and a play symbol form the mark. Warm gold (`#D6AD60`), pale gold
(`#F1D89C`), and charcoal (`#10100E`) keep the interface quiet around your media.
The name takes inspiration from a gate opening into a personal collection;
the artwork is an original geometric mark.

The release updates the landing page, browser player, Telegram welcome/help and
privacy messages, system messages, backup filenames, package names, and guides.
The logo source is `bot/server/templates/brand.svg`.

## Telegram profile

Source code cannot rename an existing bot's public BotFather profile. In
**@BotFather**, select your bot and apply these values:

| BotFather command | Suggested value |
| --- | --- |
| `/setname` | GateStream |
| `/setabouttext` | Gateway to your media. Stream and download your Telegram library. |
| `/setdescription` | Welcome to GateStream. Index media from your Telegram channels, find it with search, and create browser playback and download links. Open a gate. Press play. |

Keep the existing username and token to preserve current Telegram links.
Changing a bot username, repository name, Space slug, or domain is a separate
account action; this release does not rename those external resources.

## Compatibility

The storage names and deployed Worker name are preserved. They identify existing
data or URLs and are not part of the visible GateStream identity. See the
README's upgrade section before changing them.
