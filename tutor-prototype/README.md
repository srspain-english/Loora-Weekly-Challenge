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
- `scenarios.json` — two Business English scenario packs a student picks
  between: **Abadía Retuerta**, the nine role-plays reformatted from the real
  weekly scripts (`Loora_Abadia_Week4_Business_Fluency.html` in the repo
  root) into the template from Playbook §04; and **General Business
  English**, nine original scenarios covering common workplace idioms
  (touch base, push back, circle back, and so on) for students outside
  hospitality. Plus the three fluency-push techniques used during the
  wind-down.
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
   - `ELEVENLABS_API_KEY` — *optional*, see **Juno's voice** below.
7. Deploy. Render gives you a URL like `https://juno-xxxx.onrender.com`.

Render's free tier spins the server down after inactivity — the first request
after a quiet period takes 30–60 seconds to wake up. That's normal, not a
bug; a paid tier removes it if it becomes annoying.

Student memory (`data/students/*.json`) lives on Render's disk, which is
**not persistent on the free tier** — it can reset on redeploy. Fine for a
short pilot; if you need memory to survive long-term, that needs a real
database, which is a bigger step than this prototype takes.

## Juno's voice

Two engines, in this order:

1. **ElevenLabs**, if `ELEVENLABS_API_KEY` is set on the server. Every
   student then hears the same voice, at the same quality, on any machine.
2. **The browser's own speech engine**, otherwise — and automatically
   whenever ElevenLabs fails, so a voice problem never costs a reply.

The fallback's quality is out of our hands: it's whatever voices the
student's operating system ships. Good on a Mac with Enhanced voices
downloaded, robotic on a stock Windows machine. `pickVoice()` skips macOS's
novelty voices (Albert, Zarvox, Trinoids and friends) explicitly, since
those sort near the top of the system list and are what "it sounds like
Stephen Hawking" usually means on a Mac.

Optional overrides, both with sensible defaults:

- `ELEVENLABS_VOICE_ID` — defaults to Bella (`EXAVITQu4vr4xnSDxMaL`). Voice
  IDs come from the Voice Library in your ElevenLabs account.
- `ELEVENLABS_MODEL` — defaults to `eleven_multilingual_v2`.

**Watch the character quota.** ElevenLabs bills per character synthesised,
and the free tier is small — roughly one or two full practice calls. A class
of students will exhaust it quickly, so check your usage in the ElevenLabs
dashboard before pointing a group at a deployment. Two things soften this:
repeated lines (greetings, prompts, encouragement) are cached in memory and
only ever billed once per server run, and any single reply is capped at
`MAX_SPEAK_LENGTH` characters. When the quota runs out, ElevenLabs starts
refusing requests and every student silently drops to the browser voice —
the app keeps working, but the voice gets worse, and the reason is only
visible in the Render logs.

Never commit the key. It belongs in Render's Environment settings, and
locally in your shell:

```bash
export ELEVENLABS_API_KEY=...
python3 web.py
```

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
