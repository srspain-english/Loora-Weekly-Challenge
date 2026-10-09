# Juno — S&R Tutor Playbook, text prototype

A text-only proof of the teaching brain behind Juno, before any voice
infrastructure gets built. Same rules as the published Tutor Playbook
(correction tiers, A2–C1 level adaptation, session arc, end-of-call report,
student memory) — running in a terminal chat instead of on a call.

The report format is deliberately close to the class recaps S&R already
produces by hand for real students: a "You said / Better English" table,
IPA-annotated pronunciation cues, word traps, a "what went well" section, and
concrete homework — not the more generic report shape from the first draft
of the playbook.

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...   # or `ant auth login`
```

## Run it

```bash
python tutor.py --mode free
python tutor.py --mode business
python tutor.py --mode business --pack general_business
python tutor.py --mode business --scenario start_from_scratch
python tutor.py --mode business --student alex --level B2
```

Also runnable through a browser instead of Terminal — see `web.py`.

Type replies at the `You:` prompt. Type `end` (or `/end`, `quit`, `exit`) to
close the call — Juno gives spoken feedback, then a full class recap prints
to the terminal and gets written back into that student's memory file.

## Files

- `tutor.py` — the whole prototype: system-prompt construction from the
  playbook rules, the conversation loop, and the forced-tool call that
  produces the structured recap at the end.
- `web.py` — the same logic served as a browser page (imports `tutor.py`
  directly). Runs locally with no setup beyond the API key, or deploys to a
  host like Render — see **Deploying it** below. The class screen is
  voice-first: tap the orange mic to speak and what you say is sent when you
  stop (most reliable in Chrome/Brave/Edge; Safari's speech support is
  patchy, and "Type instead" is always there). Juno's replies are read aloud
  by default. **Hands-free** (on by default, remembered per device) switches
  the mic on by itself when Juno finishes; where a browser won't allow that,
  it quietly falls back to tapping. **Pause** stops Juno and the class clock; **Help me** explains
  in Spanish what Juno asked and how to answer, without adding anything to
  the class transcript (capped at 15 per class, since each one is a paid
  request).
- `scenarios.json` — eleven Business English scenario packs, nine role-plays
  each (99 in total), each built on one target expression, each pack
  spanning A2 to C1:

  | Pack | Covers |
  |---|---|
  | **Abadía Retuerta — Hospitality** | The real weekly scripts (`Loora_Abadia_Week4_Business_Fluency.html` in the repo root), reformatted into the template from Playbook §04. Roles name that property directly. |
  | **General Business English** | Common workplace idioms — touch base, push back, circle back. |
  | **Hospitality & Guest Service** | The same trade as the Abadía pack but property-neutral: any hotel, restaurant or guest-facing role. |
  | **Sales & Client Relations** | Winning clients over, following up, negotiating price, closing. |
  | **Meetings & Presentations** | Speaking up, raising a point, getting to the point under time pressure. |
  | **HR & People** | Interviews, workload, disagreement, difficult news. |
  | **Projects & Operations** | Delays, bottlenecks, owning a mistake, refusing to cut corners. |
  | **Industry & Manufacturing** | Breakdowns, capacity, quality standards, streamlining. |
  | **Agriculture & Agrifood** | Seasons, harvests, traceability, weather risk. Several expressions work literally and figuratively at once here. |
  | **Logistics & Supply Chain** | Delays, lost shipments, stock levels, the last mile. |
  | **Motivation & Coaching** | Not a sector but a theme: encouragement, setbacks, overload, reframing failure. |

  Plus the three fluency-push techniques used during the wind-down.

  Adding a sector is data, not code: append a pack to `business_packs` and
  it appears in the CLI's `--pack` and in the web selector automatically.
  `tests/test_scenarios.py` enforces what the rest of the code assumes —
  globally unique scenario ids, no expression taught twice across packs,
  every field filled, CEFR levels the prompt builder actually knows.
- `data/students/*.json` — student memory records. `alex.json` is a
  synthetic demo profile — deliberately not modeled on a real S&R student,
  since real student data lives in your Drive, not in this repo.

## Deploying it (Render)

`web.py` runs locally with no changes. To put it on a real URL you can share:

1. Go to **render.com**, sign up, and connect your GitHub account.
2. **New → Web Service**, pick this repo (`Loora-Weekly-Challenge`).
3. **Root Directory**: `tutor-prototype`
4. **Build Command**: `pip install -r requirements.txt`
5. **Start Command**: `python3 web.py`
6. Under **Environment**, add these variables:
   - `ANTHROPIC_API_KEY` — your key
   - `JUNO_ACCESS_PASSPHRASE` — a code your students will type before they can use it. **Required** — `web.py` refuses to start deployed without one, since an unprotected public URL means anyone who finds it spends your API credit with no limit.
7. Deploy. Render gives you a URL like `https://juno-xxxx.onrender.com`.

Render's free tier spins the server down after inactivity — the first request
after a quiet period takes 30–60 seconds to wake up. The page now says so
while it happens, and tells that apart from being offline or a real error.

### Optional environment variables

All have working defaults; set them only to tighten something.

| Variable | Default | What it does |
|---|---:|---|
| `JUNO_MAX_TURNS_PER_CALL` | 40 | Messages in one class before it asks the student to wrap up. |
| `JUNO_MAX_CALLS_PER_DAY` | 6 | Classes per student per day. |
| `JUNO_MAX_TRANSCRIPT_CHARS` | 120000 | Length ceiling on one class. |
| `JUNO_MAX_MESSAGE_CHARS` | 4000 | Longest single message accepted. |
| `JUNO_DAILY_COST_CEILING_USD` | 5.00 | **Emergency brake.** Once the whole deployment has spent this in a day, no new classes start for anyone. |
| `JUNO_SESSION_COST_WARN_USD` | 1.00 | Logs a warning past this much in one session. |
| `JUNO_DATA_DIR` | `data` | Folder for everything Juno keeps: the database, each student's memory (`students/`), and feedback. Set it to a persistent disk's mount path when deployed. |
| `JUNO_DB_PATH` | `<JUNO_DATA_DIR>/juno.db` | Overrides just the database location. Normally leave unset. |

### Migration (automatic, and non-destructive)

On first boot after this change, each `data/students/<name>.json` is given a
real student id and its memory **copied** to `<new id>.json`. The original
files are left byte-for-byte where they are, so nothing is lost if something
about the migration turns out to be wrong, and re-running is a no-op. Each
migration prints a line naming the old and new ids.

Nothing to run by hand; nothing to undo.

### What survives a restart, and what doesn't

Server state — identities, limits, saved classes, spend — lives in SQLite at
`data/juno.db`, so it survives the process being restarted, which is what
normally happens when the free tier idles out and wakes back up.

Without a persistent disk it does **not** survive a redeploy: every deploy
starts from an empty `data` folder, which wipes saved classes, limits, spend,
student memory and feedback. Render's free tier can't have a disk.

To keep it, put the service on a paid instance type, add a disk under the
service's **Disks** page (mount path `/opt/render/project/src/storage`,
1 GB is plenty), and set `JUNO_DATA_DIR=/opt/render/project/src/storage`.
A service with a disk can't run more than one instance, and deploys have a
few seconds of downtime while the disk moves to the new instance.

## What a call costs

Every student message spends your Anthropic credit. A rough sizing, from
measured prompt lengths rather than real invoices — a 20-turn call is around
71k input tokens and 3.8k output, because each turn resends the system
prompt and the whole transcript so far:

| | Per call | 40 calls |
|---|---:|---:|
| Opus 5, uncached | $0.45 | ~$18 |
| **Opus 5, cached (current)** | **$0.13** | **~$5** |

Prompt caching (`tutor.cacheable_system`, plus top-level `cache_control` at
each call site) is what closes that gap: a cache read costs a tenth of a
fresh read, and the repeated prefix is most of a call's tokens. It changes
nothing about the teaching — same model, same prompt, same replies.

It is also **silent when it breaks**: no error, no behaviour change, just a
bill several times larger. `tests/test_caching.py` asserts the conditions it
depends on — the markers are actually sent, the system prompt stays
byte-identical across turns, the transcript only ever grows, and the prompt
clears the model's minimum cacheable length.

Two levers deliberately not pulled, both yours to decide:

- **A cheaper model.** `tutor.MODEL` is `claude-opus-5`. Sonnet 5 would cost
  roughly $0.18 a call uncached, Haiku 4.5 about $0.09 — but unlike caching,
  that trades away correction quality, which is the whole point of the tool.
- **A real spend limit.** `MAX_CALLS_PER_SESSION` is per browser cookie, so
  clearing cookies resets it, and one passphrase is shared by every student.
  Fine for a pilot with people you know; not a cap.

## Juno's voice

Juno speaks through the browser's own speech engine — no API key, no
account, nothing to configure or pay for. Its quality is whatever voices
the student's operating system ships, which is the honest trade: good on a
Mac, plainer on a stock Windows machine.

`pickVoice()` works down a list of voices known to sound natural, then any
labelled Enhanced/Premium/Natural, then any cloud voice, then the system
default. It skips macOS's novelty voices (Albert, Zarvox, Trinoids and
friends) explicitly — those sort near the top of what `getVoices()` returns
and, before they were excluded, were what "it sounds like Stephen Hawking"
meant on a Mac in both Safari and Brave, which share the system voice list.

A paid server-side voice (ElevenLabs, Google Cloud) was tried and removed:
it added an API key, a bill per character, and a quota small enough that a
single class exhausts it, in exchange for a nicer voice on a tool whose
value is in the corrections. If it's ever revisited, the thing to keep in
mind is that a server voice must degrade to this one rather than to
silence.

## Students, limits, and saved classes

`store.py` holds the server-side state the browser used to be trusted with.

**Student selection is explicit.** The shared passphrase opens the application;
it does not identify a student. Returning students sign in with their own
personal code. Invalid codes produce an error and never select a cookie's
student or create a new record. Names are labels, not credentials, and sign-in
does not rename an existing student from the typed name.

**Shared browsers.** New student explicitly creates a separate identity, even
with the same name. Switch student signs out and clears the visible transcript,
recap, and feedback draft. The legacy `juno_student` cookie is not trusted.
Selection issues a random page token tied to the authenticated browser session.
It is not stored in localStorage or sessionStorage. Reloading requires selection
again; changing students invalidates older tabs' tokens. Browsers supporting
BroadcastChannel also clear the other tabs' visible student data and stop the
old class; without it, server-side token checks still deny stale requests.
Every student endpoint
requires that token, and every class action checks ownership before reading,
writing, generating a report, or replaying a cached message.

**Personal codes are random credentials.** New students receive a code containing
256 cryptographically random bits once, in the selection screen. They must save
it privately to return after reload; it is not saved in browser storage. The
existing `students.access_code` column stores a domain-separated SHA-256 hash,
never the issued code. Fast hashing is appropriate for these random bearer
secrets, not human-chosen passwords. Administrator provisioning always generates
codes; do not insert human-chosen codes with internal store helpers.

**Existing students must be provisioned before a future rollout.** Code-less
students cannot recover accounts by name or legacy cookie. The local-only
`admin_codes.py` command attaches a newly generated code to an exact existing
student ID, without changing lesson rows, usage, reports, or memory files. Old
plaintext codes are not accepted by the new login; replace them explicitly.
There is no public code replacement endpoint and no teacher dashboard change.

For a synthetic database only, the command shape is:

```bash
python3 admin_codes.py --database /tmp/synthetic-juno/juno.db --student-id stu_SYNTHETIC_ID
# For deliberate revocation and replacement, add --replace.
```

The command requires an existing Juno database and a private interactive
terminal, refuses redirected output, and reveals the generated code once. A
replacement invalidates the old code and prior sessions on their next request;
already-authorized requests may finish against their original student's data.
The administrator must independently verify the student ID's owner and deliver
the code privately. Losing the code requires administrator replacement. No live
student provisioning has been performed as part of development.

Before any approved production rollout: back up the database and student memory,
verify the ownership mapping without relying on names alone, provision and
privately deliver replacement codes, and verify access before allowing classes.
Provisioning intentionally does not migrate old plaintext credentials automatically
or merge same-name students. Database backups/WAL files containing old plaintext
credentials require separate retention and protection decisions.

**Login throttling survives browser resets.** Failed student-code and shared-gate
attempts are recorded in a security table in the existing SQLite database. Each
login type permits at most 10 failures per socket source and 100 deployment-wide
failures in a rolling five-minute window. Success does not erase failures;
concurrent checks are transactional. HTTP 429 asks the user to wait five minutes.
Forwarding headers are not trusted, so a reverse proxy or shared school network
may cause users to share the source limit. A trusted proxy configuration needs
verification before rollout; accepting arbitrary X-Forwarded-For is unsafe.
Limits survive process restarts while the database exists, but not disk loss.

**Shared-device sessions expire.** Sign out is available during setup, lessons,
and reports, clears visible student content and typed credentials, and revokes
both student identity and the shared gate (when configured). The server expires
sessions after 20 minutes without authenticated student requests. The page clears
student content after 20 minutes without interaction or lesson requests, even if
sign-out cannot reach the server. A session check every 15 seconds detects stale
or replaced identities, including tabs without BroadcastChannel; returning to a
visible tab also checks. Background timers may be suspended by the browser.
Students must sign out before handing a device to another person; an actively
signed-in screen remains usable until sign-out or expiration. Active speech turns
count as lesson activity through their existing message requests; recognition and
microphone algorithms are unchanged.

**Limits are server-side** and follow the selected student across browsers.
Creating another new student still creates another per-student allowance; the
shared gate and deployment-wide spending ceiling remain pilot safeguards.

**A class is saved every turn**, not at End call. Closing the tab, losing
connection, or the server restarting no longer loses the conversation: the
student can sign in with their personal code to continue it or get its report,
provided the data remains available on the host's filesystem.
End call still writes the report; it is no longer the only thing that saves.
A retried or double-tapped send carries an idempotency key, so it replays the
stored answer instead of duplicating the turn and paying for it twice.
Retry keys are bound to their original class and cannot overwrite another
class's cache. Report text is HTML-escaped before rendering.

### Security regression checks

From `tutor-prototype`, after installing `requirements.txt`, run:

```sh
python3 tests/run_tests.py
```

Or select the security checks:

```sh
python3 tests/run_tests.py test_security test_identity test_recovery
```

The runner replaces API credentials with a synthetic value, puts all data in
temporary directories, and blocks non-loopback network connections. Server
fixtures fail any model call that was not explicitly mocked. Node.js executes
the report-rendering regression. The optional browser security tests require
Python Playwright and system Chromium (`/usr/bin/chromium`); if unavailable,
their skips are reported rather than counted as browser validation. No real
student data or production services are needed.

**Metrics** go to stderr as one JSON line per turn — token counts split by
cache read and write, cost estimate, internal id. Deliberately no transcript,
no name, no key: a log carrying class content would be a second copy of the
student's data somewhere nobody is guarding. Visible in Render's log viewer,
and greppable for `"event": "turn"`.

## What this does and doesn't prove

Proves: whether the correction tiers, level adaptation, and report format
actually hold up turn-by-turn in a real conversation, against real scenario
content — cheaply, before over-investing in infrastructure. Now also
includes real (if basic) voice in the browser, and a real deployment path.

Still doesn't touch: student accounts/login (there's a shared passphrase,
not individual logins), a proper database for memory, or server-side speech
recognition (voice input relies on the browser's own, free, but
inconsistent across browsers — see the voice section above). Per the
playbook's own sequencing — see the next step of running a few sessions
yourself and each of the three trusted students the plan calls for, and
noting anywhere Juno over-corrects, under-corrects, or asks a question that
doesn't fit the level.

## A note on real student data

Your Drive has real class recaps and error-feedback docs for real S&R
students (Mila, Sandra, Ariadna, Pablo, María, Miriam and others) — genuinely
useful reference material for calibrating this further, since it's the proof
of what the report format should look like. None of that content is copied
into this repo. If you want to seed a real student's memory file for a more
realistic test, do that locally outside version control, or say the word and
we can add a `.gitignore` entry for a `data/students/private/` folder so real
student data never gets committed alongside the prototype code.


### Phase 3A: local synthetic classroom validation

The dedicated `local_mock.py` launcher runs the normal HTTP handlers and teaching
functions with a development-only Anthropic stand-in. `web.py` and `tutor.py`
do not import it and contain no environment switch that enables mocking.

From `tutor-prototype`, with dependencies installed and no inherited Juno or
Anthropic configuration, run:

```sh
python3 -B local_mock.py --allow-local-mock
```

The launcher creates disposable storage, chooses a free loopback port, and prints
one `JUNO_LOCAL_MOCK_READY` record with the local address and fixture-manifest
path. The page visibly identifies itself as a local synthetic mock. Read the
private `.synthetic-fixtures.json` manifest to obtain the synthetic-only shared
gate and student codes; these are not printed to server logs. Students A and B
share a display name but have separate identities. A third synthetic student has
historical open/finished lessons and learning memory; provisioning verifies that
the history and memory are unchanged.

To keep temporary fixtures across a process restart, explicitly choose an empty
directory directly under the system temporary directory whose name begins with
`juno-mock-`, then use the same directory on both launches:

```sh
python3 -B local_mock.py --allow-local-mock --data-dir /tmp/juno-mock-classroom
```

These fixtures are exclusively for synthetic testing. An automatically created
directory is cleaned up on normal exit; explicitly selected temporary storage
remains until deleted. Do not commit or copy the manifest, database, or memory
files into the repository. Losing temporary storage still loses its fixtures;
this does not establish Render storage durability.

The launcher requires the active `juno-v2-development` branch, refuses detached
or production checkouts, API credential/provider variables, deployment markers
(including `PORT` and Render variables), and inherited `JUNO_*` settings. It
refuses nonempty unmarked directories, symlinks, conflicting database paths, or
storage already held by another mock process. It binds only to `127.0.0.1`,
blocks outbound socket connections, and supplies no real API credential or SDK
fallback. Unknown mock request options and tools fail closed. The launcher uses
POSIX file locking and is intended for the Linux cloud development workspace.
It is deliberately unsuitable for hosted staging or production.

The mock provides deterministic conversation, help, and structured-report
responses with zero billable-token usage. It exercises the real lesson, report,
learning-memory, authentication, ownership, and storage paths; it does not assess
AI teaching quality, provider compatibility, speech recognition, or microphone
hardware. Teaching prompts and microphone code remain unchanged.

Run the local process and Chromium journeys, or the complete regression suite:

```sh
python3 tests/run_tests.py test_local_e2e
python3 tests/run_tests.py
```

The process tests stop and restart an actual server against the same temporary
storage, verify lost browser authentication and retained student identity,
recaps, transcripts, and memory, and resume the unfinished class. Browser tests
use real Chromium through Playwright, block external browser requests, and use
typed input with speech toggles disabled. Physical devices, other browser
brands, and HTTPS hosting need separate approved validation.

For the separate mocked HTTPS staging launcher, strict Render configuration,
persistent storage, consistent backups, and restore rehearsal, see
[STAGING.md](STAGING.md). Hosting creation and deployment require separate
approval; preparation alone does not create a service.
