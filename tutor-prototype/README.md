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
- `web.py` — the same logic served as a local browser page (imports `tutor.py`
  directly) so a real student can use Juno without touching Terminal.
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

## What this does and doesn't prove

Proves: whether the correction tiers, level adaptation, and report format
actually hold up turn-by-turn in a real conversation, against real scenario
content — cheaply, with no speech infrastructure to build first.

Doesn't touch: voice (recognition, synthesis, endpointing, latency), the web
shell, or student accounts. Per the playbook's own sequencing, those come
after this loop feels right — see the `/loop`-worthy next step of running a
few sessions yourself and each of the three trusted students the plan calls
for, and noting anywhere Juno over-corrects, under-corrects, or asks a
question that doesn't fit the level.

## A note on real student data

Your Drive has real class recaps and error-feedback docs for real S&R
students (Mila, Sandra, Ariadna, Pablo, María, Miriam and others) — genuinely
useful reference material for calibrating this further, since it's the proof
of what the report format should look like. None of that content is copied
into this repo. If you want to seed a real student's memory file for a more
realistic test, do that locally outside version control, or say the word and
we can add a `.gitignore` entry for a `data/students/private/` folder so real
student data never gets committed alongside the prototype code.
