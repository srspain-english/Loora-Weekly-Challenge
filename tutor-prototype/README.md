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
  host like Render — see **Deploying it** below. Includes real voice: hold
  the mic button to speak (Chrome/Brave/Edge — Safari doesn't support the
  browser speech API this uses), and Juno's replies are read aloud by
  default.
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
| `JUNO_DB_PATH` | `data/juno.db` | Where server state lives. |

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

It does **not** survive a redeploy: Render's free tier has no persistent
disk, so a new deploy starts from an empty database. In practice that means
daily limits reset and unfinished classes become unrecoverable on the day you
deploy. For a pilot that is a reasonable trade; if this outgrows it, the fix
is a Render disk or a managed Postgres, and `store.py` is the only file that
would change.

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

**Identity is not the name.** A student is a random `stu_…` id; the name they
type is only a label. So two students called Maria never share a memory
record, and one student retyping her own name keeps hers. Identity is
resolved from, in order: an individual access code, a year-long cookie, or a
new record.

**Individual codes** are the way off a single shared passphrase. The schema
and lookup are in place (`students.access_code`, and a field on the setup
screen); handing them out is a decision, not a code change. Until then the
shared passphrase means anyone with the code can start a class as a new
student — fine among people you know, not a real access control.

**Limits are server-side**, so clearing cookies or switching browser resets
nothing. Both per-student and a deployment-wide daily spend ceiling.

**A class is saved every turn**, not at End call. Closing the tab, losing
connection, or the server restarting no longer loses the conversation: the
student is offered it back, and can either continue it or get its report.
End call still writes the report; it is no longer the only thing that saves.
A retried or double-tapped send carries an idempotency key, so it replays the
stored answer instead of duplicating the turn and paying for it twice.

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
