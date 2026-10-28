# Direct URL to Google Drive

GateStream includes an optional, administrator-only transfer command:

```text
/drive https://files.example.com/video.mp4
```

`/gdrive` is an alias. Send the command in a private chat with the bot. The bot
checks its configured credentials, downloads the file to temporary disk,
uploads it with the Google Drive API, removes its local copy, and replies with
an **Open in Google Drive** button. It does not make files public or change
sharing permissions; existing destination-folder permissions still apply.

## Hosting permission

The feature is **disabled by default**. Enable it only on hosting that permits
this file-transfer workload. Hugging Face documents HTTP/HTTPS networking and
ephemeral disk, but those technical capabilities do not explicitly approve a
general file-transfer bot. If using Spaces, obtain confirmation from Hugging
Face before enabling it. Adding this feature cannot unpause a Space that is
flagged or otherwise restricted. Do not use a proxy to bypass hosting restrictions.

References checked for this feature:
- https://huggingface.co/docs/hub/spaces-overview#networking
- https://huggingface.co/docs/hub/spaces-storage
- https://huggingface.co/content-policy

## Credentials

Use a **trusted token.pickle that you created yourself**. It must contain a
`google.oauth2.credentials.Credentials` object authorized for Drive uploads,
usually with the `drive.file` or `drive` OAuth scope and an offline refresh token.
Enable the Google Drive API in the token's Google Cloud project. Older
`oauth2client` pickles, service-account JSON, and client-secrets JSON are not
accepted by this loader. If your token uses another format, generate a compatible
token locally; do not rename a JSON file to `.pickle`.

Pickle files can execute code when loaded. Only the deployment owner should
supply this file. Never download an untrusted token or accept one through the
bot. OAuth credentials grant access to the account: do not commit them, send
them in Telegram, or paste them into public logs. Credential filenames are
excluded from Git and Docker build contexts.

No browser login is performed on the server. The Google client handles access
token refresh during API requests. A revoked/invalid refresh token requires new
credentials. The token file is not overwritten, so read-only secret mounts work.

## Local machine or VPS

Install the updated `requirements.txt`, put your trusted token outside the
repository if possible, and configure the host environment or `.env`:

```dotenv
DRIVE_ENABLED=true
DRIVE_TOKEN_PATH=/absolute/path/to/token.pickle
DRIVE_FOLDER_ID=
DRIVE_MAX_MB=1024
```

`DRIVE_FOLDER_ID` is optional. Leave it blank to upload to the authorized user's
My Drive root, or enter the destination folder's **ID**, not its URL. The token
must be able to create files there. A `drive.file` token only accesses files and
folders authorized for that OAuth app; an arbitrary existing folder may require
separate authorization. Accessible shared-drive folders are also supported.

Restart the bot and send `/drive <direct-file-url>` as the owner or an admin
listed in `AUTH_USERS`.

### Docker secret file

The image deliberately excludes `*.pickle`. Mount the token at runtime rather
than baking it into the image. For example, add these options to your existing
Docker invocation, using an absolute local path:

```text
--mount type=bind,source=/absolute/path/token.pickle,target=/run/secrets/token.pickle,readonly
-e DRIVE_ENABLED=true
-e DRIVE_TOKEN_PATH=/run/secrets/token.pickle
```

The container's user must be able to read the mounted file.

## Hosts without secret file mounts

On hosting that permits this workload but only provides environment secrets,
encode the trusted token locally:

```bash
python -c "import base64,pathlib; print(base64.b64encode(pathlib.Path('token.pickle').read_bytes()).decode())"
```

Paste the output into a host **Secret** called `DRIVE_TOKEN_PICKLE_B64`, then set
`DRIVE_ENABLED=true`. Base64 is an encoding, not encryption. Treat the output
like the original token. This secret takes precedence over `DRIVE_TOKEN_PATH`.
Never put it in repository files or an ordinary public configuration variable.

For a Hugging Face deployment, do this only after the workload is approved and
the Space is allowed to run. Use **Space Settings → Variables and secrets**;
GitHub Actions secrets do not automatically reach the bot's host. Your Cloudflare
Worker is not involved in the Google Drive upload and needs no OAuth token.

## Limits and behavior

- Only bot administrators can use the command, in private chat.
- One transfer runs at a time. Additional requests receive a busy message.
- The default limit is **1024 MiB** per file; `DRIVE_MAX_MB` changes it. Both
  declared file sizes and actual downloaded bytes are checked.
- Downloads stream to disk in 1 MiB chunks. At least 64 MiB of disk reserve is
  kept; Google uploads use resumable 8 MiB chunks with bounded retries.
- URLs must be public HTTP/HTTPS file endpoints on ports 80, 443, or 8080.
  Each redirect is checked. Private, loopback, link-local, metadata and multicast
  destinations are blocked, including hostnames resolving to those addresses.
- Signed URL query strings are preserved. Cookies, browser login and custom
  authorization headers are not supported. Use a genuine downloadable URL;
  `/stream/...` player pages and login/share pages are rejected when they return HTML.
- The upload keeps the source filename from `Content-Disposition`, or the URL
  path when no name is supplied. Remote names are never used as local paths.
- Normal completion and failures remove temporary files. Process termination or
  a host restart interrupts the job; jobs are not persisted or automatically
  resumed after a restart. Check Drive before retrying an interrupted upload to
  avoid duplicates. The original file is never deleted from its source.
- This command does not add the uploaded file to the Telegram/MongoDB catalog.
  It uploads to Google Drive and returns a Drive link.

## Checks

```bash
python -m unittest discover -s tests -v
```

Tests mock Google and HTTP responses; they cover credentials loading, public-URL
checks, redirects, size/disk limits, upload metadata, permissions, and temporary
file cleanup. No live Google Drive upload is performed by the tests.

Google upload reference:
https://developers.google.com/workspace/drive/api/guides/manage-uploads

## Live progress on the home page

Open the GateStream home page during a transfer to see stage counts, byte totals,
percentage, average speed, elapsed time and ETA. Unknown download sizes show an
indeterminate bar. Drive uploads update when a resumable chunk is acknowledged,
so progress can advance in steps. These anonymous statistics are public and reset
when the server restarts.
