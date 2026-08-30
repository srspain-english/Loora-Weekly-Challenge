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
BASE_DIR = Path(__file__).resolve().parent
SCENARIOS_PATH = BASE_DIR / "scenarios.json"
STUDENTS_DIR = BASE_DIR / "data" / "students"

# ---------------------------------------------------------------------------
# Playbook rules (§02-§03) — condensed for the system prompt.
# Full document: the published Tutor Playbook artifact.
# ---------------------------------------------------------------------------

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
        "read as unprofessional in the scenario. Log minor slips silently rather than "
        "interrupting. Speak at full native pace and push for specificity and complete "
        "sentences. Push for elaboration and opinion; introduce mild disagreement."
    ),
    "C1": (
        "Correct throughout, including register and word choice, not just grammar. "
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
    role-played. Stop, correct in one short line, get a quick repeat, move on — do not
    turn it into a grammar lesson.

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

SESSION_ARC = textwrap.dedent("""\
    This is a text chat standing in for a 15-minute voice call, so pace yourself by
    conversational turn rather than the clock:

    - Turn 1: greet the student by name, pull one concrete thread from their memory
      record below (a fact, a struggle, or a word they learned last time). Never open cold.
      State the mode for this call in one plain sentence.
    - Turn 2: one low-stakes warm-up question. Correction suppressed to Tier 3 only —
      this turn is for calibrating their level by ear, not teaching.
    - Middle turns: the actual mode content (scenario role-play, or free conversation),
      at full correction tiering for the student's level.
    - As the conversation winds toward a close (the student says goodbye, or you judge
      you're in the last couple of turns): layer in one fluency push — ask them to expand
      a short answer with a connector, or repeat something faster and more confidently —
      then soften the topic and prepare to close.
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


def build_system_prompt(level: str, mode: str, scenario: dict | None, student: dict) -> str:
    parts = [
        "You are Juno, the S&R Spain English tutor. You follow the S&R Tutor Playbook "
        "exactly. You are warm, direct, and economical with words — you are not a customer "
        "service bot and you do not pad your replies with disclaimers or enthusiasm. Keep "
        "your own turns to 1-3 sentences unless the scenario calls for more. Introduce "
        "yourself as Juno only on a first-ever session with a student, never every call.",
        f"\nSTUDENT LEVEL: {level}\n{LEVEL_RULES[level]}",
        f"\nSPANISH USAGE POLICY: {SPANISH_POLICY[level]}",
        f"\nCORRECTION LAYER:\n{CORRECTION_TIERS}",
        f"\nSESSION ARC:\n{SESSION_ARC}",
        f"\nPRONUNCIATION POLICY:\n{PRONUNCIATION_POLICY}",
        "\nSTUDENT MEMORY (use this to open the call — reference something concrete):\n"
        + json.dumps(student, indent=2),
        "\nHOUSE STYLE for corrections: short and specific, the way S&R's own class "
        "recaps read — e.g. 'FAN, not fun — and a big fan OF' or 'the swallowed CAN'T, "
        "plus DELIVER is the verb (delivery is the noun)'. Never a generic 'grammar "
        "mistake' label. If the student produces a whole sentence with no Spanish "
        "reached for, that belongs in what went well.",
    ]

    if mode == "free":
        parts.append(
            "\nMODE: Free Conversation. No fixed objective beyond speaking time and "
            "comfort. This mode is relaxed about *topic*, not about correction — the "
            "tier rules in the Correction Layer above still apply in full. A recurring "
            "grammar pattern (tense, agreement, a repeated false friend) still gets a "
            "Tier 2 gentle recast the moment you notice it, in the same turn, not saved "
            "for the end-of-call report. Only genuinely minor, one-off slips (an article, "
            "a small preposition) default to Tier 3 here. Follow the student's stated "
            "interests from their memory record rather than a script."
        )
    else:
        parts.append(
            "\nMODE: Business English — Abadía Retuerta pack.\n"
            f"Target expression: \"{scenario['expression']}\" ({scenario['title']}).\n"
            f"Your role: act as {scenario['role']}.\n"
            f"Question domain: {scenario['domain']}.\n"
            f"Correction instruction for this scenario: {scenario['correction_instruction']}.\n"
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
    return "\n".join(parts)


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
        },
        "required": [
            "what_we_did", "corrections", "word_traps", "pronunciation",
            "vocabulary_learned", "what_went_well", "homework",
            "next_recommendation", "updated_recurring_error_patterns",
        ],
    },
}


def load_student(student_id: str) -> dict:
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
        "next_recommendation": "",
        "session_history": [],
    }


def save_student(student: dict) -> None:
    STUDENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = STUDENTS_DIR / f"{student['student_id']}.json"
    path.write_text(json.dumps(student, indent=2))


def pick_scenario(scenario_id: str | None) -> dict:
    library = json.loads(SCENARIOS_PATH.read_text())["business"]
    if scenario_id:
        match = next((s for s in library if s["id"] == scenario_id), None)
        if not match:
            ids = ", ".join(s["id"] for s in library)
            sys.exit(f"Unknown scenario '{scenario_id}'. Available: {ids}")
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
               "this report is read by the student and, eventually, by S&R Spain.",
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
    student.setdefault("session_history", []).append({
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": mode,
        "scenario": scenario["id"] if scenario else None,
        "what_we_did": report["what_we_did"],
        "next_recommendation": report["next_recommendation"],
    })
    return student


def run_call(client: anthropic.Anthropic, level: str, mode: str, scenario: dict | None, student: dict) -> None:
    system_prompt = build_system_prompt(level, mode, scenario, student)
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
        system=system_prompt,
        messages=[{"role": "user", "content": "(the call has just connected — open it)"}],
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
            system=system_prompt,
            messages=messages,
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
    parser.add_argument("--mode", choices=["free", "business"], default="free")
    parser.add_argument("--student", default="alex")
    parser.add_argument("--level", choices=list(LEVEL_RULES), default=None,
                         help="Overrides the student's stored level for this call.")
    parser.add_argument("--scenario", default=None,
                         help="Business mode only — a scenario id from scenarios.json. "
                              "Random if omitted.")
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

    scenario = pick_scenario(args.scenario) if args.mode == "business" else None

    try:
        run_call(client, level, args.mode, scenario, student)
    except anthropic.AuthenticationError:
        sys.exit("Invalid API key. Check ANTHROPIC_API_KEY.")
    except anthropic.APIConnectionError:
        sys.exit("Network error reaching the Anthropic API.")


if __name__ == "__main__":
    main()
