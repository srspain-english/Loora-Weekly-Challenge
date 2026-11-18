#!/usr/bin/env python3
"""Sending the practice-reminder emails.

Uses Resend's HTTP API with the standard library, so there is nothing extra to
install. Nothing here runs unless RESEND_API_KEY and JUNO_EMAIL_FROM are set;
until then the whole email feature stays switched off and invisible.

Environment:
  RESEND_API_KEY        the API key from resend.com
  JUNO_EMAIL_FROM       e.g.  Juno <juno@yourdomain.com>  (a domain verified in Resend)
  JUNO_EMAIL_REPLY_TO   optional: where replies go, usually the teacher
"""

from __future__ import annotations

import html
import json
import os
import re
import urllib.error
import urllib.request

API_URL = "https://api.resend.com/emails"

SUBJECTS = {
    "welcome": "Tu espacio con Juno está listo",
    "streak": "Tu racha con Juno",
    "nudge": "Tus 10 minutos de hoy con Juno",
    "back_3": "Tu práctica con Juno te espera",
    "back_7": "Una sesión corta esta semana",
    "closing": "Un último aviso de Juno",
}


class NotConfigured(Exception):
    pass


class SendFailed(Exception):
    pass


def configured() -> bool:
    return bool(os.environ.get("RESEND_API_KEY") and os.environ.get("JUNO_EMAIL_FROM"))


def deliver(payload: dict) -> None:
    """The only network call. Tests replace this."""
    request = urllib.request.Request(
        API_URL, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Authorization": "Bearer " + os.environ.get("RESEND_API_KEY", ""),
                 "Content-Type": "application/json", "User-Agent": "juno-reminders/1.0"})
    try:
        urllib.request.urlopen(request, timeout=20).read()
    except urllib.error.HTTPError as e:
        raise SendFailed(f"The email service refused the message ({e.code}).") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise SendFailed("Could not reach the email service.") from e


def _html(text: str) -> str:
    """Plain text made into simple HTML: paragraphs and clickable links."""
    paragraphs = []
    for block in text.split("\n\n"):
        safe = html.escape(block).replace("\n", "<br>")
        safe = re.sub(r"(https?://[^\s<]+)", r'<a href="\1">\1</a>', safe)
        paragraphs.append(f"<p>{safe}</p>")
    return ('<div style="font-family:system-ui,sans-serif;font-size:16px;line-height:1.5;'
            'color:#191714;max-width:520px">' + "".join(paragraphs) + "</div>")


def send(to: str, subject: str, text: str, unsubscribe_url: str | None = None) -> None:
    if not configured():
        raise NotConfigured("Email reminders are not switched on.")
    payload = {"from": os.environ["JUNO_EMAIL_FROM"], "to": [to], "subject": subject,
               "text": text, "html": _html(text)}
    reply_to = os.environ.get("JUNO_EMAIL_REPLY_TO", "").strip()
    if reply_to:
        payload["reply_to"] = reply_to
    if unsubscribe_url:
        payload["headers"] = {"List-Unsubscribe": f"<{unsubscribe_url}>",
                              "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    deliver(payload)


def confirmation(name: str, hour: int, confirm_url: str, stop_url: str) -> tuple[str, str]:
    text = (
        f"Hola {name}, para activar tus recordatorios de práctica con Juno confirma tu correo aquí:\n"
        f"{confirm_url}\n\n"
        f"Recibirás un correo como máximo al día, hacia las {hour:02d}:00, y solo si ese día no has practicado. "
        "Si no fuiste tú, ignora este mensaje y no recibirás nada.\n\n"
        f"Para no recibir más: {stop_url}")
    return "Confirma tus recordatorios de Juno", text


def reminder(kind: str, message: str, site_url: str, stop_url: str) -> tuple[str, str]:
    practise = f"\n\nPracticar ahora: {site_url}" if site_url else ""
    text = (f"{message}{practise}\n\n"
            "Recibes este correo porque pediste recordatorios de práctica con Juno. "
            f"Para no recibir más: {stop_url}")
    return SUBJECTS.get(kind, "Juno"), text
