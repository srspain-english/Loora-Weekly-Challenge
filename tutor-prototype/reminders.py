#!/usr/bin/env python3
"""What to say to an adult student who has gone quiet, and whether to say it.

The teacher sends these by hand (a WhatsApp message from their own number), so
this module only suggests. The rules come from how adult learners respond:
sober and practical rather than childish or guilt-inducing, short sessions
rather than "come and practise", at most one reminder a day and spaced further
apart the longer someone is away, softer at weekends, and a last message that
closes the door kindly instead of pressing. The wording follows the teacher's
own writing: no exclamation marks, no emoji, no hype.
"""

from __future__ import annotations

from datetime import date

KINDS_THAT_SEND = {"welcome", "streak", "nudge", "back_3", "back_7", "closing"}


def suggest(facts: dict, site_url: str = "") -> dict:
    """facts: a row of store.students_overview. Returns {kind, label, message, soft}."""
    name = (facts.get("display_name") or "").strip() or "hola"
    code = facts.get("access_code")
    n = facts.get("days_since_last")
    ago = facts.get("reminded_ago_days")
    streak = facts.get("streak_days") or 0
    try:
        weekend = date.fromisoformat(facts["today"]).weekday() >= 5
    except (KeyError, ValueError, TypeError):
        weekend = False
    where = f" en {site_url}" if site_url else ""
    entry = f" Entra con tu código {code}." if code else ""

    def out(kind, label, message=""):
        return {"kind": kind, "label": label, "message": message,
                "soft": bool(weekend and kind in KINDS_THAT_SEND)}

    if facts.get("paused"):
        return out("paused", "Reminders paused")
    if n == 0:
        return out("done", "Practised today")
    if ago == 0:
        return out("wait", "Reminded today")
    if facts.get("classes", 0) == 0:
        sent = facts.get("reminders_sent") or 0
        if sent >= 2:
            return out("quiet", "Reminded twice, has not started")
        if sent == 1:
            if ago is not None and ago < 7:
                return out("wait", "Reminded recently")
            return out("closing", "Has not started", (
                f"Hola {name}, no quiero llenarte de mensajes, así que este es el último por ahora. "
                f"Tu espacio con Juno sigue listo{where} por si quieres empezar.{entry} "
                "Cuando quieras, me escribes."))
        if ago is not None and ago < 3:
            return out("wait", "Reminded recently")
        return out("welcome", "Has not started", (
            f"Hola {name}, tu espacio de práctica con Juno está listo{where}.{entry} "
            "Bastan 10 minutos hoy para empezar, y se puede hacer desde el móvil en cualquier hueco. "
            "Si prefieres empezar otro día, dime cuál y te lo recuerdo."))
    if n is None:
        return out("wait", "No data")
    if n <= 2:
        if streak >= 2 and n == 1:
            return out("streak", f"Streak of {streak} days at risk", (
                f"Hola {name}, llevas {streak} días seguidos practicando con Juno. "
                "Hoy bastan 10 minutos para mantenerlos. "
                "Si esta semana tienes viaje o mucho trabajo, dime y no pasa nada, retomamos cuando te venga bien."))
        return out("nudge", f"{n} day{'s' if n != 1 else ''} since last class", (
            f"Hola {name}, hoy bastan 10 minutos con Juno para seguir avanzando en tu inglés. "
            "Puedes hacerlo desde el móvil en cualquier hueco. "
            "Si hoy no te viene bien, dime a qué hora te va mejor."))
    if n <= 6:
        if ago is not None and ago < 3:
            return out("wait", "Next reminder around day 7")
        return out("back_3", f"{n} days away", (
            f"Hola {name}, hace {n} días que no practicas con Juno. "
            "Con 5 minutos hoy retomas el ritmo, tu historial y tus notas siguen guardados. "
            "Si prefieres otro día, dime cuál."))
    if n <= 13:
        if ago is not None and ago < 6:
            return out("wait", "Next reminder around day 14")
        return out("back_7", f"{n} days away", (
            f"Hola {name}, ha pasado una semana desde tu última clase con Juno. "
            "Te propongo una sesión corta de 10 minutos esta semana. "
            "Si hay algo que te frena, el horario o el tipo de práctica, dime y lo ajustamos."))
    if ago is not None and ago <= 30:
        return out("quiet", "Closing message already sent")
    return out("closing", f"{n} days away", (
        f"Hola {name}, no quiero llenarte de mensajes, así que este es el último por ahora. "
        f"Tu cuenta y tus notas siguen guardadas{where} por si quieres volver. "
        "Cuando quieras retomar, me escribes."))
