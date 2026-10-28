# Deploy the GateStream Worker with GitHub Actions

The Worker forwards requests to your existing GateStream server. Your bot, Telegram
connections and database continue running on Hugging Face Spaces or your VPS.
The Worker passes response bodies through as streams and preserves byte-range
responses for seeking. It does not implement a distributed video cache or
guarantee higher download speeds.

## 1. Configure Cloudflare

In the Cloudflare dashboard, select the account that will own the Worker.
Complete the Workers onboarding and select a workers.dev subdomain if this is
your first Worker. Copy the account ID from the account's dashboard.

Create an API token using the **Edit Cloudflare Workers** template, scoped to
that account. This deploy uses workers.dev, so it does not require a custom DNS
zone. Keep the token private.

## 2. Configure GitHub

Upload the files to your repository, including the `.github` directory.
Under **Settings → Secrets and variables → Actions**, add:

| Type | Name | Value |
| --- | --- | --- |
| Repository secret | `CLOUDFLARE_API_TOKEN` | The Cloudflare API token |
| Repository secret | `CLOUDFLARE_ACCOUNT_ID` | Your Cloudflare account ID |
| Repository variable or secret | `CLOUDFLARE_ORIGIN_URL` | Your actual server address, e.g. `https://YOUR-SPACE.hf.space` |
| Repository variable (optional) | `CLOUDFLARE_WORKER_NAME` | Defaults to `ftl-stream-proxy`; set `stream-proxy` to update an existing Worker with that name in your account |

`CLOUDFLARE_ORIGIN_URL` must be a bare HTTPS origin, without credentials, a path,
query string or fragment. For Spaces, use the app's `.hf.space` address, not its
`huggingface.co/spaces/...` repository page. The origin must be publicly
accessible and must not route back to the Worker.

Enter the URL without quotation marks. The workflow accepts a repository
variable or repository secret with this name; a nonempty variable takes
precedence if both exist. For this deployment, use
`https://siamreal-hfvps.hf.space`. If the workflow reports a missing setting,
check this exact name under the GitHub repository's Actions settings and rerun
the workflow after saving it. A setting on the Hugging Face host alone is not
available to GitHub Actions.

Deploying with an existing Worker name replaces that Worker's code and manages
its variables from this workflow. Use the default name to create a separate
Worker if you want to keep an existing deployment.

## 3. Deploy

Open **Actions → Deploy GateStream Worker → Run workflow**. The workflow tests
the proxy, builds the deployment configuration, and deploys through Wrangler.
The run summary shows the deployed Worker URL. You must use the subdomain of
your own Cloudflare account; your friend's URL is not automatically yours.

Changes under `cloudflare/` or to the workflow on `main` deploy automatically.
After changing repository variables, run the workflow manually again.
If your default branch is not `main`, adjust the workflow's `push.branches`.

## 4. Connect the bot

Set this environment variable on the **bot host**, using your actual deployment URL:

```dotenv
BASE_URL=https://ftl-stream-proxy.YOUR-SUBDOMAIN.workers.dev
```

For Spaces, add `BASE_URL` in the Space settings and restart the Space. The
workflow does not update your hosting settings. Keep `CLOUDFLARE_ORIGIN_URL`
pointing to the direct host address; do not change it to `BASE_URL`.

Visit `https://YOUR-WORKER.workers.dev/health`; the bot should return `OK`.
Generate a fresh bot link and check playback, seeking, and downloading.
To check ranges with a real file code:

```bash
curl -sS -D - -o /dev/null -H 'Range: bytes=0-1023' 'https://YOUR-WORKER.workers.dev/dl/YOUR-FILE-CODE'
```

Expect HTTP `206` and `Content-Range: bytes 0-1023/TOTAL` for a file at least
1024 bytes long. A `503` indicates invalid/self-referencing Worker origin
configuration; a `502` indicates an upstream network failure. Other statuses
are passed through from your hosting server.

## Local check

No Node dependencies are needed to run the proxy tests:

```bash
node --test cloudflare/worker.test.mjs
```

Deployment references:
- https://github.com/cloudflare/wrangler-action
- https://developers.cloudflare.com/workers/wrangler/configuration/
- https://developers.cloudflare.com/workers/runtime-apis/streams/

## GateStream deployment identity

The default Worker name remains `ftl-stream-proxy` to update existing deployments
without changing public URLs. For a new branded endpoint, optionally set
`CLOUDFLARE_WORKER_NAME` to `gatestream-proxy`, deploy, then use the returned URL
as your bot host’s `BASE_URL`.
