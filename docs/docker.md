# Optional Docker setup

The [native launcher](../README.md#run-it) is the default. Docker runs the app and
its own supervised Chrome, which you use through a password-protected noVNC view.

## Requirements and startup

Use **Docker Engine 28.0.0 or newer**, Compose v2, and an amd64 host. Older Engine
versions allow LAN neighbours on the same L2 network to reach container ports even
when published on `127.0.0.1`; see [Docker's warning](https://docs.docker.com/engine/network/port-publishing/).
Check the host with `docker version --format '{{.Server.Version}}'`. The entrypoint
cannot see the host Engine version and does not mount the Docker socket.
Native arm64 Chrome images are unsupported; amd64 emulation on arm64 is unvalidated.

Copy `.env.example` to `.env`, configure your writer, and prepare `data/` with uid/gid
1000 ownership. See [the Docker setup guide](../docker/README.md) for ownership,
credentials, sandbox troubleshooting, and the host validation checklist.

If `.env` does not exist yet, copy the example:

```sh
cp .env.example .env
```

Set up a writer as described [below](#writer-options). For a new `data/` directory,
a host administrator can use `sudo install -d -o 1000 -g 1000 -m 700 data`. Restore
the same ownership after a backup restore. Build and start from the repository:

```sh
docker compose build --pull --no-cache
docker compose up -d
```

## App and Chrome view

Open the app at `http://127.0.0.1:8000` and Chrome at
`http://127.0.0.1:6080/vnc.html?autoconnect=true`. Read the Chrome view password from
`data/chrome-view.password` on the host. Chrome debugging (9222) and VNC (5900) are
not published. Keep the loopback bindings; the app has no authentication. For
remote access, tunnel both published ports through SSH to the Docker host.

On the welcome page (`/onboarding`), complete **You** with your name and email.
Choose **Open the Chrome view**, enter the noVNC password, and sign in to **Google
and LinkedIn in JobPilot's Chrome**, then refresh the login status in the app.
The control also opens sign-in tabs for missing sessions. Chrome Sync stays off.

The supervisor starts Xvfb, Chrome, x11vnc, noVNC and uvicorn in order and stops
the service if a child exits. The browser is already running when the app becomes
ready; the backend attaches to it. In Docker, **no fallback browser is ever
launched** if that Chrome becomes unavailable.

## Remote access

Access a remote server only through an **SSH tunnel or Tailscale**. Keep the
Compose ports bound to `127.0.0.1`; the app has no authentication. From your own
machine, forward both ports and keep the SSH session open:

```sh
ssh -L 8000:127.0.0.1:8000 -L 6080:127.0.0.1:6080 server
```

Replace `server` with your Docker host (its Tailscale hostname also works). Open
the same local app and Chrome URLs shown above; the password file stays on the
server. Neither CDP nor raw VNC needs forwarding.

For access through Tailscale without SSH forwarding, use a private
[Tailscale Serve proxy](https://tailscale.com/docs/features/tailscale-serve) to the
two loopback services and restrict access with your tailnet policy. Set
`JOBPILOT_CHROME_VIEW_URL` in `.env` to the private Chrome view URL, including
`/vnc.html?autoconnect=true`, and add the private app origin to
`JOBPILOT_ALLOWED_ORIGINS`. Recreate the container after changing those settings.
Keep this within your tailnet; do not enable public Funnel access.

## Chrome security updates

Rebuild with `docker compose build --pull` **at least monthly** for Chrome security
updates, and promptly when a security fix is released. Include `--no-cache` as above
to refresh the apt install even if the base image did not change, then run
`docker compose up -d --force-recreate`. Chrome does not update inside the read-only
running image. The entrypoint warns when the installed Chrome build is **older than
45 days**, based on its package changelog date, and also warns if the date cannot be
read. Rebuilding an old package does not reset this age.

## Writer options

**API writer:** set `LLM_API_KEY` in `.env` with the provider, model and endpoint
settings you need. The default `LLM_PROVIDER=auto` can select those credentials,
or set `LLM_PROVIDER=openai` for an OpenAI-compatible API or
`LLM_PROVIDER=anthropic` for Anthropic. A local model uses `LLM_BASE_URL`; for a
model on the Docker host, use `http://host.docker.internal:<port>/v1`.

**Claude Code / Codex login:** log into the chosen CLI on the host and set
`LLM_PROVIDER=claude` or `LLM_PROVIDER=codex` in `.env`. Explicit selection matters
because the CLI image includes both executables and `auto` prefers Claude.

The default image supports API writers. The optional `docker-compose.cli.yml` selects
the image with Claude Code `2.1.325` and Codex `0.166.0`, pinned in an npm lockfile
and installed as root in a separate build stage. Its only host binds are
`~/.claude/.credentials.json`, `~/.codex/auth.json`, and optional
`~/.codex/config.toml`, read-only with `create_host_path: false`. Remove entries for
unused providers or absent optional files before starting; Compose rejects missing
declared sources without creating host directories. Use regular files rather than
symlinks. Add `-f docker-compose.yml -f docker-compose.cli.yml` to build/up commands.
Startup copies just these files into a tmpfs HOME with mode 600, skipping missing
files and symlinks. Login refreshes affect ephemeral copies and are lost on stop.
The [CLI login instructions](../docker/README.md#optional-cli-logins) cover the
account access these credentials grant and host login renewal.

After removing unused or missing mounts, build the CLI stage before starting it,
including when switching from an existing default image:

```sh
docker compose -f docker-compose.yml -f docker-compose.cli.yml build --pull --no-cache
docker compose -f docker-compose.yml -f docker-compose.cli.yml up -d
```

Use both `-f` arguments for subsequent builds, updates and recreation too.
**These logins grant the container the powers of the corresponding accounts**,
including authenticated model requests and use of account quota. Read-only host
mounts prevent writes through the mounts; they do not limit what the copied
credentials can authorize. The retained source files must already exist as regular
files readable by uid 1000. Renew logins on the host and recreate the container
when needed; do not create empty files to satisfy missing mounts.

## Security

- **127.0.0.1 only:** Compose publishes app port 8000 and Chrome view port 6080 on
  host loopback. Remote use requires the private access described above.
- **CDP never published:** Chrome debugging (9222) and raw VNC (5900) listen only
  on container loopback.
- **noVNC password:** `data/chrome-view.password` is mode 600 and is never returned
  by the API or included in logs or command arguments. Classic VNC's
  **8-character limit** is eight bytes; the generated password is eight random
  ASCII characters. Missing, empty, invalid or unreadable restored password files
  are replaced atomically before VNC starts. If replacement fails, startup stops;
  VNC never starts without password protection.
- **Non-root:** uid/gid 1000, `cap_drop: [ALL]` and `no-new-privileges:true`.
- **Read-only root filesystem:** writable tmpfs at `/tmp`, `/run` and
  `/home/jobpilot/.cache`; only `/app/data` is persistent writable storage.
- **Chrome sandbox on:** `docker/chrome-seccomp.json` permits the required
  user-namespace syscalls. Startup **fails closed if the sandbox cannot start**;
  it never retries with the sandbox disabled.

### Sandbox troubleshooting

Chrome uses the supplied seccomp profile and its user-namespace sandbox as uid 1000,
with `cap_drop: [ALL]` and `no-new-privileges:true`. No extra capability is needed.
If the host blocks unprivileged namespaces, fix its scoped policy as described in
the setup guide. With `JOBPILOT_CHROME_VIEW_URL` set, the backend retries CDP discovery
and fails clearly if the supervised Chrome is unavailable; it never starts another
browser or disables the sandbox. Native browser behavior is unchanged.

Inspect `docker compose logs jobpilot`. Confirm Compose uses
`seccomp=docker/chrome-seccomp.json` and a current Docker Engine/libseccomp, then
have the host administrator check unprivileged user namespaces and the container's
scoped AppArmor policy. The [sandbox troubleshooting guide](../docker/README.md#seccomp-provenance-and-sandbox-troubleshooting)
records the profile source, changes, hashes and host checks. If local policy cannot
permit sandboxed Chrome, use a host that can; do not disable the sandbox, remove
seccomp or enable privileged mode.

## Storage and backups

The database, logs, CVs and Chrome profile/logins persist in host `data/`, mounted
at `/app/data`. Only one running container may use that directory; an exclusive
lock prevents competing Chrome instances. Xvfb has TCP disabled and uses a private
Xauthority cookie shared with Chrome and x11vnc.

When `CREDENTIAL_KEY` is empty, startup generates `data/credential.key` with mode
600 and supplies it to the backend. Back it up with the database. An invalid or
unreadable existing key stops startup; it is never silently regenerated over
encrypted data. An explicit `CREDENTIAL_KEY` in `.env` takes precedence.

CLI login copies live under `HOME=/tmp/jobpilot-home` on tmpfs and disappear when
the container stops. Persistent browser logins remain in `data/jobpilot-chrome`.
Use `docker compose logs jobpilot` for service output and `docker compose down`
to stop the container; the host `data/` directory remains.
