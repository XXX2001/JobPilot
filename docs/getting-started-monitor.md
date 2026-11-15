# Getting started with the discovery monitor

This guide takes you from a fresh clone to your first job notification on your phone.

The discovery monitor runs inside JobPilot all the time. It checks job sources on a short timer, scores every new posting against your skills, and pushes the good ones to your phone. No AI model runs during discovery, so it costs nothing per job.

> **Monitor mode or batch mode?** JobPilot also has an older **batch mode**: you click *Refresh queue*, it scrapes a set of boards once (with an AI model for some sites), and fills the queue. Batch mode still works. The monitor is the always-on alternative: it polls by itself, uses plain web requests only, and tells you within minutes when a matching job appears. You can use both.

---

## What you need

- JobPilot installed and starting (see the [README Quickstart](../README.md#quickstart)). JobPilot needs an AI provider in `.env` to start, even though the monitor itself does not use it.
- A phone with the **ntfy** app (Android or iOS), for push notifications. Optional: without it you only see new matches in the web app.
- For reading LinkedIn job pages: **Linux with Google Chrome installed (`google-chrome` on the PATH)**, and a local install (Path B in the README). The Docker image has no Chrome. Everything else in this guide also works in Docker.

In the commands below, `data/` is the data folder in the project (the `JOBPILOT_DATA_DIR` setting). Run commands from the project folder.

---

## Step 1: set up ntfy on your phone

ntfy delivers a push to everyone subscribed to a *topic*. A topic is just a name. There is no password: **anyone who knows the topic name can read your notifications and send to it.** So pick a long, random name.

1. Make a random topic name, for example:

   ```bash
   python3 -c "import secrets; print('jobpilot-' + secrets.token_urlsafe(16))"
   ```

2. Install the ntfy app on your phone and subscribe to that topic (on the default server `ntfy.sh`, or on your own ntfy server).
3. Check that it works. Send a test from your computer:

   ```bash
   curl -d "test from JobPilot" https://ntfy.sh/<your-topic>
   ```

   The phone should show it within seconds.

Each notification contains the job title, the company, the location and the link. That text goes through the ntfy server you chose. If you do not want it on a public server, run your own ntfy server and use its address.

---

## Step 2: turn the monitor on in `.env`

Add these lines to `.env` in the project folder:

```dotenv
MONITOR_ENABLED=true
NTFY_URL=https://ntfy.sh/<your-topic>
# Optional, the defaults are fine:
# MONITOR_TICK_SECONDS=30
# MONITOR_MAX_CONCURRENCY=8
```

| Setting | Default | What it does |
|---|---|---|
| `MONITOR_ENABLED` | `false` | Turns the monitor on. When off, nothing is polled. |
| `NTFY_URL` | empty | Your ntfy topic address. When empty, there are no phone notifications. New matches still show in the web app. |
| `MONITOR_TICK_SECONDS` | `30` | How often JobPilot looks for sources that are due. Each source also has its own interval (see Step 6). |
| `MONITOR_MAX_CONCURRENCY` | `8` | How many sources are polled at the same time. |

Leave `JOBPILOT_AUTO_SUBMIT_ENABLED` at `false` (the default). With it off, JobPilot never clicks the final *Submit* button for you.

**Restart JobPilot** after any change to `.env`. Settings are read only at startup.

To check: open **Settings → Monitor** in the web app. It should say *Polling is on*.

---

## Step 3: tell JobPilot what you are looking for

1. Upload your CV in the onboarding wizard (`/onboarding`) or on the **CV** page (`/cv`).
2. In **Settings → Search**, fill in:
   - **Keywords to include**: the job titles or fields you want (for example "data engineer").
   - **Locations**: cities or countries (for example "Paris" or "France"). The watchlist import in Step 6 needs at least one.
   - **Match confidence threshold**: the lowest score that counts as a match. The default is 30.
   - Optionally, excluded keywords and excluded companies.
3. Save. Saving rescores the jobs JobPilot already has.

How the score works, in short: JobPilot reads the skills a posting asks for, using a built-in list of skills (no AI model), and checks how many of them you have. A skill the posting calls *required* counts more than a *nice to have*. A title that matches your keywords counts more. The full rules are in [Matching & fit scoring](matching.md).

---

## Step 4: log into job sites in the JobPilot Chrome

JobPilot uses its own copy of Google Chrome, with its own profile stored in `data/jobpilot-chrome`. It is never your everyday Chrome profile. For the monitor it does one thing: it reads the full LinkedIn job page of a posting that matched (see "How LinkedIn is handled" below).

Why a separate Chrome:

- Chrome refuses remote control of its default profile (Chrome 136 and later), and JobPilot refuses to use it.
- Your personal browsing, passwords and tabs stay out of reach of the app.

When you start JobPilot with `start.py`, the app itself opens in this Chrome window, so the app, your logins and the job tabs are all in one place.

**How to open it**

1. Open **Settings → Monitor**. The **JobPilot browser** card is at the top. (The setup wizard shows the same buttons in its step 4, **Accounts**.)
2. Click **Open the JobPilot browser**. A Chrome window opens with the Google (Gmail) and LinkedIn login pages for any site not already signed in.
3. Sign in to Google and LinkedIn by hand in that window, entering your passwords and any two-factor codes there. You only need to do this once; the logins are kept in `data/jobpilot-chrome`.
4. Click **Check logins**. It should show `google: logged in` and `linkedin: logged in`.

This is your applications browser: sign in to Google for job-site Google sign-in and Gmail verification mail, and to LinkedIn for job pages. Keep **Chrome Sync off**. See [The JobPilot Chrome](browser.md) for the browser and account setup decision.

JobPilot checks both Google and LinkedIn sessions and opens only the sign-in pages still needed.

The same thing from a terminal:

```bash
curl -X POST http://127.0.0.1:8000/api/browser/open \
  -H "X-JobPilot-Session: $(cat data/.session_secret)"
```

`data/.session_secret` is a key that JobPilot creates on first start. Every command that changes something in JobPilot must send it. Keep it private.

Keep the JobPilot Chrome open (minimised is fine) while you want LinkedIn pages read. It keeps running when JobPilot stops. After a reboot, open it again the same way. A bar about an "unsupported command-line flag" may show at the top. It is harmless; close it.

---

## Step 5: list your skills (recommended)

By default JobPilot reads your skills from your CV. A skill file is more reliable. When the file lists at least one skill, it replaces the skills read from the CV.

1. Copy the example:

   ```bash
   cp scripts/defaults/my_skills.example.txt data/my_skills.txt
   ```

2. Edit `data/my_skills.txt`. One skill per line, with other spellings after `|`:

   ```text
   python | python3
   ros 2 | ros2
   docker
   !Go | golang
   ```

   - Lines starting with `#` are comments.
   - `!` in front of a name makes it case-sensitive. Use it for names that are also ordinary words (`!Go`, `!C`).
   - A skill the built-in list does not know is fine. It is added.

3. New jobs use the file from the next poll. Jobs already found are rescored at the next restart.

You can also read and replace the file through the app's API (`GET` / `PUT /api/settings/skills`). A `PUT` checks the file, saves it and rescores right away. An empty file removes it and JobPilot goes back to reading the CV.

```bash
python3 -c "import json,sys; print(json.dumps({'my_skills': open('data/my_skills.txt').read()}))" \
  | curl -X PUT http://127.0.0.1:8000/api/settings/skills \
      -H "X-JobPilot-Session: $(cat data/.session_secret)" \
      -H "Content-Type: application/json" -d @-
```

Advanced: `data/skills.txt`, if present, replaces the whole built-in skill list. Most people do not need it.

---

## Step 6: add sources

A *source* is one thing the monitor polls: one company's job board, or one saved search on LinkedIn or Indeed.

| Source type (`adapter`) | What it polls | Default interval | Full description? |
|---|---|---|---|
| `greenhouse` | One company board on Greenhouse | 1 min | yes |
| `ashby` | One company board on Ashby | 1 min | yes |
| `smartrecruiters` | One company board on SmartRecruiters | 1 min | no, title only |
| `indeed` | One Indeed search | 7 min | yes |
| `linkedin_guest` | One LinkedIn search, logged out | 5 min | read in the JobPilot Chrome (see below) |

All polling is logged out: no account and no cookies are sent.

### Company boards: import a watchlist

`scripts/import_watchlist.py` adds many company boards at once from a CSV file. For each board in the file, it fetches the board once and keeps it only if at least one job is in your **Locations** (Step 3), and, with `--title-regex`, if that job's title also matches.

```bash
uv run python scripts/import_watchlist.py --ats greenhouse --csv my_greenhouse.csv
uv run python scripts/import_watchlist.py --ats ashby --csv my_ashby.csv --title-regex 'engineer|developer'
uv run python scripts/import_watchlist.py --ats smartrecruiters --csv my_smartrecruiters.csv --limit 20
```

- `--ats` must be `greenhouse`, `ashby` or `smartrecruiters`.
- `--limit N` reads only the first N rows.
- It makes one request per second, and prints `keep <name>` or `skip <name>` for each board.
- You can run it again safely. Boards already added are skipped.
- On a machine where a `PYTHONPATH` from another tool (ROS, for example) breaks `uv run`, put `env -u PYTHONPATH` in front of the command.

The CSV needs a header line and a `sample_url` column. Other columns are ignored. `sample_url` is the address of any job on the company's hosted board. JobPilot reads the board name from it:

```text
company,sample_url
Example Corp,https://job-boards.greenhouse.io/examplecorp/jobs/1234567
```

Only boards hosted on `greenhouse.io`, `ashbyhq.com` or `smartrecruiters.com` work. A company whose jobs live on its own site is skipped.

Ready-made watchlist CSVs exist in the developer's working copy under `docs/superpowers/proofs/ats/watchlist/`, but that folder is not in the repository. On a fresh clone, make your own CSV.

### A single company board

You can add one board without a CSV. This skips the location check:

```bash
curl -X POST http://127.0.0.1:8000/api/monitor/sources \
  -H "X-JobPilot-Session: $(cat data/.session_secret)" \
  -H "Content-Type: application/json" \
  -d '{"adapter": "greenhouse", "name": "greenhouse:examplecorp", "config": {"slug": "examplecorp"}}'
```

### LinkedIn and Indeed searches

These are added with the same command. The body has:

- `adapter`: `linkedin_guest` or `indeed`.
- `name`: any name you like. It must be unique.
- `config`: the search (see below).
- `interval_s` (optional): seconds between polls. Leave it out to use the default.

LinkedIn search:

```bash
curl -X POST http://127.0.0.1:8000/api/monitor/sources \
  -H "X-JobPilot-Session: $(cat data/.session_secret)" \
  -H "Content-Type: application/json" \
  -d '{"adapter": "linkedin_guest", "name": "linkedin:data:Paris",
       "config": {"keywords": "data engineer", "location": "Paris, France"}}'
```

Indeed search:

```bash
curl -X POST http://127.0.0.1:8000/api/monitor/sources \
  -H "X-JobPilot-Session: $(cat data/.session_secret)" \
  -H "Content-Type: application/json" \
  -d '{"adapter": "indeed", "name": "indeed:data:Paris",
       "config": {"what": "data engineer", "where": "Paris", "country": "FR", "radius_km": 30}}'
```

For Indeed, `what` and `where` are required. `country` is a two-letter country code (default `FR`), and `radius_km` defaults to 30.

The answer is the new source. `409` means the name is taken. `422` means the adapter name is wrong.

Keep the number of LinkedIn searches small (one or two), and do not set a shorter interval than the default. Each LinkedIn poll can make up to five requests.

---

## Step 7: wait for the first notification

Open **Settings → Monitor**. You see every source, its interval, its last and next poll, how many jobs it has seen, and its health. The pause/resume button stops or restarts one source.

**The first poll of a source is silent.** It stores and scores everything already on the board or in the search, but sends no notification. Those jobs show in the queue. From the second poll on, only jobs that are new are announced.

Company boards are often quiet for hours. In practice, the first notification usually comes from a LinkedIn or Indeed search.

New matches appear in three places:

- a push on your phone (if `NTFY_URL` is set),
- the "What's new" section of the Today page, live,
- the queue.

Each job is matched once. A job you have already seen, or one reposted with the same title, city and description, does not notify again.

---

## What a notification says

The notification title is `New match <score>`, sometimes followed by notes in brackets. The text is `<job title> — <company> (<location>)`. Tapping it opens the posting.

| Note | Meaning |
|---|---|
| `title only` | JobPilot had no usable description, only the title. The score is a guess from the title: 50 if one of your keywords is in the title, 40 if one of your skills is. Open the posting to judge it. SmartRecruiters jobs, and LinkedIn jobs that could not be read, are title only. In the queue, title-only matches come after the other matches of the same day. |
| `location unverified` | Your location setting is a country, and the job's town is one JobPilot does not know, so it could not confirm the country. It let the job through rather than drop it. |
| `Easy Apply` | LinkedIn says this job uses Easy Apply (LinkedIn's own form). |

A notification titled **Source disabled** means a source was refused with an authorisation error (for example, Indeed's app key stopped working). That source is not retried until you resume it.

---

## How LinkedIn is handled

LinkedIn search results come without the job description. This is what JobPilot does with them:

1. The search itself is polled **logged out**, like a visitor without an account.
2. When a card matches on its title, and the JobPilot Chrome is open and logged in, JobPilot opens that one job page in a new tab, the way you would. It reads the description and the apply method (Easy Apply, external site, or closed), closes the tab, and rescores the job.
3. A closed posting, or one whose description brings it under your threshold, stays in the queue but is **not pushed** to your phone.

Limits, so the pace stays slow:

- at most 5 job pages per poll,
- at most 80 job pages per day (counted in memory, so a restart resets the count),
- a pause of 6 to 18 seconds between two pages.

Only matched jobs are opened, never the whole search. Nothing is opened on a source's first poll.

If the JobPilot Chrome is closed, logged out, or LinkedIn shows a login or security check, JobPilot stops opening pages and the match is pushed as `title only`.

---

## Troubleshooting

**Settings → Monitor says polling is off.** Set `MONITOR_ENABLED=true` in `.env` and restart JobPilot.

**A source shows "failed" in its health column.** JobPilot waits longer after each failure (up to 6 hours) and tries again by itself. `blocked: HTTP 429` or `HTTP 403` means the site is refusing requests for now; it usually recovers. `HTTP 404` on a company board usually means a wrong board name. Click resume to try again at once.

**A source is paused and I did not pause it.** It was disabled after an authorisation error (you also got a "Source disabled" notification). Fix the cause, then click resume.

**LinkedIn matches are always "title only".**
- Check the JobPilot Chrome is running and logged in (**Check logins** in the wizard, or `GET /api/browser/status`).
- If LinkedIn asks you to log in again or to pass a check, do it by hand in the JobPilot Chrome window. The log file `data/logs/jobpilot.log` shows "LinkedIn asks for a login or a check" when this happens.
- The limits above (5 per poll, 80 per day) may have been reached.

**The JobPilot browser does not open.** Google Chrome must be installed (`google-chrome` on the PATH). If another Chrome is already using `data/jobpilot-chrome` without remote control, close it first. Chrome's own log is in `data/jobpilot-chrome.chrome.log`.

**No matches at all.**
- Wait for the second poll: the first one is silent.
- Look at the queue. Jobs from the first poll are there, without notifications.
- Lower the **Match confidence threshold**. Above 50, title-only jobs can never match.
- Check **Locations** and excluded keywords: a job outside your locations scores 0.
- Check your keywords and `data/my_skills.txt`. A job that names none of your skills scores 0.
- Check `NTFY_URL`, and that the test push in Step 1 worked.

**`curl` answers `Session required; reload the app`.** The `X-JobPilot-Session` header is missing or wrong. It must hold the content of `data/.session_secret`.

**Will running the tests send notifications?** No. The test suite always turns the monitor off and clears `NTFY_URL`, whatever your `.env` says, and it uses a temporary data folder. Your real database and your phone are not touched. (If `uv run pytest` fails at import on a machine with a `PYTHONPATH` from another tool, run `env -u PYTHONPATH uv run pytest`.)

---

## Risks and limits

- **Site terms.** LinkedIn's User Agreement prohibits automated access to its site, including scraping. Indeed and other sites have similar terms. JobPilot reduces how much it does: discovery is logged out, only matched jobs are opened in your logged-in Chrome, and at a slow pace with daily limits. It does not remove the risk. **If LinkedIn restricts or closes your account, that risk is yours.** If that matters to you, do not log the JobPilot Chrome into LinkedIn, and remove LinkedIn sources.
- **The LinkedIn guest search is not an official API.** It can change or stop working at any time. JobPilot then backs off, and the source shows failures.
- **The JobPilot Chrome can be controlled by any program on your computer** while it runs, through its local remote-control port. Use it for job sites and the Google (Gmail) account used for applications, keep Chrome Sync off, and leave unrelated personal browsing and banking in your everyday browser. See [The JobPilot Chrome](browser.md).
- **ntfy topics are public by name.** Anyone who knows yours can read your job notifications and send to your phone.
- **Scores are a filter, not a judgement.** They come from a skill list, not from reading the posting the way a person does. Title-only scores are rough.

---

## Not covered here, or not built yet

- **Applying through the JobPilot Chrome**, and **captcha handling** while applying, are being worked on. This guide does not cover them. Here, the JobPilot Chrome is only used to *read* LinkedIn job pages. For applying, see the [user guide](user-guide.md).
- **Welcome to the Jungle** as a monitor source (it is still in batch mode).
- **Lever, Teamtailor and Personio** company boards.
- **Automatic final submission** stays off (`JOBPILOT_AUTO_SUBMIT_ENABLED=false`). Keep it off.
