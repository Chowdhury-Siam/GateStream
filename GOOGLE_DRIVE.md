# Google Drive mirror, clone and index

For in-bot setup, send **`/dset`** as the owner. Set the folder/index, upload
your own OAuth `token.json` under **Private Files**, check the connection, and
enable transfers. See [BOT_SETTINGS.md](BOT_SETTINGS.md). Existing host token
settings remain an alternative.

Drive transfers remain optional, **disabled by default**, and limited to the
owner/admins in private Telegram chats. Existing token settings, destination
folder and limits are preserved.

| Command | What it does |
| --- | --- |
| `/mirror <direct-file-url>` | Downloads a public HTTP file to temporary disk, uploads it to Drive, then removes the temporary copy |
| `/clone <drive-share-link-or-id>` | Copies a Drive file or an entire folder tree using Google's server-side API; no media download to the bot |
| `/drive <url>` or `/gdrive <url>` | Existing aliases; Google share URLs automatically use clone, other URLs use mirror |
| `/drivestatus` | Shows the current job stage and progress |
| `/canceldrive` | Requests cancellation after the current network operation |

Only one Drive job runs at a time. Telegram progress updates every three seconds
when the stage or transferred amount changes. Other admins can check or cancel
the running job. Restarting the host interrupts the job; there is no persistent
job queue or automatic job recovery.

## Hugging Face compatibility, checked 2026-10-30

Spaces allow outbound HTTP/HTTPS on ports 80, 443 and 8080. Google Drive API and
OAuth endpoints use HTTPS/443, so these API operations are technically compatible.
Folder clones keep media within Google Drive, avoiding temporary media storage
on the Space. Mirrors need sufficient temporary disk for one complete file.
Spaces disk is ephemeral and free hardware can sleep; an interrupted transfer
cannot be promised to continue after a restart.

These technical capabilities **do not establish permission to run a general
bulk mirror service**. Hugging Face's policy restricts unauthorized bot APIs,
excessive bulk activity, irrelevant repository data and bypassing restrictions.
Check with Hugging Face before enabling this workload on a Space. The feature
stays opt-in; GateStream does not bypass network limits or hosting restrictions.

Sources:
- https://huggingface.co/docs/hub/spaces-overview#networking
- https://huggingface.co/docs/hub/spaces-storage
- https://huggingface.co/content-policy

## Credentials and destination

Keep using your existing trusted host OAuth `token.pickle`, or upload an
authorized-user `token.json` through the owner settings panel. It must contain a
`google.oauth2.credentials.Credentials` object with an offline refresh token.
Enable the Google Drive API in your own Google Cloud project. `drive.file`
access is limited to files authorized for that app; cloning arbitrary accessible
existing files/folders generally needs the `https://www.googleapis.com/auth/drive`
scope. The account must be allowed to read/copy each source and create files
in the destination. Copy restrictions and quota still apply.

A pickle can execute code. Generate it yourself; never accept one from a bot
user, an unknown website or an untrusted attachment. Service-account JSON,
client-secrets JSON and old `oauth2client` pickles are not accepted by the host
pickle loader. The settings panel only accepts OAuth authorized-user JSON. No browser login is performed on the server. Expired/revoked OAuth
refresh tokens must be regenerated locally.

On a permitted host, configure:

```dotenv
DRIVE_ENABLED=true
DRIVE_TOKEN_PATH=/run/secrets/token.pickle
DRIVE_FOLDER_ID=your_destination_folder_id
DRIVE_MAX_MB=1024
DRIVE_CLONE_MAX_FILES=1000
DRIVE_INDEX_URL=https://your-index.example/0:/Mirror/
```

`DRIVE_FOLDER_ID` is an ID, not a URL. Blank keeps the existing My Drive root
behavior. Accessible shared-drive folders are supported. `DRIVE_MAX_MB` limits
each mirrored file. `DRIVE_CLONE_MAX_FILES` counts files **and folders**, including
the cloned root, in one preflight snapshot. Clone byte sizes are not charged
against the mirror's local-disk limit, because clone does not stage media.

Mount the secret token at runtime; it is deliberately excluded from Git and the
Docker build context. If the approved host only supports environment secrets,
encode your trusted token locally:

```bash
python -c "import base64,pathlib; print(base64.b64encode(pathlib.Path('token.pickle').read_bytes()).decode())"
```

Store that value in the host **Secret** `DRIVE_TOKEN_PICKLE_B64`. It takes
precedence over `DRIVE_TOKEN_PATH`. Base64 is not encryption: protect it like
the original token. Do not put the base64 pickle in public variables, source, logs, or Telegram.
The settings panel uses a separate JSON-only credential flow.
Existing host secrets are not changed. Uploaded JSON credentials take precedence
until removed in /bset. Drive settings saved in the bot override the environment
for those editable values until reset to host defaults.

## Connect BhadooGDIndex

Deploy/configure your own index separately using
https://github.com/rokibhasansagar/BhadooGDIndex. Use your own Google credentials
and an index account that can read the bot's destination. GateStream does not
install another Worker or share its Google token with the index.

`DRIVE_INDEX_URL` is the **full browser folder URL for the exact destination**:

- If `DRIVE_FOLDER_ID` is the index's root folder: `https://index.example/0:/`.
- If it is a folder named `Mirror` under that root: `https://index.example/0:/Mirror/`.
- For a second indexed drive, use its actual number, such as `/1:/Mirror/`.

GateStream appends the uploaded/cloned name with UTF-8 URL encoding. Files get a
**Download from index** button; folder clones get a **Browse folder** button.
Leave the setting blank to return Google Drive buttons only. Google Drive links
are always provided. A Google account or the index's own login may be required.
GateStream's Link password does **not** protect an independently hosted index.

Drive filenames containing slashes cannot be represented reliably as Bhadoo
paths, so those results get only the Google Drive button. Bhadoo resolves by
name: duplicate names in the same destination can be ambiguous. Choose a dedicated
destination and resolve duplicates in Drive if an index opens the wrong item.
Google-native documents may need to be opened/exported through Drive instead
of downloaded as binary files from the index.

The bot does not call the permissions API or make source/destination files
public. New files inherit the destination's existing sharing. The index uses
its own authorized Google access and access controls.

## Reliability and limits

Mirrors retain the previous protections: public direct HTTP/HTTPS URLs only;
each redirect and DNS resolution is checked against private/local addresses;
signed query strings are preserved; HTML/login/player pages are rejected;
declared and actual sizes are limited; at least 64 MiB of disk reserve is kept.
Downloads use 1 MiB disk writes and uploads use resumable 8 MiB chunks with
bounded retries. OAuth is never forwarded to the source HTTP server.

Clones read all pages of a bounded folder snapshot before creating anything.
Shortcuts, incomplete listings and files Google explicitly marks uncopyable
are rejected before writes. Link resource keys are supported. Original media
and existing destination items are never deleted or moved. Newly created files
and folders inherit the destination's permissions; folder structure and names
are copied, not source sharing rules.

On a failed/cancelled clone, the bot attempts to trash **only its newly created
root copy**, which also contains that job's copied children. Cleanup failure is
reported. Copy/create requests are not blindly retried, because a timed-out
write may already have succeeded. A hard kill, connection failure during a
write, or lost API response can leave a partial/uncertain result: check the
destination before retrying. Sources can change after the preflight snapshot;
this is a copy job, not a transactional backup.

`/canceldrive` cooperates between chunks and API calls. It does not forcibly kill
a thread during a Drive write. Temporary files stay available until a running
upload thread exits, then are removed. Cancellation may wait for the current
network operation. Completed files are not automatically deleted by a late
cancel request.

Mirrors and clones return Drive/index links; they do not add Drive objects to
the Telegram-backed catalog. Users browse them through the configured index.
The home page shows actual mirror download/upload bytes. Server-side clone
operations do not pretend to transfer media through the host.

## Verification

Run `python -m unittest discover -s tests -v`. Tests mock Google and HTTP:
credentials, SSRF defenses, disk limits, resumable upload metadata, paginated
folder copies, resource keys, copy restrictions, cancellation, cleanup, encoded
index links and admin command behavior are checked. Live OAuth, Drive quota,
permissions and your particular index deployment require real-account testing.

Google API references:
- https://developers.google.com/workspace/drive/api/reference/rest/v3/files/copy
- https://developers.google.com/workspace/drive/api/guides/folder
- https://developers.google.com/workspace/drive/api/guides/resource-keys
- https://developers.google.com/workspace/drive/api/guides/manage-uploads
