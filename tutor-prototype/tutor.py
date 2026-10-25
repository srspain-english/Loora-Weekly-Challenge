#!/usr/bin/env python3
"""
Juno — S&R Tutor Playbook, text-only prototype of the teaching brain.

Proves out the pedagogy (correction tiers, level adaptation, session arc,
end-of-call report, student memory) in plain text before any speech
infrastructure gets built. Rules below are the direct implementation of
the Tutor Playbook v1: correction tiers, A2-C1 level adaptation, Spanish
usage policy, session arc, end-of-call report schema, student memory.

Usage:
    python tutor.py --mode free
    python tutor.py --mode business
    python tutor.py --mode business --scenario start_from_scratch
    python tutor.py --mode business --student alex --level B2
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import anthropic

MODEL = "claude-opus-5"
# The conversation itself runs on a faster model so the student is not left
# waiting between turns; the end-of-call report keeps MODEL above, where depth
# matters more than speed. Override with JUNO_TURN_MODEL.
TURN_MODEL = os.environ.get("JUNO_TURN_MODEL", "claude-sonnet-5-5")
# The conversation answers without a separate thinking step, which is most of
# the "Juno is thinking..." pause. Set JUNO_TURN_THINKING=on to bring it back.
TURN_THINKING = os.environ.get("JUNO_TURN_THINKING", "off").strip().lower()


def turn_thinking(model: str) -> dict | None:
    """The `thinking` setting that turns thinking off for this model, or None
    when it should be left as it is (thinking on, or a model that requires it)."""
    if TURN_THINKING == "on":
        return None
    if model.startswith("claude-sonnet-5-5"):
        return {"type": "between_tools"}      # Sonnet 5.5's way of "no thinking"
    if model.startswith(("claude-opus-5-5", "claude-fable", "claude-mythos")):
        return None                            # these cannot switch it off
    return {"type": "disabled"}
BASE_DIR = Path(__file__).resolve().parent
SCENARIOS_PATH = BASE_DIR / "scenarios.json"
# Everything a student builds up over time lives here. Point it at a
# persistent disk when deployed, or a redeploy wipes it.
DATA_DIR = Path(os.environ.get("JUNO_DATA_DIR", BASE_DIR / "data"))
STUDENTS_DIR = DATA_DIR / "students"

# ---------------------------------------------------------------------------
# Playbook rules (§02-§03) — condensed for the system prompt.
# Full document: the published Tutor Playbook artifact.
# ---------------------------------------------------------------------------

# Standard CEFR grammar progression — the public framework taught in every
# general ESL course, not any single publisher's proprietary syllabus.
# Ordered roughly easiest-to-hardest within each level. Structured Class
# mode works through these one at a time per student, tracked in their
# memory record, rather than repeating or randomizing.
CEFR_GRAMMAR_QUEUE: dict[str, list[str]] = {
    "A2": [
        "past simple (regular and irregular verbs)",
        "past continuous for interrupted actions and background scene-setting",
        "comparatives and superlatives",
        "going to and will for future plans",
        "present continuous for future arrangements",
        "quantifiers: some / any / how much / how many",
        "can / could for ability and polite requests",
    ],
    "B1": [
        "zero conditional for facts and routines",
        "present perfect simple: experience with ever / never",
        "present perfect simple: since vs for",
        "present perfect vs past simple",
        "first conditional",
        "modals of obligation: have to / must / should",
        "passive voice in present and past simple",
        "relative clauses with who / which / that",
    ],
    "B2": [
        "present perfect continuous",
        "second conditional",
        "conditional sequencing in professional contexts (if we do X, we'll need Y unless Z)",
        "modals of deduction: must / might / can't have",
        "reported speech",
        "passive voice across tenses",
        "causative structures: have / get something done",
        "phrasal verbs: separable and inseparable",
    ],
    "C1": [
        "third conditional and mixed conditionals",
        "inversion for emphasis",
        "fronting objects and adverbials for emphasis",
        "advanced discourse markers and linking",
        "cleft sentences for emphasis",
        "modal verbs across the certainty spectrum (must / will / should / may / might / could)",
        "nuanced modality: would rather / I'd sooner",
        "advanced concession and contrast markers (notwithstanding, whereas, while X, Y)",
        "persuasive parallel structures for high-stakes communication (not only... but also; X, which would otherwise...)",
        "register control and idiomatic collocation",
    ],
}


def next_grammar_point(level: str, student: dict) -> str:
    covered = set(student.get("grammar_covered", []))
    for point in CEFR_GRAMMAR_QUEUE.get(level, []):
        if point not in covered:
            return point
    # Whole queue for this level is covered — recycle from the top rather
    # than stall; the report can still note it as reinforcement, not new.
    queue = CEFR_GRAMMAR_QUEUE.get(level, [])
    return queue[0] if queue else "a review point of the student's choice"


LEVEL_RULES = {
    "A2": (
        "Correct meaning-breaking errors only; log everything else silently. "
        "Keep your own turns short, one idea per sentence, common vocabulary only. "
        "Ask closed or short-answer questions, one at a time."
    ),
    "B1": (
        "Correct meaning-breaking errors immediately; give a gentle recast (reuse the "
        "correct form naturally in your next line) for recurring grammar patterns like "
        "tense or agreement. Speak at a natural pace and introduce everyday idiom "
        "deliberately. Ask open questions with one follow-up for detail."
    ),
    "B2": (
        "Correct meaning-breaking errors immediately; gentle recast anything that would "
        "read as unprofessional in the scenario, within the limit on what to correct out "
        "loud below. Log minor slips silently rather than interrupting. Speak at full native pace and push for specificity and complete "
        "sentences. Push for elaboration and opinion; introduce mild disagreement."
    ),
    "C1": (
        "Hold the student to a high standard of register and word choice, not just "
        "grammar, mostly through the report; out loud, the limit below still applies. "
        "Push toward sophisticated, idiomatic phrasing. No simplification of your own "
        "language — treat the student as a colleague, not a learner. Use abstract, "
        "hypothetical, or consultative framing."
    ),
}

SPANISH_POLICY = {
    "A2": "Release valve enabled: if the student is visibly stuck, you may name one "
          "Spanish word to unblock the thought, then bridge back to English in the same turn. "
          "Cap yourself at roughly once this session.",
    "B1": "Release valve enabled: if the student is visibly stuck, you may name one "
          "Spanish word to unblock the thought, then bridge back to English in the same turn. "
          "Cap yourself at roughly once this session.",
    "B2": "Offer an English paraphrase first when the student is stuck. Only fall back to "
          "naming a single Spanish word if that paraphrase doesn't land.",
    "C1": "Disabled. If the student reaches for Spanish, ask them to describe around the "
          "word in English instead — that struggle is the exercise.",
}

CORRECTION_TIERS = textwrap.dedent("""\
    Every error you notice gets exactly one of three treatments — decide per error, not per turn:

    TIER 1 — Immediate correction. Only when the listener genuinely could not have
    understood, or the error would embarrass the student in the real situation being
    role-played. Correct in one short line, then ask your next question in the same
    reply. Never ask the student to repeat the corrected sentence, and do not turn it
    into a grammar lesson.

    TIER 2 — Gentle recast. The sentence was understood, but the error is a pattern worth
    surfacing (recurring tense mistake, false friend, unnatural collocation). Do not stop
    the student — simply reuse the corrected form naturally in your own next line.

    TIER 3 — Silent log. Minor slips (articles, small prepositions, small pronunciation
    wobbles). Never surfaced in the conversation itself — just remember it, it will
    appear in the end-of-call report instead.

    Working rule: when genuinely unsure between Tier 1 and Tier 2, choose Tier 2. Under-
    correcting and catching it in the report is cheaper than making the conversation feel
    like an exam.
""")

# Students found constant correction broke the conversation ("te corrige todo,
# no acabas siendo fluido"), and that Business English corrected far more than
# Free talk. One rule, in the shared prompt, for every mode and level.
CORRECTION_LIMIT = textwrap.dedent("""\
    WHAT TO CORRECT OUT LOUD (this overrides every other instruction about how much
    to correct, including the level rules above and any mode or scenario note below):
    - Only two kinds of mistake are corrected during the conversation: one that
      changes the meaning or would confuse a listener, and a tense or person
      mistake (present instead of past, a missing third-person -s, a verb that does
      not agree with its subject).
    - At most one correction per reply, in one short line, then carry on with your
      question. If there were two, choose the one that matters most.
    - Everything else - articles, prepositions, word order, word choice, register,
      small slips - is never said out loud. It goes into the end-of-call report.
    - Never correct the same mistake twice in a row. If it comes back, simply use
      the right form yourself in your next line.
    - A scenario's correction note only says which mistakes matter most in that
      situation, never how often to interrupt.
""")

SESSION_ARC = textwrap.dedent("""\
    This is a text chat standing in for a 15-minute voice call, so pace yourself by
    conversational turn rather than the clock:

    - Turn 1: greet the student by name and open by saying what you worked on or
      planned last time, taken from their memory record below ("Last time we worked
      on ...", or their next_recommendation). If the record shows no earlier class,
      pull one concrete thread from it instead (a fact, a struggle, or a word). Never
      open cold. State the mode for this call in one plain sentence.
    - Turn 2: one low-stakes warm-up question. Correction suppressed to Tier 3 only —
      this turn is for calibrating their level by ear, not teaching.
    - Middle turns: the actual mode content (scenario role-play, or free conversation),
      at full correction tiering for the student's level.
    - As the conversation winds toward a close (the student says goodbye, or you judge
      you're in the last couple of turns): layer in one fluency push — ask them to expand
      a short answer with a connector — then soften the topic and prepare to close.
    - On close: give two sentences of spoken feedback before signing off — one genuine
      strength, one clear focus for next time — and never end on a correction. If a
      correctable moment lands right at the goodbye, log it silently instead of
      interrupting the close.
""")

PRONUNCIATION_POLICY = textwrap.dedent("""\
    You have no phoneme-level pronunciation scoring in this prototype — you're reading
    text, not audio, so you cannot hear how a word was actually said. Never invent a score
    or percentage. What you can do, matching the real S&R class recap format: for words the
    student actually typed that are commonly mispronounced by Spanish-L1 speakers (vowel
    pairs like live/leave, word-final consonant clusters, th-sounds, stress placement on
    multisyllabic words), flag the word with its IPA and one line of what to watch for plus
    a short drill — framed as "practice this," never as a measurement of what you heard.
""")


def stable_prefix(level: str) -> str:
    """The half of the system prompt that is identical for every student.

    Split out from the per-student half so it can be cached on its own. Two
    things follow from that, and both matter:

    Privacy — nothing identifying a student is inside the block marked
    cacheable. (Anthropic's cache is keyed on an exact prefix match and scoped
    to our own workspace, so student data in there was never readable by
    anyone else; it is simply not somewhere personal data belongs.)

    Cost — this ~1,240-token block is now shared by every student at the same
    level rather than cached once per student, so a class starting after
    someone else's warms up on their cache entry instead of writing a new one.

    Depends only on `level`, and must stay byte-identical for a given level:
    interpolating anything per-student or per-call here silently costs the
    entire saving.
    """
    return "\n".join([
        "You are Juno, the S&R Spain English tutor. You follow the S&R Tutor Playbook "
        "exactly. You are warm, direct, and economical with words — you are not a customer "
        "service bot and you do not pad your replies with disclaimers or enthusiasm. Keep "
        "your own turns to 1-3 sentences unless the scenario calls for more. Introduce "
        "yourself as Juno only on a first-ever session with a student, never every call.",
        "\nTURN-TAKING: this is a spoken call, and the student must always know it is "
        "their turn. End every reply with one clear question or prompt for them, "
        "including replies that contain a correction — never end on the correction "
        "itself. The only exception is your final goodbye.",
        f"\nSTUDENT LEVEL: {level}\n{LEVEL_RULES[level]}",
        f"\nSPANISH USAGE POLICY: {SPANISH_POLICY[level]}",
        f"\nCORRECTION LAYER:\n{CORRECTION_TIERS}",
        f"\n{CORRECTION_LIMIT}",
        f"\nSESSION ARC:\n{SESSION_ARC}",
        f"\nPRONUNCIATION POLICY:\n{PRONUNCIATION_POLICY}",
        "\nHOUSE STYLE for corrections: short and specific, the way S&R's own class "
        "recaps read — e.g. 'FAN, not fun — and a big fan OF' or 'the swallowed CAN'T, "
        "plus DELIVER is the verb (delivery is the noun)'. Never a generic 'grammar "
        "mistake' label. If the student produces a whole sentence with no Spanish "
        "reached for, that belongs in what went well.",
    ])


# Added for one turn only, when the conversation length the student chose has
# run out: Juno answers, closes, and the class ends with its notes.
WRAP_UP = (
    "\nTIME IS UP for this conversation. Reply to what the student just said in one "
    "short sentence, then close warmly in one more: say it was good practice and that "
    "they can start a new conversation whenever they like. Ask no new question - "
    "this is your final goodbye."
)


# Opt-in from the student's Practice screen. It overrides the level rules'
# register on purpose: a student who asks for this finds Juno's words too hard.
EASIER_ENGLISH = (
    "\nTHE STUDENT ASKED FOR EASIER ENGLISH. Whatever the level rules above say about "
    "pace, idiom or vocabulary: use short sentences (about twelve words or fewer), the "
    "most common everyday words, and no idioms or phrasal verbs unless one is today's "
    "teaching target - and if you use one, explain it in plain words in the same reply. "
    "Ask one simple question at a time. Never use a harder word when an easy one means "
    "the same thing."
)


def dynamic_context(level: str, mode: str, scenario: dict | None, student: dict,
                    easier: bool = False) -> str:
    """The per-student half: memory, scenario, this session's grammar point.

    Never marked cacheable. It changes per student and per call, so caching it
    would buy nothing even setting privacy aside.
    """
    parts = [
        "\nSTUDENT MEMORY (use this to open the call — reference something concrete):\n"
        + json.dumps(student, indent=2),
    ]

    if mode == "free":
        parts.append(
            "\nMODE: Free Conversation. No fixed objective beyond speaking time and "
            "comfort. This mode is relaxed about *topic*, not about correction — the "
            "Correction Layer and the limit on what to correct out loud apply in full. "
            "A tense or agreement mistake still gets a short gentle recast in the same "
            "turn; everything smaller goes to the end-of-call report. Follow the student's stated "
            "interests from their memory record rather than a script."
        )
    elif mode == "structured":
        target_point = next_grammar_point(level, student)
        vocab_review = student.get("vocab_acquired_log", [])
        parts.append(
            "\nMODE: Structured Class. This is not free-flowing conversation — it follows "
            "a fixed six-part shape, same skeleton every session, matching how S&R runs a "
            "real 1:1 class. Move through the parts in order; keep each part brief in a "
            "text chat (a few exchanges, not a marathon) rather than timing it literally:\n"
            "  1. Warm Up — one or two spontaneous personal questions. No correction at "
            "all here, not even Tier 3 out loud — just build rapport and note anything for "
            "later.\n"
            "  2. Basics — quick-fire questions pushing for full-sentence answers with "
            "correct auxiliaries (do/does/is/are), not one-word replies.\n"
            "  3. Vocabulary Review — quiz the student on 3-5 terms from their vocab log "
            "below (give the meaning, they give the English term, or vice versa) before "
            "introducing anything new.\n"
            f"  4. Main Grammar Point — today's target is: {target_point}. State the rule "
            "in one plain sentence, model one example, then ask 4-6 questions that force "
            "the student to produce it themselves in full sentences. This is the heart of "
            "the session — spend the most time here.\n"
            "  5. Agility Drill — 3-4 rapid translation prompts (you give a Spanish phrase "
            "relevant to the student's real context from their memory record, they give "
            "the English fast) to build fluency reflexes, not accuracy analysis.\n"
            "  6. Homework Review — if last session's report set homework, check it "
            "briefly before closing.\n"
            f"Vocabulary log to draw part 3 from: {json.dumps(vocab_review)}",
        )
    else:
        # pack_label names the actual sector this scenario came from (Sales &
        # Client Relations, Logistics & Supply Chain, ...), not a fixed
        # string — this used to say "Abadía Retuerta pack" unconditionally,
        # regardless of which of the eleven packs the student had picked.
        pack_label = scenario.get("pack_label", "Business English")
        parts.append(
            f"\nMODE: Business English — {pack_label} pack.\n"
            f"Target expression: \"{scenario['expression']}\" ({scenario['title']}).\n"
            f"Your role: act as {scenario['role']}.\n"
            f"Question domain: {scenario['domain']}.\n"
            f"Correction instruction for this scenario: {scenario['correction_instruction']} "
            "(this says which mistakes matter most here; how often you correct out loud "
            "still follows WHAT TO CORRECT OUT LOUD above).\n"
            f"Push instruction: {scenario['push_instruction']}.\n"
            f"Closing instruction: {scenario['closing_instruction']}.\n"
            "Stay in character as that role for the whole conversation. Work the target "
            "expression into your own questions naturally rather than lecturing about it."
        )

    parts.append(
        "\nThe student ends the call by typing /end. Nothing you say should assume you "
        "know that's coming — react to it naturally when it happens, per the session arc's "
        "closing step, then stop."
    )
    if easier:
        parts.append(EASIER_ENGLISH)
    return "\n".join(parts)


def build_system_prompt(level: str, mode: str, scenario: dict | None, student: dict) -> str:
    """The whole system prompt as one string.

    Kept for the CLI and for anything that just wants to read the prompt. The
    server sends the two halves separately — see build_system_blocks.
    """
    return stable_prefix(level) + "\n" + dynamic_context(level, mode, scenario, student)


def build_system_blocks(level: str, mode: str, scenario: dict | None,
                        student: dict, easier: bool = False) -> list[dict]:
    """The system prompt as API blocks: cached prefix, then uncached context.

    Order matters. Caching matches on a prefix, so the stable half has to come
    first and carry the only breakpoint; the student's half sits after it and
    is re-read fresh every turn, which is what we want for a few hundred
    tokens that change anyway.
    """
    return [
        {
            "type": "text",
            "text": stable_prefix(level),
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": dynamic_context(level, mode, scenario, student, easier),
        },
    ]


REPORT_TOOL = {
    "name": "submit_session_report",
    "description": "Submit the structured end-of-call report for this tutoring session, "
                    "in the same format S&R already uses for its human-taught class "
                    "recaps (§06 of the Tutor Playbook, calibrated against real recaps).",
    "input_schema": {
        "type": "object",
        "properties": {
            "what_we_did": {
                "type": "string",
                "description": "Two or three sentences summarizing the session — the "
                                "mode, the scenario or topic, how it went. Matches the "
                                "'What we did today' opening of a real S&R recap.",
            },
            "corrections": {
                "type": "array",
                "minItems": 3,
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "properties": {
                        "tier": {"type": "string", "enum": ["immediate", "gentle_recast", "silent_log"]},
                        "said": {"type": "string", "description": "Exactly what the student typed."},
                        "better": {"type": "string", "description": "The corrected form."},
                        "note": {"type": "string", "description": "One short line of why — the S&R house style, e.g. 'FAN, not fun — and a big fan OF'."},
                    },
                    "required": ["tier", "said", "better", "note"],
                },
                "description": "'You said / Better English' pairs, most useful first.",
            },
            "word_traps": {
                "type": "array",
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "properties": {
                        "you_said": {"type": "string"},
                        "problem": {"type": "string", "description": "Short label, e.g. 'wrong word' or 'noun used as verb'."},
                        "we_say": {"type": "string"},
                    },
                    "required": ["you_said", "problem", "we_say"],
                },
                "description": "The highest-value recurring confusions this session, if "
                                "any repeated. Empty array is fine — most sessions won't "
                                "produce a true repeat pattern.",
            },
            "pronunciation": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "word": {"type": "string"},
                        "ipa": {"type": "string"},
                        "watch_for": {"type": "string", "description": "One line, e.g. 'clear final /d/ — livD, not lift'."},
                    },
                    "required": ["word", "ipa", "watch_for"],
                },
                "description": "Directional practice cues only, never a score — see the "
                                "pronunciation policy. Empty array if nothing stood out.",
            },
            "vocabulary_learned": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "term": {"type": "string"},
                        "meaning": {"type": "string"},
                    },
                    "required": ["term", "meaning"],
                },
                "description": "The session's target expression plus any incidental "
                                "vocabulary the student used back correctly.",
            },
            "what_went_well": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 4,
                "description": "Genuine, specific strengths — a self-correction, a "
                                "sustained argument, a word used with no Spanish reached "
                                "for. Not generic praise.",
            },
            "homework": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Concrete tasks before next session, S&R style — e.g. "
                                "'Say ten times: I CAN'T live without it.'",
            },
            "next_recommendation": {
                "type": "string",
                "description": "One line: the single biggest focus for next session. "
                                "Feeds directly into student memory.",
            },
            "updated_recurring_error_patterns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The student's recurring_error_patterns list, updated with "
                                "anything confirmed again this session. Carry forward "
                                "unresolved old ones; drop ones clearly fixed.",
            },
            "grammar_point_taught": {
                "type": "string",
                "description": "Structured Class mode only: the exact Main Grammar Point "
                                "target given in the system prompt, echoed back verbatim "
                                "so it can be marked covered. Empty string in any other mode.",
            },
            "skill_estimates": {
                "type": "object",
                "description": "A CEFR estimate of what the student actually did in THIS "
                                "conversation, for their Progress screen. Judge the evidence "
                                "on the page, not the level the class was aimed at, and not "
                                "what the student might manage on a better day. Be "
                                "conservative: when torn between two levels, choose the "
                                "lower. Pronunciation is deliberately not rated - you only "
                                "have text.",
                "properties": {
                    "enough_evidence": {
                        "type": "boolean",
                        "description": "False if the student said fewer than about six "
                                        "substantial turns, mostly answered in single words, "
                                        "or the class was cut short. The levels below are "
                                        "then ignored.",
                    },
                    "fluency": {"type": "string", "enum": ["A1", "A2", "B1", "B2", "C1", "C2"],
                                 "description": "Length and flow of what they said: full "
                                                "developed answers versus fragments."},
                    "grammar": {"type": "string", "enum": ["A1", "A2", "B1", "B2", "C1", "C2"],
                                 "description": "Range and control of structures actually used."},
                    "vocabulary": {"type": "string", "enum": ["A1", "A2", "B1", "B2", "C1", "C2"],
                                    "description": "Range and precision of words they used."},
                    "understanding": {"type": "string", "enum": ["A1", "A2", "B1", "B2", "C1", "C2"],
                                       "description": "How well they understood Juno - "
                                                      "relevant answers, few requests to repeat."},
                },
                "required": ["enough_evidence", "fluency", "grammar", "vocabulary", "understanding"],
            },
        },
        "required": [
            "what_we_did", "corrections", "word_traps", "pronunciation",
            "vocabulary_learned", "what_went_well", "homework",
            "next_recommendation", "updated_recurring_error_patterns",
            "grammar_point_taught", "skill_estimates",
        ],
    },
}


# Set by web.py when a database is configured: student memory then lives
# there, so a deploy cannot erase it. None means the JSON files below.
MEMORY_STORE = None


def load_student(student_id: str) -> dict:
    if MEMORY_STORE is not None:
        saved = MEMORY_STORE.get_memory(student_id)
        if saved is not None:
            return saved
    path = STUDENTS_DIR / f"{student_id}.json"
    if path.exists():
        return json.loads(path.read_text())
    print(f"(no memory record for '{student_id}' — starting fresh)")
    return {
        "student_id": student_id,
        "name": student_id.capitalize(),
        "cefr_level": "B1",
        "sector_context": "",
        "recurring_error_patterns": [],
        "target_vocab_queue": [],
        "vocab_acquired_log": [],
        "grammar_covered": [],
        "next_recommendation": "",
        "session_history": [],
    }


def save_student(student: dict) -> None:
    if MEMORY_STORE is not None:
        MEMORY_STORE.put_memory(student["student_id"], student)
        return
    STUDENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = STUDENTS_DIR / f"{student['student_id']}.json"
    path.write_text(json.dumps(student, indent=2))


class UnknownScenario(ValueError):
    """An unrecognised pack or scenario id.

    A plain exception rather than sys.exit() because web.py calls this on a
    request thread: SystemExit inherits from BaseException, so its `except
    Exception` would not catch it, and the request would die without ever
    answering the browser. The CLI turns this back into an exit below.
    """


def pick_scenario(scenario_id: str | None, pack: str | None = None) -> dict:
    packs = json.loads(SCENARIOS_PATH.read_text())["business_packs"]
    if pack:
        matching_packs = [p for p in packs if p["id"] == pack]
        if not matching_packs:
            pack_ids = ", ".join(p["id"] for p in packs)
            raise UnknownScenario(f"Unknown pack '{pack}'. Available: {pack_ids}")
        packs = matching_packs

    # Flattening for lookup loses which pack a scenario came from, but the
    # system prompt needs that name — not to hardcode "Abadía Retuerta" the
    # way it used to regardless of which of the eleven packs was actually
    # picked. Stamp it onto a copy rather than the loaded dict, so repeated
    # calls in the same process (the CLI's whole run, a test) don't leak
    # pack_id/pack_label onto scenarios.json's own in-memory data.
    library = [
        dict(s, pack_id=p["id"], pack_label=p["label"])
        for p in packs for s in p["scenarios"]
    ]
    if scenario_id:
        match = next((s for s in library if s["id"] == scenario_id), None)
        if not match:
            ids = ", ".join(s["id"] for s in library)
            raise UnknownScenario(f"Unknown scenario '{scenario_id}'. Available: {ids}")
        return match
    return random.choice(library)


def generate_report(client: anthropic.Anthropic, transcript: list[dict]) -> dict:
    report_request = transcript + [{
        "role": "user",
        "content": "The call has ended. Generate the end-of-call report now by calling "
                    "submit_session_report, reviewing the whole conversation above for "
                    "every correctable moment, not just the ones you spoke aloud.",
    }]
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system="You are producing the Tutor Playbook §06 end-of-call report from the "
               "transcript of a call you just finished tutoring. Be specific and honest — "
               "this report is read by the student and, eventually, by S&R Spain. The "
               "skill_estimates feed a level shown to the student, so rate only what the "
               "transcript shows, and lean low when unsure.",
        messages=report_request,
        tools=[REPORT_TOOL],
        tool_choice={"type": "tool", "name": "submit_session_report"},
    )
    tool_use = next(b for b in response.content if b.type == "tool_use")
    return tool_use.input


def print_report(report: dict) -> None:
    print("\n" + "=" * 60)
    print("CLASS RECAP")
    print("=" * 60)

    print(f"\nWhat we did today\n  {report['what_we_did']}")

    if report.get("grammar_point_taught"):
        print(f"\nMain grammar point: {report['grammar_point_taught']}")

    print("\nYour corrections to remember")
    for c in report["corrections"]:
        print(f"  [{c['tier']}] {c['said']!r} -> {c['better']!r}")
        print(f"      {c['note']}")

    if report["word_traps"]:
        print("\nWord traps — learn these")
        for w in report["word_traps"]:
            print(f"  {w['you_said']!r} ({w['problem']}) -> {w['we_say']!r}")

    if report["pronunciation"]:
        print("\nPronunciation (directional, not scored)")
        for p in report["pronunciation"]:
            print(f"  {p['word']}  {p['ipa']}  — {p['watch_for']}")

    print("\nVocabulary from today")
    for v in report["vocabulary_learned"]:
        print(f"  {v['term']} — {v['meaning']}")

    print("\nWhat went well")
    for w in report["what_went_well"]:
        print(f"  • {w}")

    if report["homework"]:
        print("\nBefore our next class")
        for h in report["homework"]:
            print(f"  • {h}")

    print(f"\nNext session focus: {report['next_recommendation']}")
    print("=" * 60 + "\n")


def apply_report_to_student(student: dict, report: dict, mode: str, scenario: dict | None) -> dict:
    known_terms = {
        entry.split(" — ")[0].strip().lower() for entry in student["vocab_acquired_log"]
    }
    for v in report["vocabulary_learned"]:
        term_key = v["term"].strip().lower()
        if term_key not in known_terms:
            student["vocab_acquired_log"].append(f"{v['term']} — {v['meaning']}")
            known_terms.add(term_key)
    student["recurring_error_patterns"] = report["updated_recurring_error_patterns"]
    student["next_recommendation"] = report["next_recommendation"]
    taught = report.get("grammar_point_taught", "").strip()
    if taught:
        student.setdefault("grammar_covered", [])
        if taught not in student["grammar_covered"]:
            student["grammar_covered"].append(taught)
    student.setdefault("session_history", []).append({
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": mode,
        "scenario": scenario["id"] if scenario else None,
        "what_we_did": report["what_we_did"],
        "next_recommendation": report["next_recommendation"],
    })
    return student


def run_call(client: anthropic.Anthropic, level: str, mode: str, scenario: dict | None, student: dict) -> None:
    system_blocks = build_system_blocks(level, mode, scenario, student)
    messages: list[dict] = []

    print("\n" + "-" * 60)
    print(f"JUNO — S&R TUTOR — {mode.upper()} · level {level}"
          + (f" · scenario: {scenario['title']}" if scenario else ""))
    print("Type your replies. Type 'end' (or /end, quit) to finish the call and get your report.")
    print("-" * 60 + "\n")

    # Juno opens, per the session arc — no user turn yet.
    opening = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=system_blocks,
        messages=[{"role": "user", "content": "(the call has just connected — open it)"}],
        cache_control={"type": "ephemeral"},
    )
    opening_text = next(b.text for b in opening.content if b.type == "text")
    print(f"Juno: {opening_text}\n")
    messages.append({"role": "user", "content": "(the call has just connected — open it)"})
    messages.append({"role": "assistant", "content": opening_text})

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n(call dropped — no report generated)")
            return

        exit_words = {"end", "quit", "exit", "/end", "/quit", "/exit"}
        if user_input.lower().strip(".!") in exit_words:
            break
        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system_blocks,
            messages=messages,
            cache_control={"type": "ephemeral"},
        )
        reply = next(b.text for b in response.content if b.type == "text")
        print(f"Juno: {reply}\n")
        messages.append({"role": "assistant", "content": reply})

    print("\n(generating report...)")
    report = generate_report(client, messages)
    print_report(report)

    student = apply_report_to_student(student, report, mode, scenario)
    save_student(student)
    print(f"Memory updated for '{student['student_id']}'.")


def main() -> None:
    global MODEL
    parser = argparse.ArgumentParser(description="S&R Tutor Playbook — text prototype")
    parser.add_argument("--mode", choices=["free", "business", "structured"], default="free")
    parser.add_argument("--student", default="alex")
    parser.add_argument("--level", choices=list(LEVEL_RULES), default=None,
                         help="Overrides the student's stored level for this call.")
    parser.add_argument("--pack", choices=["abadia_retuerta", "general_business"], default=None,
                         help="Business mode only — restrict to one scenario pack. "
                              "Either pack if omitted.")
    parser.add_argument("--scenario", default=None,
                         help="Business mode only — a scenario id from scenarios.json. "
                              "Random (within --pack, if given) if omitted.")
    parser.add_argument("--model", default=MODEL,
                         help="Override the model (default: claude-opus-5).")
    args = parser.parse_args()
    MODEL = args.model

    if not os.environ.get("ANTHROPIC_API_KEY"):
        try:
            client = anthropic.Anthropic()  # may still resolve via `ant auth login`
        except Exception:
            sys.exit("No Anthropic credentials found. Set ANTHROPIC_API_KEY or run "
                      "`ant auth login`.")
    else:
        client = anthropic.Anthropic()

    student = load_student(args.student)
    level = args.level or student.get("cefr_level", "B1").rstrip("+")
    if level not in LEVEL_RULES:
        level = "B1"

    try:
        scenario = pick_scenario(args.scenario, args.pack) if args.mode == "business" else None
    except UnknownScenario as e:
        sys.exit(str(e))  # same message and exit behaviour as before

    try:
        run_call(client, level, args.mode, scenario, student)
    except anthropic.AuthenticationError:
        sys.exit("Invalid API key. Check ANTHROPIC_API_KEY.")
    except anthropic.APIConnectionError:
        sys.exit("Network error reaching the Anthropic API.")


if __name__ == "__main__":
    main()
