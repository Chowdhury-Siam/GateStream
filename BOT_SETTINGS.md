# Bot settings and Drive setup

Send **`/bset`** (or `/botsettings`) to your bot in private chat as its owner.
`/dset` and `/driveset` open Google Drive setup directly. `/cancelsettings`
cancels a pending input and returns to the menu.

The menu uses stacked Telegram buttons, like a mirror bot settings panel:

- **Config Variables** — editable runtime options, with hidden secret values.
- **Module Settings** — Drive, ZIP cache budget, metadata keys, Private links
  password, and the optional private-upload storage channel.
- **Google Drive** — enable/disable, destination folder, index URL, mirror size
  limit, clone item limit, token setup, connection check and command help.
- **Private Files** — upload/remove a Google OAuth `token.json`.
- **Status** — credential source, Drive state, ZIP budget and current Drive job.
- **Close** — closes the panel and cancels pending input.

Telegram controls the button colours, fonts and chat theme. GateStream supplies
the layout and working actions. Unsupported qBittorrent/aria2/SABnzbd engines
are not shown as empty settings pages.

## Set up Drive entirely in the bot

1. Open `/dset` → **Set Drive token** → **Upload Drive token.json**.
2. Reply **directly to the bot's prompt** with your own OAuth authorized-user
   file, named **`token.json`**, as a document (maximum 64 KiB).
3. Open **Destination folder** → **Edit value**. Reply with its Drive folder ID
   or folder URL. Enter `root` for My Drive. The authorized account must be able
   to create files in it. A destination protected by a resource key should first
   be shared directly with the authorized Google account.
4. Optional: set **Drive index URL** to the full HTTPS Bhadoo folder URL for
   that exact destination, such as `https://index.example/0:/Mirror/`.
5. Select **Check Drive connection**. This reads folder metadata/capabilities;
   it does not create a test file or change sharing. Transfer scopes and quotas
   still apply, so a successful check is not a completed upload/clone test.
6. Turn on **Drive transfers**, then use `/mirror <direct-url>` or
   `/clone <drive-file-or-folder-link>`.

The token must include `client_id`, `client_secret` and `refresh_token`, and come
from your own Google OAuth authorization. Client-secrets JSON and service-account
keys are different formats and are rejected. Drive API access/scopes are still
required; see [GOOGLE_DRIVE.md](GOOGLE_DRIVE.md).

Existing trusted host `DRIVE_TOKEN_PATH` / `DRIVE_TOKEN_PICKLE_B64` settings keep
working. An uploaded JSON token takes precedence. Removing it restores the host
credential source if one is configured; it does not delete the host token or
revoke Google authorization. Missing host credentials then produce the existing
missing-token error. No token is uploaded automatically or enabled by opening
settings.

### If you already have a trusted token.pickle

Convert **only your own trusted pickle**, on your own computer, without sending
it to the bot. In a Python environment with the matching Google credential
library installed:

```python
import pickle
from pathlib import Path
from google.oauth2.credentials import Credentials

with open('token.pickle', 'rb') as source:
    credentials = pickle.load(source)  # Trusted local file only: pickle can run code.
if not isinstance(credentials, Credentials):
    raise TypeError('Use a google.oauth2.credentials.Credentials token')
Path('token.json').write_text(credentials.to_json(), encoding='utf-8')
```

Keep the resulting JSON private. The bot never unpickles a chat attachment.

## Editing and persistence

The editable allowlist is:

| Setting | Meaning |
| --- | --- |
| `DRIVE_ENABLED` | Enable/disable Drive mirror and clone jobs |
| `DRIVE_FOLDER_ID` | Drive destination; blank/root means My Drive |
| `DRIVE_INDEX_URL` | Optional HTTPS Bhadoo folder URL |
| `DRIVE_MAX_MB` | Mirror file limit in MiB |
| `DRIVE_CLONE_MAX_FILES` | Clone snapshot item limit, including folders/root |
| `ZIP_CACHE_MAX_MB` | Shared ZIP disk budget in MiB |
| `TELEGRAM_CHANNEL_ID` | Optional storage channel for normal private media uploads; 0 disables |
| `MAL_CLIENT_ID`, `OMDB_API_KEY` | Optional metadata provider keys; displayed only as configured/not set |

Choose a value, then **Edit value** and reply to the prompt. **Clear value**
removes an optional value. **Use host default** removes its saved override and
restores the environment value captured at startup. While replying, `/default`
restores the host value; `/clear` clears a clearable field. Prompts expire after
10 minutes. Unrelated commands continue working.

Changes are written to the **existing MongoDB database**, `settings` collection,
document `bot_runtime`, and applied only after a successful write. Startup loads
them before Telegram accepts commands. No database name, database URI, bot token,
owner ID, worker tokens, host file, or HF README metadata is rewritten. Core
connection/identity settings remain host-managed. Saved overrides take precedence
until cleared/reset; changing the host environment alone does not remove one.

The existing `index_access` password document is preserved. The menu's Private
links password action calls the same hashed password setter as `/setpassword`;
changing it signs out old sessions and invalidates protected ZIP links.

Drive settings/token changes are blocked during an active Drive job: wait for
completion or use `/canceldrive` first. ZIP budget changes affect future capacity
checks; existing archives/builds are not deleted. Metadata key changes affect
subsequent provider requests; re-scan titles to retry earlier cached matches.

## Private values

Only the configured **owner** can view/edit these settings. Authorized admins
can still use the existing transfer commands, but cannot change owner settings.
Group chats, forwarded panels and other users cannot use the settings actions.

The uploaded refresh-token JSON and metadata API keys are encrypted with Fernet
before storage. The encryption key derives from the existing high-entropy bot
token with a GateStream-specific domain. Keep the bot token secret and stable:
rotating it makes these private stored values unreadable, requiring re-entry.
Other runtime values still restore. An unreadable uploaded Drive token disables
Drive at startup until the owner uploads a replacement or removes that saved
credential source. Never expose the bot token alongside database backups.

No private file is copied to the bin channel, media index, ZIP cache, logs, or
normal backup export. Common token/credential filenames are also rejected by
the regular media-upload handler outside settings. Credential input messages
and prompts are deleted on a best-effort basis, and secret values are never
echoed. Keep your own token copy: this menu does not export private files.
Telegram bot chats are not end-to-end encrypted; host secret configuration
remains available if you prefer not to send credentials through Telegram.

Hugging Face workload restrictions remain the same as documented in
[GOOGLE_DRIVE.md](GOOGLE_DRIVE.md). Opening this menu does not enable Drive or
establish hosting permission. No torrent/download engines have been added.

Tests cover restart recovery, failed writes, input validation, masked/encrypted
secrets, token rotation, private owner checks, reply binding, expiration, private
file size/type limits, Telegram keyboard serialization, Drive job locking and
read-only connection checks. Live Telegram and Google account operations still
need deployment testing.
