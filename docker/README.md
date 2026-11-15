# Optional Docker Chrome desktop

This image targets **linux/amd64**. Native arm64 Chrome images are unsupported;
emulating amd64 on an arm64 host is not validated. The existing Python, frontend,
Tectonic and runtime build stages, `jobpilot` user (uid/gid 1000), and HTTP healthcheck
are retained. Google Chrome stable comes from [Google's signed apt repository](https://www.google.com/linuxrepositories/).
The build verifies Google's primary key fingerprint
`EB4C1BFD4F042F6DDDCCEC917721F63BD38B4796` and fails if it does not match.
Only that key is exported to the repository keyring, scoped with `signed-by`.
[noVNC 1:1.3.0-1](https://packages.debian.org/bookworm/novnc) and
[websockify 0.10.0+dfsg1-4+b1](https://packages.debian.org/bookworm/websockify) are
pinned Debian Bookworm packages. The frontend and optional CLI stage use Node 22.

Require **Docker Engine 28.0.0 or newer** and the Compose v2 plugin. In older
Engine releases, LAN neighbours on the same L2 segment can reach ports published
to `127.0.0.1`; see [Docker's port-publishing warning](https://docs.docker.com/engine/network/port-publishing/).
Check the server version on the host with `docker version --format '{{.Server.Version}}'`.
The entrypoint cannot inspect the host Engine version; this prerequisite is enforced
by the operator. The Docker socket is not mounted in the container.

## Chrome security updates

Rebuild with `docker compose build --pull` **at least monthly**, and promptly for
Chrome security fixes. Add `--no-cache` so a cached apt layer cannot preserve an
old Chrome package even when the base image has not changed:

```sh
docker compose build --pull --no-cache
docker compose up -d --force-recreate
```

For the CLI image, use both `-f docker-compose.yml -f docker-compose.cli.yml`
arguments on each command. The entrypoint logs a warning when the installed Chrome
build is **older than 45 days**, using the first entry's package date from
`/usr/share/doc/google-chrome-stable/changelog.gz`. It does not use the image build
or installation time. An unreadable/invalid date also emits a warning; neither
warning stops startup. The read-only image does not update Chrome at runtime.

## Storage and startup

Use a `.env` based on `.env.example`; the API writer accepts `LLM_API_KEY` and the
existing model settings. The host `data/` directory must be writable by uid 1000.
For a new directory, a host administrator can prepare it with
`sudo install -d -o 1000 -g 1000 -m 700 data`. After restoring a backup, restore that
ownership too. Only one running container may use the data directory; an exclusive
volume lock prevents competing Chrome instances.

The Python supervisor starts Xvfb, Chrome, x11vnc, noVNC and uvicorn in that order,
waiting for each dependency. A child exit stops the other processes and exits
nonzero. Tini reaps orphaned children; SIGTERM shuts down the service processes.
Chrome's fixed-port endpoint is recorded in `jobpilot-chrome/DevToolsActivePort`
so the existing backend helpers attach to the supervised browser. With
`JOBPILOT_CHROME_VIEW_URL` set, endpoint discovery retries three one-second probes
with one-second pauses (up to roughly five seconds). If Chrome remains unavailable,
apply runs raise "The JobPilot Chrome in the container is not answering"; they
never launch a replacement browser. Native fallback behavior is unchanged.
Xvfb disables TCP listening and requires a private Xauthority cookie shared with
Chrome and x11vnc. The container Chrome omits the automation flag that produces
the unsupported-command-line-flag banner.

Compose publishes only `127.0.0.1:8000` (app) and `127.0.0.1:6080` (Chrome view).
Chrome debugging stays on container loopback port 9222, and password-protected VNC
on loopback port 5900. Open `http://127.0.0.1:6080/vnc.html?autoconnect=true`, or use
**Open the Chrome view** in the app; that control also opens missing sign-in tabs.
The Chrome view password is in **`data/chrome-view.password` on the Docker host**.
It is never included in API responses, command arguments or application logs.
Classic VNC uses eight password bytes, so the generated password is eight random
ASCII characters. Missing, empty, invalid or unreadable restored password files are
replaced atomically with mode 600 before VNC starts. If replacement is impossible,
startup fails; VNC never starts without the password option.

The root filesystem is read-only. `/tmp`, `/run` and `/home/jobpilot/.cache` are
tmpfs; `HOME=/tmp/jobpilot-home` gives Chrome and optional CLIs writable temporary
state. Chrome profiles, logins and application data persist under `/app/data`.
When `CREDENTIAL_KEY` is empty, startup saves a generated key at
`data/credential.key` (mode 600) and exports it to the backend. Back up this key with
the database. An unreadable or invalid existing encryption key stops startup;
it must not be regenerated over encrypted data. An explicit key in `.env` takes
precedence.

## Optional CLI logins

The `cli` image stage supplies `@anthropic-ai/claude-code@2.1.325` and
`@openai/codex@0.166.0`, matching the host versions verified on 2026-11-15.
`docker/cli/package.json` and `package-lock.json` pin versions and npm artifact
integrity hashes. A separate root build stage uses `npm ci --ignore-scripts`, then
runs Claude's required native-binary setup offline; both version commands must
succeed. The final image runs the CLIs as uid 1000.

Before starting, edit `docker-compose.cli.yml` to remove mounts for any provider
you do not use and any optional file that does not exist. The only allowed sources
are:

| Host file | Use |
| --- | --- |
| `~/.claude/.credentials.json` | Claude login; remove the bind if using only Codex/API writers. |
| `~/.codex/auth.json` | Codex login; remove the bind if using only Claude/API writers. |
| `~/.codex/config.toml` | Optional Codex configuration; remove the bind if absent. |

The example enables all three. Compose has no optional bind-file switch: each
remaining source must already be a regular file readable by uid 1000. Long-syntax
binds set `read_only: true` and `create_host_path: false`, so a missing source fails
at container creation without creating directories on the host. Do not create empty
login files to satisfy the mounts. Avoid symlink sources: the host Docker daemon
may resolve them before mounting, and dangling sources fail at mount time.

```sh
docker compose -f docker-compose.yml -f docker-compose.cli.yml up -d --build
```

Startup copies only these three files from `/host-cli` into the tmpfs
`HOME=/tmp/jobpilot-home`, using mode 600 files and mode 700 directories. It skips
missing files, non-regular files and symlinks (including dangling links and symlinked
provider directories) without following them. Entire CLI directories, history,
plugins, other settings and `~/.claude.json` are not imported. The read-only binds
prevent container writes through those mounts; CLI refreshes change only temporary
copies. Refreshed tokens/state are lost whenever the container stops; renew the host
login and recreate the container when necessary. Host login tools may still update
their own files. These logins grant the container the corresponding account's powers;
enable the override deliberately.

## Seccomp provenance and sandbox troubleshooting

`chrome-seccomp.json` contains the complete syscall rules from
[Jess Frazelle's Chrome profile](https://github.com/jessfraz/dotfiles/blob/c19b5e2670197748afaab605a6fc68313870c29d/etc/docker/seccomp/chrome.json),
retrieved **2026-11-15**, revision `c19b5e2670197748afaab605a6fc68313870c29d`.
Upstream SHA-256:
`5d92dc9c74781df77b82e38ce802ef82f0b603a9f40ac3b20f8202150b33200b`.

The upstream default-deny policy and its `clone`, `setns`, `unshare`, and `seccomp`
rules are preserved. Its 2016 syscall list needs additions for modern Bookworm
glibc and Chrome: `rseq`, `statx`, `faccessat2`, `close_range`, `epoll_pwait2`,
`membarrier`, `copy_file_range`, `preadv2`, and `pwritev2` are allowed. `clone3`
returns `ENOSYS` (38) so glibc falls back to the allowed `clone`, following
[Moby's default profile](https://github.com/moby/profiles/blob/main/seccomp/default.json).
Shipped SHA-256:
`730bc23a85edbfb4bd36bd503122597fa42628c83975e57cf2aaefe21d4cec28`.

Chrome runs as uid 1000 with its sandbox enabled, `cap_drop: [ALL]`, and
`no-new-privileges:true`. Its [user-namespace sandbox](https://chromium.googlesource.com/chromium/src/+/9a2c4b7730a398293bdda2af079a76686ab06038/docs/linux/sandboxing.md)
uses unprivileged namespaces and requires no added container capabilities;
`CAP_SYS_ADMIN` is not needed. Host namespace policy and the seccomp rules still apply.
If Chrome fails to start, the supervisor prints a diagnostic pointing here and
stops the container; it never retries with a disabled sandbox. Inspect
`docker compose logs jobpilot`. Ensure Compose loads `seccomp=docker/chrome-seccomp.json`
and the host uses a current Docker Engine/libseccomp that recognizes these syscalls.

Chrome needs unprivileged user namespaces on the host. Check
`sysctl user.max_user_namespaces` and, on distributions providing it,
`sysctl kernel.unprivileged_userns_clone`. A host administrator can enable them
with `sudo sysctl -w user.max_user_namespaces=28633` and
`sudo sysctl -w kernel.unprivileged_userns_clone=1` respectively if disabled.
If AppArmor denies user namespace creation, the administrator must permit it in
the container's scoped AppArmor policy; see
[Chromium's explanation of AppArmor user namespace restrictions](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/security/apparmor-userns-restrictions.md).
Use a host that permits sandboxed Chrome when local policy forbids this; do not
remove seccomp, add privileged mode, or disable Chrome's sandbox.

## Validation handoff

No Docker build or container run was performed in the implementation sandbox.
On the target amd64 host, build/start the default Compose configuration, check
`/api/health` and `/api/browser/status`, and confirm the Chrome view demands the
password and accepts interactive logins. Recreate it to verify persistence, then
repeat with the CLI override and check `claude --version` / `codex --version` as
the container user. These are host validation steps, not claims of completed runs.
