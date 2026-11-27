package main

const pageTemplates = `
{{define "head"}}<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <title>GateStream</title>
    <style>
      :root {
        color-scheme: dark;
        font-family: system-ui, sans-serif;
        color: #f2efe7;
        background: #10100e;
        --gold: #d5ae60;
        --line: #3c382b;
      }
      * {
        box-sizing: border-box;
      }
      body {
        margin: 0;
      }
      main {
        max-width: 1050px;
        margin: auto;
        padding: 35px 24px;
      }
      header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 55px;
      }
      a {
        color: var(--gold);
        text-underline-offset: 5px;
      }
      header a {
        font-weight: 700;
        text-decoration: none;
      }
      h1 {
        font-size: clamp(32px, 6vw, 58px);
        letter-spacing: -0.04em;
        margin: 12px 0;
      }
      h2 {
        font-size: 20px;
      }
      p {
        line-height: 1.7;
        color: #bbb6a7;
      }
      button,
      .button {
        border: 0;
        border-radius: 9px;
        background: var(--gold);
        color: #19150b;
        padding: 13px 20px;
        font: inherit;
        font-weight: 600;
        cursor: pointer;
        text-decoration: none;
        display: inline-block;
      }
      button.secondary {
        background: transparent;
        color: var(--gold);
        border: 1px solid var(--line);
      }
      .actions {
        display: flex;
        align-items: center;
        gap: 20px;
        flex-wrap: wrap;
        margin: 26px 0;
      }
      .card {
        border: 1px solid var(--line);
        background: #171610;
        border-radius: 13px;
        padding: 22px;
        margin: 15px 0;
      }
      .grid {
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 15px;
      }
      .grid .card {
        margin: 0;
      }
      .number {
        display: block;
        color: var(--gold);
        font-size: 32px;
        font-weight: 700;
        margin-bottom: 8px;
      }
      small {
        color: #a8a18f;
        font-size: 12px;
      }
      input {
        width: 100%;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 13px;
        background: #10100e;
        color: inherit;
        font: inherit;
        margin: 8px 0 18px;
      }
      label {
        display: block;
        font-size: 14px;
      }
      form.search {
        display: flex;
        gap: 12px;
        align-items: baseline;
      }
      form.search input {
        flex: 1;
      }
      progress {
        width: 100%;
        height: 7px;
        accent-color: var(--gold);
        margin: 15px 0;
      }
      .row {
        display: flex;
        gap: 12px;
        align-items: center;
        justify-content: space-between;
      }
      .filename {
        overflow-wrap: anywhere;
        margin: 0 0 8px;
      }
      .muted {
        color: #aba596;
      }
      .notice {
        border-left: 3px solid var(--gold);
        padding-left: 15px;
      }
      footer {
        margin-top: 50px;
        color: #a8a18f;
        font-size: 12px;
      }
      .signin {
        max-width: 520px;
        margin: 70px auto;
      }
      .signup {
        max-width: 520px;
      }
      .error {
        color: #edb0a1;
      }
      video {
        width: 100%;
        max-height: 70vh;
        background: #000;
        border-radius: 12px;
      }
      code {
        overflow-wrap: anywhere;
      }
      button:disabled {
        opacity: 0.5;
        cursor: default;
      }
      @media (max-width: 600px) {
        main {
          padding: 24px 18px;
        }
        header {
          margin-bottom: 35px;
        }
        .card {
          padding: 16px;
        }
        .row {
          flex-wrap: wrap;
        }
        .grid {
          gap: 10px;
        }
        .actions {
          gap: 15px;
        }
      }
    </style>
  </head>
  <body>
    <main>
      <header><a href="/">GateStream</a><a href="/index">Library</a></header>
      {{end}} {{define "foot"}}
      <footer>GateStream 3.0.0</footer>
    </main>
  </body>
</html>
{{end}} {{define "home"}}{{template "head" .}}
<h1>Your media <span style="color: var(--gold)">library.</span></h1>
<p>Open your saved files, stream online, or download directly.</p>
<div class="actions">
  <a class="button" href="https://t.me/{{.Username}}"
    >Open Telegram bot ↗</a
  ><a href="/index">Browse library ↗</a><a href="/health">Server health</a>
</div>
<h2>Transfers</h2>
<div class="grid">
  <div class="card">
    <strong class="number" id="files">—</strong>Single files downloaded
  </div>
  <div class="card">
    <strong class="number" id="sent">—</strong>Data delivered
  </div>
</div>
<p><small id="metrics">Connecting…</small></p>
<div id="jobs"></div>
<p><small id="saved"></small></p>
<script>
  const owner = {{.Owner}},
    csrf = {{.CSRF}};
  const bytes = (n) => {
    let units = ["B", "KiB", "MiB", "GiB", "TiB"],
      i = 0;
    while (n >= 1024 && i < 4) {
      n /= 1024;
      i++;
    }
    return n.toFixed(1) + " " + units[i];
  };
  async function update() {
    try {
      let response = await fetch("/api/activity", { cache: "no-store" });
      if (!response.ok) throw new Error();
      let s = await response.json(),
        m = s.phases.media;
      document.querySelector("#files").textContent = m.single_downloads;
      document.querySelector("#sent").textContent = bytes(m.bytes);
      document.querySelector("#metrics").textContent =
        m.active +
        " active connections · " +
        bytes(m.speed_bps) +
        "/s · Uptime " +
        Math.floor(s.uptime_seconds / 60) +
        "m";
      document.querySelector("#saved").textContent =
        "Totals storage " +
        s.persistence.state +
        ". Live activity resets after restart.";
      let root = document.querySelector("#jobs");
      root.replaceChildren();
      for (let job of s.transfers || []) {
        let box = document.createElement("section");
        box.className = "card";
        let title = document.createElement("strong");
        title.textContent =
          "File download #" + job.id + " · " + job.percent.toFixed(1) + "%";
        let bar = document.createElement("progress");
        bar.max = 100;
        bar.value = job.percent;
        let detail = document.createElement("small");
        detail.textContent =
          bytes(job.bytes) +
          " / " +
          bytes(job.total_bytes) +
          " · " +
          bytes(job.speed_bps) +
          "/s";
        box.append(title, bar, detail);
        if (owner) {
          let stop = document.createElement("button");
          stop.className = "secondary";
          stop.textContent = "Stop";
          stop.style.marginLeft = "20px";
          stop.onclick = async () => {
            stop.disabled = true;
            try {
              let result = await fetch("/api/transfers/" + job.id + "/stop", {
                method: "POST",
                body: new URLSearchParams({ csrf }),
              });
              if (result.ok) await update();
              else stop.disabled = false;
            } catch {
              stop.disabled = false;
            }
          };
          box.append(stop);
        }
        root.append(box);
      }
    } catch {
      document.querySelector("#metrics").textContent =
        "Live statistics unavailable. Retrying…";
    }
  }
  update();
  setInterval(update, 2000);
</script>
{{template "foot" .}}{{end}}
{{define "library"}}{{template "head" .}}
<h1>Your library</h1>
{{if .Signed}}
<div class="row">
  <p>{{.Total}} saved files</p>
  <form method="post" action="/index/sign-out">
    <input type="hidden" name="csrf" value="{{.CSRF}}" /><button
      class="secondary"
    >
      Sign out
    </button>
  </form>
</div>
<form class="search" method="get" action="/index">
  <input
    type="search"
    name="q"
    value="{{.Query}}"
    placeholder="Search your files"
    aria-label="Search files"
  /><button>Search</button>
</form>
{{range .Items}}
<article class="card row">
  <div>
    <h2 class="filename">{{index . "file_name"}}</h2>
    <small>{{size (index . "file_size")}}</small>
  </div>
  <div class="actions">
    <a href="/stream/{{index . "code"}}">Open</a
    ><a class="button" href="/dl/{{index . "code"}}">Download ↓</a>
  </div>
</article>
{{else}}
<p>No files found. Send a file to the bot to add it.</p>
{{end}}
<nav class="actions">
  <a href="/index?page={{.Prev}}&amp;q={{.QueryURL}}"
    >Previous</a
  ><span>Page {{.Page}} of {{.Pages}}</span
  ><a href="/index?page={{.Next}}&amp;q={{.QueryURL}}"
    >Next</a
  >
</nav>
{{else}}
<p>Sign in through the bot to view your saved files.</p>
<a class="button" href="https://t.me/{{.Username}}?start=library"
  >Sign in with Telegram ↗</a
>{{end}}{{template "foot" .}}{{end}}
{{define "signin"}}{{template "head" .}}
<section class="signin">
  <h1>Sign in</h1>
  <p>
    Continue to {{if .Setup}}owner worker
    setup{{else}}your personal library{{end}}.
  </p>
  <form method="post" id="signin">
    <input type="hidden" name="csrf" value="{{.CSRF}}" /><input
      type="hidden"
      name="token"
      id="token"
    /><button id="continue" disabled>Continue</button>
  </form>
  <p id="error"></p>
</section>
<script>
  const token = location.hash.slice(1);
  history.replaceState(null, "", location.pathname + location.search);
  if (/^[A-Za-z0-9_-]{43}$/.test(token)) {
    document.querySelector("#token").value = token;
    document.querySelector("#continue").disabled = false;
  } else
    document.querySelector("#error").textContent =
      "Open a fresh sign-in link from the bot.";
</script>
{{template "foot" .}}{{end}}
{{define "setup"}}{{template "head" .}}
<section class="signup">
  <h1>Worker repair</h1>
  <p>{{.Message}}</p>
  {{if eq .Step "done"}}<a class="button" href="/">Return home</a
  >{{else}}
  <form method="post">
    <input type="hidden" name="csrf" value="{{.CSRF}}" /><input
      type="hidden"
      name="action"
      value="{{.Step}}"
    />{{if eq .Step "start"}}
    <p>
      Connect the Telegram account matching OWNER_ID. The session is stored
      encrypted for worker repair.
    </p>
    <label
      >API ID<input
        name="api_id"
        inputmode="numeric"
        autocomplete="off"
        required /></label
    ><label
      >API hash<input
        name="api_hash"
        type="password"
        autocomplete="off"
        required /></label
    ><label
      >Phone number<input
        name="phone"
        type="tel"
        autocomplete="off"
        placeholder="+880…"
        required /></label
    >{{else if eq .Step "code"}}<label
      >Telegram login code<input
        name="code"
        inputmode="numeric"
        autocomplete="one-time-code"
        required /></label
    >{{else}}<label
      >Two-step verification password<input
        name="password"
        type="password"
        autocomplete="off"
        required /></label
    >{{end}}<button>Continue</button>
  </form>
  <form method="post" style="margin-top: 15px">
    <input type="hidden" name="csrf" value="{{.CSRF}}" /><input
      type="hidden"
      name="action"
      value="cancel"
    /><button class="secondary">Cancel setup</button>
  </form>
  {{end}}
</section>
{{template "foot" .}}{{end}}
{{define "player"}}{{template "head" .}}
<h1 style="font-size: 28px">{{.Name}}</h1>
<video
  controls
  playsinline
  preload="metadata"
  src="/dl/{{.Code}}?inline=1"
></video>
<div class="actions">
  <a class="button" href="/dl/{{.Code}}">Download ↓</a
  ><button
    class="secondary"
    onclick="navigator.clipboard.writeText(document.querySelector('#url').textContent).then(()=>this.textContent='Copied')"
  >
    Copy media link
  </button>
</div>
<code id="url"
  >{{.Base}}/dl/{{.Code}}?inline=1</code
>{{template "foot" .}}{{end}}
`
