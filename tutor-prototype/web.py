#!/usr/bin/env python3
"""
Juno — browser version, runnable locally or deployed (e.g. on Render).

Same tested logic as tutor.py (imported directly, not reimplemented) —
system prompt construction, correction tiers, report schema, student
memory — reached through a chat webpage instead of Terminal, with real
voice input/output in supporting browsers.

Local use (unchanged from before):
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 web.py
Opens http://127.0.0.1:8765 automatically. No passphrase needed.

Deployed use (e.g. Render): set environment variables
    ANTHROPIC_API_KEY        - required
    JUNO_ACCESS_PASSPHRASE   - required once this is reachable by anyone
                                other than you; gates every call
    PORT                     - set automatically by most hosts
When PORT is set in the environment, this binds to 0.0.0.0 instead of
127.0.0.1 (loopback-only) and skips auto-opening a browser, since that
only makes sense on your own machine.

Unauthenticated public deployments are a real risk: every message
spends your Anthropic API credit with no limit. Always set
JUNO_ACCESS_PASSPHRASE before sharing a deployed URL with anyone.
"""

from __future__ import annotations

import http.cookies
import json
import os
import secrets
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime, timezone
from html import escape as html_escape
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anthropic

import autoreminders  # the daily run that emails students who asked for it
import emailer  # sending those emails
import reminders  # what to say to a student who has gone quiet
import store  # identity, limits, saved calls, cost metrics
import tutor  # the exact tested logic: prompts, tiers, report schema

RUNNING_DEPLOYED = bool(os.environ.get("PORT"))
HOST = "0.0.0.0" if RUNNING_DEPLOYED else "127.0.0.1"
PORT = int(os.environ.get("PORT", 8765))
ACCESS_PASSPHRASE = os.environ.get("JUNO_ACCESS_PASSPHRASE", "")
MAX_FEEDBACK_LENGTH = 2000
# The teacher's private page for personal codes. Switched off unless set.
ADMIN_CODE = os.environ.get("JUNO_ADMIN_CODE", "")
FEEDBACK_PATH = tutor.DATA_DIR / "feedback.jsonl"
# A year: the point of this cookie is that a student stays the same person
# between classes without having to be given a code first.
STUDENT_COOKIE_MAX_AGE = 365 * 24 * 3600

# "Help me" during a class. Capped per class: each press is a paid request.
MAX_HELP_PER_CALL = 15
HELP_SYSTEM = (
    "A Spanish-speaking adult is in a live English conversation class with an "
    "AI teacher called Juno. They pressed 'Help me' because they don't know "
    "how to answer. Reply in plain text with no markdown, in under 90 words, "
    "in exactly this shape:\n"
    "First, one or two short sentences in Spanish saying what Juno just said "
    "or asked, explained simply.\n"
    "Then two lines, each starting with >> : a short answer in English the "
    "student could say, suited to CEFR level {level}, then || and its meaning "
    "in Spanish. For example:\n"
    ">> I went to the gym this morning. || Fui al gimnasio esta mañana.\n"
    "Don't correct the student and don't continue the conversation yourself."
)

# Tapping a word Juno said. Capped per day per browser: each tap is a paid
# request, and the same word in the same sentence is answered from memory.
MAX_LOOKUPS_PER_DAY = 80
DEFINE_CACHE: dict[tuple[str, str], dict] = {}
DEFINE_SYSTEM = (
    "A Spanish-speaking adult learning English tapped a word in something their "
    "English teacher said, to understand it. Reply in plain text with no "
    "markdown, in exactly two lines.\n"
    "Line 1: what it means in Spanish as used in this sentence, in a few words. "
    "If the word is part of a phrase (like 'make sense' or 'get up to speed'), "
    "give the whole phrase and its meaning.\n"
    "Line 2: one very short, simple English example sentence using it."
)

# Per-browser-session state, keyed by a random cookie value. Necessary as
# soon as more than one person can reach this server at once - a single
# global dict (fine for one local user) would let concurrent students
# overwrite each other's calls.
SESSIONS: dict[str, dict] = {}

# When a confirmation email last went to each student, so the form cannot be
# used to send someone a stream of emails.
REMINDER_CONFIRM_SENT: dict[str, float] = {}

# Juno as an installable web app ("Add to Home Screen"). The service worker is
# deliberately empty of caching: a class must never be served from a stale copy.
# It exists because phones only offer installation, and later notifications,
# to pages that have one.
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {
    "/icons/icon-192.png": ("icon-192.png", "image/png"),
    "/icons/icon-512.png": ("icon-512.png", "image/png"),
    "/icons/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    "/favicon.ico": ("icon-192.png", "image/png"),
}
MANIFEST = {
    "name": "Juno · S&R Spain",
    "short_name": "Juno",
    "description": "Your English practice space.",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#F6F2EA",
    "theme_color": "#F6F2EA",
    "icons": [
        {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
}
SERVICE_WORKER = """// Juno service worker: makes Juno installable. It keeps nothing offline.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});
"""

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#F6F2EA">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/icons/icon-192.png">
<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Juno">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<title>Juno Teaching Assistant</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@600;700&family=Instrument+Sans:wght@400;500;600&family=Instrument+Serif&display=swap');

  :root{
    color-scheme:light;
    --bg:#F6F2EA; --bg-glow:#FBF8F1; --surface:#FFFFFF; --surface-2:#EDE7DA;
    --line:#E4DDCE;
    --ink:#191714; --ink-soft:#5C554A; --muted:#8C857A;
    --accent:#5B2BD6; --accent-hi:#7A4BEE; --accent-tint:#F0ECFC; --accent-line:rgba(91,43,214,.4);
    --ok:#2E9E73; --warn:#B7791F; --bad:#C2553F;
    --brand:#FF4A1C;
  }
  /* The class itself stays dark, in purple. */
  body.in-call{
    color-scheme:dark;
    --bg:#0E0C16; --bg-glow:#241C3B; --surface:#1A1628; --surface-2:#252036;
    --line:rgba(255,255,255,.10);
    --ink:#F6F2EA; --ink-soft:#CFC9DD; --muted:#8B84A8;
    --accent:#7A4BEE; --accent-hi:#B79BFF; --accent-tint:rgba(122,75,238,.16); --accent-line:rgba(155,124,255,.4);
    --ok:#5FD0A0; --warn:#E3A33B; --bad:#FF7A6B;
  }
  *{box-sizing:border-box}
  [hidden]{display:none !important}
  html,body{margin:0}
  body{
    min-height:100vh;min-height:100dvh;color:var(--ink-soft);
    background-color:var(--bg);
    background-image:radial-gradient(120% 60% at 50% 0%, var(--bg-glow) 0%, var(--bg) 62%);
    background-attachment:fixed;
    font-family:'Instrument Sans',system-ui,sans-serif;font-size:15px;
  }
  h1,h2,h3{font-family:'Space Grotesk',sans-serif;color:var(--ink)}
  .app{
    max-width:480px;margin:0 auto;min-height:100vh;min-height:100dvh;
    display:flex;flex-direction:column;
    padding:20px 18px calc(20px + env(safe-area-inset-bottom));
  }

  /* Top bar and intro, on every screen except the class itself */
  .topbar{display:flex;align-items:center;gap:10px;font-size:12px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted)}
  .live-dot{width:7px;height:7px;border-radius:50%;background:var(--brand);box-shadow:0 0 10px var(--brand);flex-shrink:0}
  .brand-mark{color:var(--ink)}
  #feedback-btn{margin-left:auto;padding:7px 14px;font-size:12px;letter-spacing:.04em;text-transform:none}
  .lede{font-family:'Instrument Serif',Georgia,serif;color:var(--ink);font-size:30px;line-height:1.2;margin:26px 0 24px}
  body.in-call .topbar, body.in-call .lede{display:none}
  body.has-tabs .lede{display:none}
  body.has-tabs .app{padding-bottom:calc(96px + env(safe-area-inset-bottom))}
  body.in-call .app{height:100vh;height:100dvh;min-height:0;overflow:hidden}

  .panel{background:var(--surface);border:1px solid var(--line);border-radius:20px;padding:22px}
  label{display:block;font-size:12px;color:var(--muted);margin:16px 0 7px;text-transform:uppercase;letter-spacing:.08em}
  label:first-child{margin-top:0}
  .hint{text-transform:none;letter-spacing:0;color:var(--muted)}
  input[type=text], input[type=password], select, textarea{
    width:100%;padding:12px 14px;border:1px solid var(--line);border-radius:12px;
    font-family:inherit;font-size:16px;background:var(--surface-2);color:var(--ink);
  }
  input:focus, select:focus, textarea:focus{outline:2px solid var(--accent);outline-offset:1px}
  textarea{resize:vertical}
  input[type=checkbox]{accent-color:var(--accent);width:16px;height:16px}
  button{
    font-family:'Space Grotesk',sans-serif;font-weight:600;font-size:15px;cursor:pointer;
    border:none;border-radius:999px;padding:13px 22px;background:var(--accent);color:#fff;
  }
  button:disabled{opacity:.45;cursor:default}
  button.ghost{background:transparent;color:var(--ink-soft);border:1px solid var(--line)}
  button:focus-visible{outline:2px solid var(--accent-hi);outline-offset:3px}
  .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  .error{color:var(--bad);font-size:13.5px;margin-top:10px}
  .error:empty{display:none}

  #auth-screen{display:block}
  #today-screen, #practice-screen, #progress-screen, #notes-screen, #chat-screen, #report-screen{display:none}

  .status{display:flex;align-items:center;justify-content:center;gap:7px;font-size:12.5px;color:var(--muted);margin-top:10px;min-height:18px;text-align:center}
  .status .dot{width:7px;height:7px;border-radius:50%;background:var(--muted);flex-shrink:0}
  .status:has(#chat-status-text:empty) .dot, .status:has(#setup-status-text:empty) .dot{display:none}
  .status.waking .dot{background:var(--warn);animation:pulse 1.4s ease-in-out infinite}
  .status.offline .dot{background:var(--bad)}
  .status.offline{color:var(--ink-soft)}
  .status.saved .dot{background:var(--ok)}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}

  .notice{background:var(--accent-tint);border:1px solid var(--accent-line);border-radius:16px;padding:16px 18px;margin-bottom:16px;font-size:14px}
  .notice h2{font-size:15px;margin:0 0 6px}
  .notice .row{margin-top:12px}
  #feedback-panel{margin-bottom:18px}
  #feedback-thanks{color:var(--muted);font-size:13px;margin-top:8px}
  .privacy{margin-top:20px;padding-top:16px;border-top:1px solid var(--line);font-size:13px;color:var(--muted);line-height:1.55}
  .privacy summary{cursor:pointer;color:var(--ink-soft);font-weight:500}
  .privacy ul{margin:8px 0 0;padding-left:18px}
  .privacy li{margin-bottom:4px}

  /* The class */
  #chat-screen{flex:1;flex-direction:column;min-height:0}
  .call-top{display:flex;align-items:center;gap:10px;font-size:12px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);padding-top:4px}
  #call-clock{margin-left:auto;letter-spacing:.06em;font-variant-numeric:tabular-nums}
  .stage{display:flex;flex-direction:column;align-items:center;text-align:center;padding:clamp(14px,3.5vh,30px) 4px 4px;flex-shrink:0}
  .juno-line{
    font-family:'Instrument Serif',Georgia,serif;color:var(--ink);
    font-size:30px;line-height:1.22;margin:0 0 clamp(26px,4.5vh,40px);max-height:28vh;overflow-y:auto;
    min-height:1.22em;
  }
  .juno-line.long{font-size:24px;line-height:1.3}
  .juno-line.longer{font-size:20px;line-height:1.4}
  .juno-line.waiting{color:var(--muted)}
  .juno-line strong{font-weight:400;color:var(--accent-hi)}
  .line strong, #help-text strong{font-weight:600;color:var(--ink)}
  #mic-btn{
    --orb:clamp(118px,22vh,180px);
    position:relative;width:var(--orb);height:var(--orb);border-radius:50%;padding:0;flex-shrink:0;
    display:flex;align-items:center;justify-content:center;color:#fff;
    background:radial-gradient(70% 70% at 34% 28%, #B79BFF 0%, #7A4BEE 46%, #4A21C4 100%);
    box-shadow:0 20px 80px rgba(122,75,238,.5), inset 0 -12px 26px rgba(0,0,0,.18);
    transition:transform .15s ease, filter .3s ease;
  }
  #mic-btn::before{content:"";position:absolute;inset:-20px;border-radius:50%;border:1px solid rgba(155,124,255,.38);pointer-events:none}
  #mic-btn svg{width:26%;height:26%}
  #mic-btn:active{transform:scale(.97)}
  #mic-btn:disabled{filter:grayscale(.9) brightness(.55);opacity:1}
  #mic-btn.recording::after{
    content:"";position:absolute;inset:-14px;border-radius:50%;
    border:2px solid var(--accent-hi);animation:ring 1.4s ease-out infinite;
  }
  @keyframes ring{0%{transform:scale(.92);opacity:.9}100%{transform:scale(1.2);opacity:0}}
  #mic-btn.speaking{animation:breathe 2.4s ease-in-out infinite}
  @keyframes breathe{0%,100%{transform:scale(1)}50%{transform:scale(1.045)}}
  #mic-btn.thinking{animation:dim 1.6s ease-in-out infinite}
  @keyframes dim{0%,100%{filter:brightness(1)}50%{filter:brightness(.72)}}
  .orb-state{margin-top:clamp(24px,4vh,34px);font-size:14px;letter-spacing:.06em;color:var(--muted);min-height:20px}
  .heard{margin-top:6px;font-size:15px;color:var(--ink);min-height:22px;max-width:100%}

  /* Help or typing open: make room by shrinking the orb and Juno's line. */
  #chat-screen:has(#help-card:not([hidden])) #mic-btn, #chat-screen:has(#word-card:not([hidden])) #mic-btn, #chat-screen:has(#type-row:not([hidden])) #mic-btn{--orb:clamp(76px,12vh,116px)}
  #chat-screen:has(#help-card:not([hidden])) #mic-btn::before, #chat-screen:has(#word-card:not([hidden])) #mic-btn::before, #chat-screen:has(#type-row:not([hidden])) #mic-btn::before{inset:-12px}
  #chat-screen:has(#help-card:not([hidden])) .juno-line, #chat-screen:has(#word-card:not([hidden])) .juno-line, #chat-screen:has(#type-row:not([hidden])) .juno-line{font-size:22px;line-height:1.3;max-height:20vh;margin-bottom:22px}
  #chat-screen:has(#help-card:not([hidden])) .orb-state, #chat-screen:has(#word-card:not([hidden])) .orb-state, #chat-screen:has(#type-row:not([hidden])) .orb-state{margin-top:14px}
  .help-card{
    background:var(--surface-2);border:1px solid var(--accent-line);border-radius:16px;
    padding:14px 16px;margin-top:10px;font-size:15px;line-height:1.5;color:var(--ink);
    max-height:32vh;overflow-y:auto;flex-shrink:0;
  }
  .help-head{display:flex;align-items:center;justify-content:space-between;font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--accent-hi);margin-bottom:6px}
  #help-text{white-space:pre-line}
  .w{cursor:pointer;border-radius:5px;transition:background .15s ease}
  .w:hover, .w.picked{background:rgba(155,124,255,.28)}
  .tap-hint{margin:-18px 0 14px;font-size:12.5px;color:var(--muted);letter-spacing:.02em}
  .say-row{display:flex;align-items:flex-start;gap:10px;margin-top:10px;white-space:normal}
  .say-row .play-btn{width:36px;height:36px;font-size:13px;background:var(--accent);color:#fff;border:none}
  .say-row strong{display:block;color:var(--ink)}
  .say-row span{display:block;color:var(--ink-soft);font-size:14px}
  #word-text .example{margin-top:6px;color:var(--ink-soft);font-style:italic}

  .transcript{
    flex:1 1 0;min-height:0;overflow-y:auto;margin-top:10px;padding:4px 2px;
    display:flex;flex-direction:column;gap:14px;
    -webkit-mask-image:linear-gradient(to bottom, transparent 0, #000 40px);
    mask-image:linear-gradient(to bottom, transparent 0, #000 40px);
  }
  .transcript > :first-child{margin-top:auto}
  .line{display:grid;grid-template-columns:48px 1fr;gap:12px;font-size:15px;line-height:1.5}
  .line .who{font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);padding-top:4px}
  .line.juno .text{color:var(--ink-soft)}
  .line.you .text{color:var(--ink)}

  .type-row{display:flex;gap:8px;margin-top:12px}
  .type-row input[type=text]{flex:1;border-radius:999px;padding:12px 16px}
  .call-actions{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-top:12px}
  .pill{
    background:var(--surface-2);color:var(--ink);border:1px solid var(--line);border-radius:16px;
    padding:17px 8px;font-family:'Instrument Sans',sans-serif;font-weight:500;font-size:16px;
  }
  .pill.light{background:#F4EEE4;color:#121722;border-color:transparent}
  .call-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:12px;font-size:13px;color:var(--muted)}
  .call-settings select{width:auto;padding:6px 10px;font-size:13px;border-radius:10px}
  .call-settings{justify-content:space-between}
  .check-row{display:flex;align-items:flex-start;gap:8px;text-transform:none;letter-spacing:0;font-size:14px;color:var(--ink-soft);margin:0}
  .check-row input{margin-top:2px;flex-shrink:0}
  .foot-toggles{display:flex;gap:16px;flex-wrap:wrap;justify-content:flex-end}
  .call-foot label{margin:0;text-transform:none;letter-spacing:0;font-size:13px;display:flex;align-items:center;gap:6px;cursor:pointer}
  .link{background:none;border:none;border-radius:0;padding:4px 0;color:var(--ink-soft);font-family:inherit;font-weight:500;font-size:13px;text-decoration:underline;text-underline-offset:3px}

  /* Report */
  /* Session notes */
  #report-screen, #progress-screen, #notes-screen, #today-screen, #practice-screen{background:none;border:none;padding:4px 0 0}
  .eyebrow{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);margin:0 0 8px}
  .notes-title{font-family:'Instrument Serif',Georgia,serif;font-weight:400;font-size:40px;line-height:1.02;color:var(--ink);margin:0}
  .notes-title i{color:var(--accent-hi)}
  .notes-summary{margin:14px 0 0;color:var(--ink-soft);line-height:1.55}
  .notes-label{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);margin:30px 0 10px}
  .note-card{background:var(--surface);border:1px solid var(--line);border-radius:20px;padding:16px 18px;margin-bottom:10px}
  .note-card .said{margin:0;color:var(--muted);text-decoration:line-through;line-height:1.5}
  .note-card .better{margin:4px 0 0;color:var(--ink);font-weight:600;line-height:1.5}
  .note-card .why{margin:10px 0 0;font-size:13.5px;color:var(--ink-soft);line-height:1.5}
  .note-list{background:var(--surface-2);border-radius:20px;padding:2px 18px}
  .note-list > div{padding:14px 0;border-bottom:1px solid var(--line)}
  .note-list > div:last-child{border-bottom:none}
  .note-list strong{display:block;color:var(--ink);font-weight:600}
  .note-list span{display:block;margin-top:3px;font-size:13.5px;color:var(--ink-soft);line-height:1.5}
  .pron{display:flex;align-items:center;gap:14px}
  .play-btn{width:46px;height:46px;flex-shrink:0;border-radius:50%;padding:0;background:var(--surface-2);color:var(--ink);font-size:15px;border:1px solid var(--line)}
  .pron strong{display:block;color:var(--ink)}
  .pron span{display:block;margin-top:3px;font-size:13.5px;color:var(--ink-soft);line-height:1.5}
  .task-card{background:#191714;color:#F6F2EA;border-radius:22px;padding:20px;margin-top:30px}
  .task-card .eyebrow{color:#A8A096}
  .task-card h2{font-family:'Instrument Serif',Georgia,serif;font-weight:400;font-size:26px;line-height:1.2;color:#F6F2EA;margin:6px 0 0}
  .task-card ul{margin:12px 0 0;padding-left:20px;line-height:1.55;font-size:14.5px}
  .task-card button{width:100%;margin-top:18px}
  .stat-row{display:flex;gap:10px;margin-top:22px}
  .stat{flex:1;background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:14px}
  .stat b{display:block;font-family:'Space Grotesk',sans-serif;font-size:24px;color:var(--ink);margin-bottom:2px}
  .stat span{font-size:11.5px;color:var(--muted);line-height:1.3}
  .bars{display:flex;align-items:flex-end;gap:9px;height:120px;margin-bottom:8px}
  .bars .bar{flex:1;background:var(--surface-2);border:1px solid var(--line);border-radius:6px 6px 3px 3px;min-height:4px}
  .bars .bar.some{background:#C6B4F5;border-color:transparent}
  .bars .bar.today{background:var(--accent);border-color:transparent}
  .bar-days{display:flex;gap:9px;font-size:11px;color:var(--muted)}
  .bar-days span{flex:1;text-align:center}
  .note-card .meta{margin:0 0 6px;font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
  .note-card .body{margin:0;color:var(--ink);line-height:1.5}
  /* Tabs, Today, Practice, Notes */
  .tabs{position:fixed;left:0;right:0;bottom:0;z-index:50;display:none;background:rgba(246,242,234,.94);
    -webkit-backdrop-filter:blur(18px);backdrop-filter:blur(18px);border-top:1px solid var(--line);
    padding:8px 12px calc(8px + env(safe-area-inset-bottom))}
  body.has-tabs .tabs{display:block}
  .tabs-inner{display:flex;max-width:480px;margin:0 auto}
  .tabs button{flex:1;background:none;border-radius:12px;padding:6px 2px;display:flex;flex-direction:column;align-items:center;
    gap:4px;font-family:'Instrument Sans',sans-serif;font-size:10.5px;font-weight:500;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
  .tabs button.on{color:var(--accent)}
  .tabs svg{width:22px;height:22px}
  body.has-tabs .topbar{margin-bottom:20px}
  #scenario-row label{margin-top:16px}
  .hello-row{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:22px}
  .hello{font-family:'Instrument Serif',Georgia,serif;font-weight:400;font-size:36px;line-height:1.05;color:var(--ink);margin:0}
  .hello i{color:var(--accent)}
  .level-badge{display:flex;flex-direction:column;align-items:center;gap:3px;background:var(--surface-2);border:1px solid var(--line);
    border-radius:14px;padding:9px 12px;color:var(--ink)}
  .level-badge b{font-size:17px;color:var(--accent)}
  .level-badge span{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);font-weight:500}
  .mode-card{display:flex;align-items:center;gap:14px;width:100%;text-align:left;background:var(--surface);border:1px solid var(--line);
    border-radius:22px;padding:16px 18px;margin-bottom:10px;color:var(--ink);font-family:inherit;font-weight:400}
  .mode-card .ico{width:44px;height:44px;border-radius:50%;background:var(--surface-2);display:flex;align-items:center;justify-content:center;flex:none;color:var(--ink)}
  .mode-card .ico svg{width:20px;height:20px}
  .mode-card b{display:block;font-family:'Space Grotesk',sans-serif;font-size:16px;font-weight:600}
  .mode-card .d{display:block;font-size:13px;line-height:1.4;color:var(--muted);margin-top:2px}
  .mode-card .tick{margin-left:auto;width:22px;height:22px;border-radius:50%;border:1.5px solid var(--line);flex:none}
  .mode-card.on{background:var(--accent-tint);border-color:var(--accent)}
  .mode-card.on .ico{background:var(--accent);color:#fff}
  .mode-card.on .tick{background:var(--accent);border-color:var(--accent)}
  #today-screen .mode-card[data-mode="free"]{background:#191714;color:#F6F2EA;border-color:#191714}
  #today-screen .mode-card[data-mode="free"] .d{color:#C6BFB3}
  #today-screen .mode-card[data-mode="free"] .ico{background:var(--accent);color:#fff}
  .go{margin-left:auto;color:var(--muted);flex:none}
  #profile-card, #email-card{margin-bottom:16px}
  #profile-change{margin:0 0 16px}
  .level-card{background:#191714;color:#F6F2EA;border-radius:26px;padding:22px;margin-top:22px}
  .level-card .row-bands{display:flex;justify-content:space-between;align-items:baseline;gap:10px;margin-bottom:14px;font-weight:600}
  .level-card .row-bands small{font-weight:400;font-size:12px;color:#A8A096;text-align:center}
  .level-card .track{position:relative;height:10px;border-radius:5px;background:rgba(246,242,234,.14);margin-bottom:12px}
  .level-card .fill{position:absolute;left:0;top:0;bottom:0;border-radius:5px;background:linear-gradient(90deg,#5B2BD6,#9B7CFF)}
  .level-card .dot{position:absolute;top:-4px;width:18px;height:18px;border-radius:50%;background:#F6F2EA;border:4px solid #9B7CFF;transform:translateX(-50%)}
  .level-card p{margin:0;font-size:13px;color:#C6BFB3;line-height:1.45}
  .level-wait{background:var(--surface);border:1px solid var(--line);border-radius:22px;padding:18px;margin-top:22px;font-size:14px;line-height:1.5;color:var(--ink-soft)}
  .skill{margin-bottom:16px}
  .skill:last-child{margin-bottom:0}
  .skill-head{display:flex;justify-content:space-between;font-size:14px;color:var(--ink);margin-bottom:7px}
  .skill-head b{color:var(--accent)}
  .skill-bar{height:6px;border-radius:3px;background:var(--surface-2)}
  .skill-bar i{display:block;height:6px;border-radius:3px;background:var(--accent)}
  .note-open{display:block;width:100%;text-align:left;padding:16px 18px;font-family:inherit;font-weight:400;color:var(--ink);border-radius:20px}
  .tag{font-size:10px;text-transform:uppercase;color:var(--muted);font-family:ui-monospace,monospace}

  @media (prefers-reduced-motion:reduce){
    #mic-btn.recording::after, #mic-btn.speaking, #mic-btn.thinking, .status.waking .dot{animation:none}
  }
</style>
</head>
<body>
<div class="app">
  <div class="topbar">
    <span class="live-dot"></span><span class="brand-mark">Juno</span><span>· S&amp;R Spain</span>
    <button class="ghost" id="feedback-btn" style="display:none">Feedback</button>
  </div>
  <p class="lede" id="lede">Loading…</p>

  <div id="feedback-panel" class="panel" style="display:none">
    <label for="feedback-text">Tell us what's working or not</label>
    <textarea id="feedback-text" rows="3" placeholder="Anything you want Juno's teacher to know…"></textarea>
    <div class="row" style="margin-top:12px">
      <button id="feedback-submit">Send feedback</button>
      <button class="ghost" id="feedback-cancel">Cancel</button>
    </div>
    <div class="error" id="feedback-error"></div>
    <div id="feedback-thanks" style="display:none">Thanks — sent.</div>
  </div>

  <div id="auth-screen" class="panel">
    <label for="passphrase">Access code</label>
    <input type="password" id="passphrase" placeholder="Ask your teacher for the code">
    <div class="row" style="margin-top:16px"><button id="auth-btn">Enter</button></div>
    <div class="error" id="auth-error"></div>
  </div>

  <div id="today-screen">
    <p class="eyebrow" id="today-date"></p>
    <div class="hello-row">
      <h1 class="hello" id="hello">Hello</h1>
      <button class="level-badge" id="level-badge" aria-label="Change your level"><b id="level-badge-text">B1</b><span id="level-badge-note">Level</span></button>
    </div>

    <div id="profile-card" class="panel" hidden>
      <label for="student">Your name</label>
      <input type="text" id="student" placeholder="e.g. Maria">

      <label for="student-code">Your personal code <span class="hint">(optional — lets Juno recognise you on any device)</span></label>
      <input type="text" id="student-code" placeholder="Leave empty if your teacher hasn't given you one">
      <div class="row" style="margin-top:16px"><button id="profile-save">Continue</button></div>
      <div class="error" id="profile-error"></div>
    </div>
    <button class="link" id="profile-change" hidden>Not you? Change name or code</button>

    <div id="resume-notice" class="notice" style="display:none">
    <h2>You have an unfinished class</h2>
    <p id="resume-detail">Started earlier and never closed.</p>
    <div class="row">
      <button id="resume-btn">Continue it</button>
      <button class="ghost" id="resume-report-btn">Just get my report</button>
      <button class="ghost" id="resume-discard-btn">Discard</button>
    </div>
    </div>

    <div id="last-time" class="notice" hidden>
    <h2>Last time</h2>
    <p id="last-time-text"></p>
    <p id="last-time-next" style="margin-top:6px"></p>
    </div>

    <div id="install-card" class="notice" hidden>
      <h2>Keep Juno on your phone</h2>
      <p id="install-text"></p>
      <div class="row">
        <button id="install-btn" hidden>Install</button>
        <button class="ghost" id="install-dismiss">Not now</button>
      </div>
    </div>

    <div id="email-card" class="panel" hidden>
      <strong id="email-title">Practice reminders by email</strong>
      <p class="hint" style="margin:6px 0 0">One short email a day at the hour you choose, only on days you have not practised, Monday to Friday. You can stop any time.</p>
      <label for="email-address">Your email</label>
      <input type="text" id="email-address" inputmode="email" autocomplete="email" placeholder="you@example.com">
      <label for="email-hour">Hour</label>
      <select id="email-hour"></select>
      <label class="check-row" style="margin-top:14px"><input type="checkbox" id="email-consent"> Yes, send me practice reminders by email.</label>
      <div class="row" style="margin-top:14px">
        <button id="email-save">Turn on reminders</button>
        <button class="ghost" id="email-stop" hidden>Stop reminders</button>
      </div>
      <div class="error" id="email-error"></div>
      <div class="status" style="justify-content:flex-start" id="email-status"></div>
    </div>

    <p class="notes-label" style="margin-top:8px">Talk with Juno</p>
    <button class="mode-card" data-mode="free"><span class="ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg></span><span><b>Free talk</b><span class="d">Talk about anything. Juno follows you.</span></span><svg class="go" width="8" height="13" viewBox="0 0 8 13" aria-hidden="true"><path d="M1 1l6 5.5L1 12" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round"/></svg></button>
    <button class="mode-card" data-mode="business"><span class="ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="7" width="18" height="13" rx="2.5"/><path d="M8.5 7V4.5h7V7"/></svg></span><span><b>Business English</b><span class="d">Real work moments: meetings, updates, emails.</span></span><svg class="go" width="8" height="13" viewBox="0 0 8 13" aria-hidden="true"><path d="M1 1l6 5.5L1 12" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round"/></svg></button>
    <button class="mode-card" data-mode="structured"><span class="ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l2.4 5.4 5.6.8-4.1 4 1 5.8L12 16.3 7.1 19l1-5.8-4.1-4 5.6-.8z"/></svg></span><span><b>Structured class</b><span class="d">A step-by-step class with a focus for today.</span></span><svg class="go" width="8" height="13" viewBox="0 0 8 13" aria-hidden="true"><path d="M1 1l6 5.5L1 12" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round"/></svg></button>

    <div class="privacy">
      <details>
        <summary>What Juno saves about you</summary>
        <ul>
          <li><b>What it keeps:</b> your first name, your level, the transcript of each class, and the report at the end — corrections, vocabulary, and what to work on next.</li>
          <li><b>What it is for:</b> so Juno remembers you between classes and does not repeat what you already know. Nothing else.</li>
          <li><b>Who can see it:</b> you, and your teacher at S&amp;R Spain. It is not shared with anyone else and is not used to train anything.</li>
          <li><b>Please do not type confidential information</b> — real client names, personal data about colleagues, anything under NDA. Practise with the situation, not the specifics.</li>
          <li><b>Your email:</b> only if you ask for reminders. It is used for those reminders and nothing else, and every email has a link to stop them.</li>
          <li><b>Your phone number:</b> only if you gave it to your teacher. They use it to send you practice reminders on WhatsApp, nothing else. Ask and it is removed.</li>
          <li><b>To have your data deleted:</b> ask your teacher and it is removed.</li>
        </ul>
      </details>
    </div>
  </div>

  <div id="practice-screen">
    <p class="eyebrow">Before you begin</p>
    <h1 class="notes-title">Choose how<br><i>you want to talk.</i></h1>
    <div style="margin-top:22px">
    <button class="mode-card" data-mode="free"><span class="ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg></span><span><b>Free talk</b><span class="d">Talk about anything. Juno follows you.</span></span><span class="tick"></span></button>
    <button class="mode-card" data-mode="business"><span class="ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="7" width="18" height="13" rx="2.5"/><path d="M8.5 7V4.5h7V7"/></svg></span><span><b>Business English</b><span class="d">Real work moments: meetings, updates, emails.</span></span><span class="tick"></span></button>
    <button class="mode-card" data-mode="structured"><span class="ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l2.4 5.4 5.6.8-4.1 4 1 5.8L12 16.3 7.1 19l1-5.8-4.1-4 5.6-.8z"/></svg></span><span><b>Structured class</b><span class="d">A step-by-step class with a focus for today.</span></span><span class="tick"></span></button>
    </div>
    <select id="mode" hidden>
      <option value="free">Free Conversation</option>
      <option value="business">Business English</option>
      <option value="structured">Structured Class</option>
    </select>

    <div class="panel" style="margin-top:6px">
      <label for="level">Level</label>
      <select id="level">
        <option value="A2">A2 · Básico</option>
        <option value="B1" selected>B1 · Intermedio</option>
        <option value="B2">B2 · Intermedio alto</option>
        <option value="C1">C1 · Avanzado</option>
      </select>

      <div id="scenario-row" style="display:none">
        <label for="scenario">Scenario <span class="hint">(optional — random if left as "Surprise me")</span></label>
        <select id="scenario"><option value="">Surprise me</option></select>
      </div>
    </div>

    <label class="check-row" style="margin-top:14px"><input type="checkbox" id="easier"> Easier English <span class="hint">— shorter sentences and simpler words from Juno</span></label>

    <div class="row" style="margin-top:18px">
      <button id="start-btn" style="flex:1">Begin conversation →</button>
    </div>
    <div class="error" id="start-error"></div>
    <div class="status" id="setup-status"><span class="dot"></span><span id="setup-status-text"></span></div>
  </div>

  <div id="progress-screen">
    <div id="progress-body"></div>
  </div>

  <div id="notes-screen">
    <p class="eyebrow">Your notes</p>
    <h1 class="notes-title">Every class,<br><i>kept for you.</i></h1>
    <div id="notes-list" style="margin-top:22px"></div>
  </div>

  <div id="chat-screen">
    <div class="call-top">
      <span class="live-dot"></span><span id="call-label">Juno · Free talk</span>
      <span id="call-clock">0:00</span>
    </div>

    <div class="stage">
      <p class="juno-line" id="juno-line" aria-live="polite"></p>
      <p class="tap-hint" id="tap-hint" hidden>Toca cualquier palabra para ver qué significa</p>
      <button id="mic-btn" aria-label="Tap to talk" title="Tap to talk">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/></svg>
      </button>
      <div class="orb-state" id="orb-state">Tap to talk</div>
      <div class="heard" id="heard"></div>
    </div>

    <div class="help-card" id="help-card" hidden>
      <div class="help-head"><span>Ayuda</span><button class="link" id="help-close">Cerrar</button></div>
      <div id="help-text"></div>
    </div>

    <div class="help-card" id="word-card" hidden>
      <div class="help-head"><span id="word-title"></span><button class="link" id="word-close">Cerrar</button></div>
      <div id="word-text"></div>
    </div>

    <div class="transcript" id="chat-log"></div>

    <div class="type-row" id="type-row" hidden>
      <input type="text" id="msg-input" placeholder="Type your reply…">
      <button id="send-btn">Send</button>
    </div>
    <div class="status" id="chat-status"><span class="dot"></span><span id="chat-status-text"></span></div>
    <div class="error" id="chat-error"></div>

    <div class="call-actions">
      <button class="pill" id="pause-btn">Pause</button>
      <button class="pill" id="help-btn">Help me</button>
      <button class="pill light" id="end-btn">End</button>
    </div>
    <div class="call-foot call-settings">
      <label title="How fast Juno speaks">Speed
        <select id="voice-speed"><option value="slow">Slow</option><option value="normal" selected>Normal</option><option value="fast">Fast</option></select>
      </label>
      <label title="How long Juno waits when you pause">Wait
        <select id="patience"><option value="normal">4 seconds</option><option value="long">8 seconds</option><option value="tap">Until I tap</option></select>
      </label>
    </div>
    <div class="call-foot">
      <button class="link" id="type-toggle">Type instead</button>
      <div class="foot-toggles">
        <label><input type="checkbox" id="handsfree-toggle" checked> Hands-free</label>
        <label><input type="checkbox" id="speak-toggle" checked> Juno speaks</label>
      </div>
    </div>
  </div>

  <div id="report-screen">
    <div id="recap"></div>
    <div class="task-card">
      <p class="eyebrow">Task for your next conversation</p>
      <h2 id="next-task"></h2>
      <ul id="homework"></ul>
      <button id="again-btn">Start a new class</button>
    </div>
  </div>
</div>

<nav class="tabs" id="tabs" aria-label="Sections"><div class="tabs-inner">
  <button data-tab="today"><svg viewBox="0 0 21 21" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round" aria-hidden="true"><path d="M2.5 8.5L10.5 2l8 6.5V18a1 1 0 0 1-1 1h-14a1 1 0 0 1-1-1V8.5z"/></svg>Today</button>
  <button data-tab="practice"><svg viewBox="0 0 21 21" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><rect x="7" y="1.5" width="7" height="11" rx="3.5"/><path d="M4 11a6.5 6.5 0 0 0 13 0M10.5 17.5v2"/></svg>Practice</button>
  <button data-tab="progress"><svg viewBox="0 0 21 21" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" aria-hidden="true"><path d="M3 17V9M9 17V4M15 17v-6"/></svg>Progress</button>
  <button data-tab="notes"><svg viewBox="0 0 21 21" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" aria-hidden="true"><path d="M2 4.5c3-1.5 6-1.2 8.5 1v12c-2.5-2.2-5.5-2.5-8.5-1zM19 4.5c-3-1.5-6-1.2-8.5 1v12c2.5-2.2 5.5-2.5 8.5-1z"/></svg>Notes</button>
</div></nav>

<script>
const $ = (id) => document.getElementById(id);
let scenarios = null;
let recognition = null;
let recognitionActive = false;
let voiceAvailable = false;
let paused = false;
let micBusy = false;
let cancelListening = () => {};
let startListening = () => {};
let autoListenBlocked = false;
let autoTimer = null;

// Three failures look identical to a student and need different words:
// the free tier waking up (wait), no connection (check your wifi), and a
// real error (tell your teacher). ApiError carries which one it was.
class ApiError extends Error {
  constructor(message, kind) { super(message); this.kind = kind; }
}

// Render idles the free instance out; the first request after that can take
// most of a minute. Anything slower than this is worth telling the student
// about rather than leaving them looking at a dead button.
const WAKING_AFTER_MS = 3000;

async function api(path, body, opts) {
  const onWaking = (opts || {}).onWaking;
  const timer = onWaking ? setTimeout(onWaking, WAKING_AFTER_MS) : null;
  let res;
  try {
    res = await fetch(path, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body || {}),
      credentials: 'same-origin',
    });
  } catch (e) {
    // fetch only rejects for network-level failures, never for a 4xx/5xx.
    throw new ApiError(
      navigator.onLine === false
        ? 'You appear to be offline. Your class is saved — reconnect and continue.'
        : "Couldn't reach Juno. Check your connection and try again — your class is saved.",
      'network');
  } finally {
    if (timer) clearTimeout(timer);
  }

  let data;
  try {
    data = await res.json();
  } catch (e) {
    throw new ApiError('Juno sent something unexpected. Please try again.', 'server');
  }
  if (!res.ok) {
    throw new ApiError(data.error || 'Something went wrong.',
                       res.status === 429 ? 'limit' : 'server');
  }
  return data;
}

function setStatus(el, text, kind) {
  const box = $(el), label = $(el + '-text');
  if (!box || !label) return;
  box.className = 'status' + (kind ? ' ' + kind : '');
  label.textContent = text || '';
}

let lastSavedAt = null;
function showSaved(iso) {
  if (iso) lastSavedAt = new Date(iso);
  if (!lastSavedAt) return;
  const secs = Math.round((Date.now() - lastSavedAt.getTime()) / 1000);
  const when = secs < 10 ? 'just now'
    : secs < 90 ? `${secs}s ago`
    : `${Math.round(secs / 60)} min ago`;
  setStatus('chat-status', `Saved ${when}`, 'saved');
}
setInterval(() => { if (lastSavedAt && $('chat-status').className.includes('saved')) showSaved(); }, 15000);

const TAB_OF = { 'today-screen': 'today', 'practice-screen': 'practice', 'progress-screen': 'progress',
                 'notes-screen': 'notes', 'report-screen': 'notes' };
const SCREENS = ['auth-screen', 'today-screen', 'practice-screen', 'progress-screen', 'notes-screen',
                 'chat-screen', 'report-screen'];

function show(id) {
  SCREENS.forEach((s) => {
    $(s).style.display = s !== id ? 'none' : (s === 'chat-screen' ? 'flex' : 'block');
  });
  document.body.classList.toggle('in-call', id === 'chat-screen');
  document.body.classList.toggle('has-tabs', id in TAB_OF);
  document.querySelectorAll('#tabs button').forEach((btn) => {
    btn.classList.toggle('on', btn.dataset.tab === TAB_OF[id]);
  });
  const theme = document.querySelector('meta[name=theme-color]');
  if (theme) theme.content = id === 'chat-screen' ? '#0E0C16' : '#F6F2EA';
}

// --- The class screen ------------------------------------------------------
// Juno's newest line is shown large above the mic; everything before it
// goes into the transcript underneath.

let junoLine = '';

// Juno sometimes marks a correction with **double asterisks**. On screen
// that part is highlighted; out loud the symbols are dropped, or the
// voice reads them as "asterisk".
function isWordChar(ch) {
  return ch.toLowerCase() !== ch.toUpperCase() || ch === "'" || ch === '’' || ch === '-';
}

// Each word becomes something the student can tap to see what it means.
function appendWords(parent, text) {
  let word = '';
  let rest = '';
  const flushWord = () => {
    if (!word) return;
    if (word.split('').some((c) => c.toLowerCase() !== c.toUpperCase())) {
      const span = document.createElement('span');
      span.className = 'w';
      span.textContent = word;
      parent.appendChild(span);
    } else {
      parent.appendChild(document.createTextNode(word));
    }
    word = '';
  };
  const flushRest = () => { if (rest) { parent.appendChild(document.createTextNode(rest)); rest = ''; } };
  for (const ch of text) {
    if (isWordChar(ch)) { flushRest(); word += ch; } else { flushWord(); rest += ch; }
  }
  flushWord();
  flushRest();
}

function fillRich(el, text, tappable) {
  el.textContent = '';
  text.split('**').forEach((part, i) => {
    const clean = part.replace(/[*#`]/g, '');
    const target = i % 2 ? document.createElement('strong') : el;
    if (tappable) appendWords(target, clean); else target.appendChild(document.createTextNode(clean));
    if (target !== el) el.appendChild(target);
  });
}
function speakable(text) {
  return text.replace(/[*#`]/g, '').replace(/_{2,}/g, ' blank ');
}

function addLine(who, text) {
  const row = document.createElement('div');
  row.className = 'line ' + who;
  const name = document.createElement('span');
  name.className = 'who';
  name.textContent = who === 'juno' ? 'Juno' : 'You';
  const body = document.createElement('span');
  body.className = 'text';
  fillRich(body, text, who === 'juno');
  row.append(name, body);
  const log = $('chat-log');
  log.appendChild(row);
  log.scrollTop = log.scrollHeight;
}

function setJunoLine(text, waiting) {
  const el = $('juno-line');
  fillRich(el, text, !waiting);
  el.classList.toggle('long', text.length > 110 && text.length <= 220);
  el.classList.toggle('longer', text.length > 220);
  el.classList.toggle('waiting', !!waiting);
  el.scrollTop = 0;
}

function bubble(who, text) {
  if (who === 'juno') {
    if (junoLine) addLine('juno', junoLine);
    junoLine = text;
    setJunoLine(text);
  } else {
    if (junoLine) { addLine('juno', junoLine); junoLine = ''; }
    addLine('you', text);
  }
}

function clearChat() {
  $('chat-log').innerHTML = '';
  junoLine = '';
  setJunoLine('');
  $('heard').textContent = '';
  $('help-card').hidden = true;
  $('chat-error').textContent = '';
}

function setOrb(state, label) {
  const b = $('mic-btn');
  b.classList.toggle('recording', state === 'listening');
  b.classList.toggle('speaking', state === 'speaking');
  b.classList.toggle('thinking', state === 'thinking');
  $('orb-state').textContent = label;
}
function idleOrb() {
  if (paused) { setOrb('idle', 'Paused'); return; }
  setOrb('idle', voiceAvailable ? 'Your turn — tap to talk' : 'Your turn — type your answer below');
}

const MODE_NAMES = { free: 'Free talk', business: 'Business English', structured: 'Structured class' };
let clockStart = 0, clockPausedTotal = 0, pausedAt = 0, clockTimer = null;
function drawClock() {
  const now = pausedAt || Date.now();
  const s = Math.max(0, Math.floor((now - clockStart - clockPausedTotal) / 1000));
  $('call-clock').textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}
function startClass(mode) {
  $('call-label').textContent = 'Juno · ' + (MODE_NAMES[mode] || MODE_NAMES.free);
  paused = false;
  $('pause-btn').textContent = 'Pause';
  $('mic-btn').disabled = false;
  $('help-btn').disabled = false;
  $('type-row').hidden = voiceAvailable;
  $('type-toggle').textContent = voiceAvailable ? 'Type instead' : 'Hide typing';
  clockStart = Date.now(); clockPausedTotal = 0; pausedAt = 0;
  clearInterval(clockTimer);
  clockTimer = setInterval(drawClock, 1000);
  drawClock();
  $('word-card').hidden = true;
  let tapped = false;
  try { tapped = localStorage.getItem('juno_tapped_word') === '1'; } catch (e) { tapped = false; }
  $('tap-hint').hidden = tapped;
  show('chat-screen');
  idleOrb();
}
function stopClass() {
  clearInterval(clockTimer);
  clockTimer = null;
  paused = false;
  if (window.speechSynthesis) window.speechSynthesis.cancel();
}

function openTyping() {
  if (micBusy) { cancelListening(); idleOrb(); }
  $('type-row').hidden = false;
  $('type-toggle').textContent = 'Hide typing';
  $('msg-input').focus();
}
$('type-toggle').onclick = () => {
  if ($('type-row').hidden) { openTyping(); return; }
  $('type-row').hidden = true;
  $('type-toggle').textContent = 'Type instead';
  autoListen();
};

// Hands-free: once Juno has finished, the mic switches on by itself. If the
// browser won't allow that (or the student says nothing), it quietly falls
// back to tapping - a tap always works.
const HANDSFREE_KEY = 'juno-handsfree';
try {
  if (localStorage.getItem(HANDSFREE_KEY) === 'off') $('handsfree-toggle').checked = false;
} catch (e) { /* storage unavailable: hands-free stays on */ }
$('handsfree-toggle').onchange = () => {
  const on = $('handsfree-toggle').checked;
  try { localStorage.setItem(HANDSFREE_KEY, on ? 'on' : 'off'); } catch (e) { /* not remembered */ }
  if (on) { autoListen(); return; }
  clearTimeout(autoTimer);
  if (micBusy) { cancelListening(); idleOrb(); }
};
function autoListen(delay) {
  clearTimeout(autoTimer);
  // The short wait keeps the mic from catching the tail of Juno's own voice.
  autoTimer = setTimeout(() => {
    if (!$('handsfree-toggle').checked || autoListenBlocked || !voiceAvailable) return;
    if (!classInProgress || paused || sending || micBusy || helpLoading) return;
    if (!$('type-row').hidden || $('end-btn').disabled) return;
    if (!document.body.classList.contains('in-call')) return;
    if (window.speechSynthesis && window.speechSynthesis.speaking) return;
    startListening(true);
  }, delay === undefined ? 400 : delay);
}

// Named voices known to sound natural rather than robotic, checked in order.
// Covers Chrome/Edge (cloud voices) and Safari/macOS (Enhanced/Premium voices).
const PREFERRED_VOICE_NAMES = [
  'Google US English',
  'Samantha',
  'Ava',
  'Allison',
  'Susan',
  'Nicky',
  'Alex',
  'Tom',
  'Microsoft Aria Online (Natural) - English (United States)',
  'Microsoft Jenny Online (Natural) - English (United States)',
];

// macOS ships a set of joke/novelty voices that getVoices() returns right
// alongside the real ones - several of them sort near the top of the list.
// Falling through to "first English voice" therefore lands on something like
// Albert or Zarvox, which is where the robotic Stephen-Hawking sound came
// from on a Mac, in Safari and Brave alike.
const NOVELTY_VOICE_NAMES = new Set([
  'Albert', 'Bad News', 'Bahh', 'Bells', 'Boing', 'Bubbles', 'Cellos',
  'Deranged', 'Good News', 'Jester', 'Junior', 'Kathy', 'Organ',
  'Pipe Organ', 'Princess', 'Ralph', 'Superstar', 'Trinoids', 'Whisper',
  'Wobble', 'Zarvox', 'Fred', 'Hysterical', 'Bruce',
]);

function isUsableVoice(v) {
  return v && v.lang && v.lang.startsWith('en') && !NOVELTY_VOICE_NAMES.has(v.name);
}

let cachedVoices = [];
function refreshVoices() { cachedVoices = window.speechSynthesis.getVoices(); }
if (window.speechSynthesis) {
  refreshVoices();
  // Chrome loads voices asynchronously - the list above is often empty
  // until this fires, which is why the very first reply can sound worse
  // than later ones if we don't wait for it.
  window.speechSynthesis.onvoiceschanged = refreshVoices;
}

function pickVoice(voices) {
  for (const name of PREFERRED_VOICE_NAMES) {
    // Match the base name too: macOS lists these as "Samantha (Enhanced)"
    // once the better-quality version has been downloaded.
    const match = voices.find((v) => v.name === name || v.name.startsWith(name + ' ('));
    if (match) return match;
  }
  const enhanced = voices.find((v) => isUsableVoice(v) && /enhanced|premium|natural/i.test(v.name));
  if (enhanced) return enhanced;
  const cloud = voices.find((v) => isUsableVoice(v) && v.localService === false);
  if (cloud) return cloud;
  const systemDefault = voices.find((v) => isUsableVoice(v) && v.default);
  if (systemDefault) return systemDefault;
  return voices.find(isUsableVoice) || null;
}

const PREFS_KEY = 'juno_prefs';
const VOICE_RATES = { slow: 0.75, normal: 0.9, fast: 1 };

function loadPrefs() {
  try { return JSON.parse(localStorage.getItem(PREFS_KEY)) || {}; } catch (e) { return {}; }
}
function savePrefs() {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({
      speed: $('voice-speed').value, wait: $('patience').value, easier: $('easier').checked,
    }));
  } catch (e) { /* private window: settings last until the tab closes */ }
}
(function restorePrefs() {
  const saved = loadPrefs();
  if (VOICE_RATES[saved.speed]) $('voice-speed').value = saved.speed;
  if (['normal', 'long', 'tap'].includes(saved.wait)) $('patience').value = saved.wait;
  $('easier').checked = !!saved.easier;
  ['voice-speed', 'patience', 'easier'].forEach((id) => $(id).addEventListener('change', savePrefs));
})();

function speak(text) {
  if (!$('speak-toggle').checked || !window.speechSynthesis || paused) return false;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(speakable(text));
  u.lang = 'en-US';
  u.rate = VOICE_RATES[$('voice-speed').value] || VOICE_RATES.normal;
  u.pitch = 1;
  const voice = pickVoice(cachedVoices.length ? cachedVoices : window.speechSynthesis.getVoices());
  if (voice) u.voice = voice;
  u.onstart = () => { if (!paused && !micBusy) setOrb('speaking', 'Juno is speaking'); };
  u.onend = u.onerror = () => {
    if (micBusy || sending) return;
    idleOrb();
    autoListen();
  };
  window.speechSynthesis.speak(u);
  return true;
}
$('speak-toggle').onchange = () => {
  if (!$('speak-toggle').checked && window.speechSynthesis) window.speechSynthesis.cancel();
};

// --- Installing Juno on the home screen ----------------------------------------
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => { navigator.serviceWorker.register('/sw.js').catch(() => {}); });
}
const IS_IPHONE = /iPad|iPhone|iPod/.test(navigator.userAgent)
  || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
const IS_STANDALONE = window.navigator.standalone === true
  || (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches);
// Safari's speech recognition has been unreliable inside a home-screen icon.
const STANDALONE_NOTE = IS_IPHONE && IS_STANDALONE
  ? ' On iPhone, voice may not work inside the home-screen icon. Open Juno in Safari to talk, or type.' : '';
let installPrompt = null;
window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); installPrompt = e; showInstallCard(); });

function showInstallCard() {
  let dismissed = false;
  try { dismissed = localStorage.getItem('juno_install_dismissed') === '1'; } catch (e) { dismissed = false; }
  if (IS_STANDALONE || dismissed) return;
  if (installPrompt) {
    $('install-text').textContent = 'Open Juno like an app, straight from your home screen.';
    $('install-btn').hidden = false;
  } else if (IS_IPHONE) {
    $('install-text').textContent = 'Tap the Share button at the bottom of your screen, then “Add to Home Screen”.';
  } else {
    return;
  }
  $('install-card').hidden = false;
}
$('install-btn').onclick = async () => {
  if (!installPrompt) return;
  installPrompt.prompt();
  try { await installPrompt.userChoice; } catch (e) { /* closed without choosing */ }
  installPrompt = null;
  $('install-card').hidden = true;
};
$('install-dismiss').onclick = () => {
  $('install-card').hidden = true;
  try { localStorage.setItem('juno_install_dismissed', '1'); } catch (e) { /* asked again next visit */ }
};

let voiceSetUp = false;
function setupVoiceInput() {
  if (voiceSetUp) return;
  voiceSetUp = true;
  const micBtn = $('mic-btn');
  const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
  let voiceError = null;
  let heard = '';
  let attempt = 0;
  let timers = [];
  let silenceTimer = null;
  let attemptAuto = false;

  // Safari can accept start() and then never report anything, or hear the
  // words and never say it has finished. These limits stop either from
  // looking like a dead button.
  const START_MS = 4000;    // not listening by then: it didn't start
  const SILENCE_MS = 4000;  // no new words for this long: the student has finished
  const NOTHING_MS = 10000; // not a single word by then: stop quietly
  const END_MS = 1500;      // asked to stop but never ended: end it ourselves
  const MAX_MS = 60000;     // never listen longer than this
  // How long Juno waits after the last word before deciding the student has
  // finished. "Until I tap" never decides for them.
  const PATIENCE = { normal: SILENCE_MS, long: 8000, tap: Infinity };
  const patienceMs = () => PATIENCE[$('patience').value] || SILENCE_MS;
  let carried = '';      // words from earlier pieces of the same answer
  let lastWordAt = 0;
  let stopping = false;  // we asked it to stop: do not stitch another piece on
  let stitchedAt = 0;
  let stitches = 0;

  const later = (ms, fn) => {
    const mine = attempt;
    timers.push(setTimeout(() => { if (mine === attempt && micBusy) fn(); }, ms));
  };
  const clearTimers = () => {
    timers.forEach(clearTimeout);
    timers = [];
    clearTimeout(silenceTimer);
  };
  const askToStop = () => {
    stopping = true;
    try { recognition.stop(); } catch (err) { /* already stopped */ }
    later(END_MS, () => finish());
  };

  function finish(reason) {
    if (!micBusy) return;
    micBusy = false;
    recognitionActive = false;
    stopping = true;
    clearTimers();
    $('heard').textContent = '';
    if (paused) { idleOrb(); return; }
    const said = heard.trim();
    heard = '';
    carried = '';
    const problem = voiceError || reason;
    if (!said && problem && problem !== 'aborted' && attemptAuto) {
      // A hands-free attempt that came to nothing stays quiet; the student
      // can still tap. If this browser won't start the mic on its own, stop
      // trying for the rest of the class.
      if (['no-start', 'not-allowed', 'service-not-allowed', 'audio-capture'].includes(problem)) {
        autoListenBlocked = true;
      }
      idleOrb();
      return;
    }
    if (!said && problem && problem !== 'aborted') {
      idleOrb();
      const why = {
        'no-speech': "I didn't hear anything. Tap the mic and speak.",
        'not-allowed': 'The microphone is blocked for this site. Allow it in your browser settings, or type instead.',
        'service-not-allowed': 'This browser is not letting the site use speech recognition. Type instead.',
        'audio-capture': 'No microphone found. Type instead.',
        'network': 'Voice input needs an internet connection. Type instead.',
        'no-start': "The microphone didn't start in this browser. Check it is allowed to use the microphone, or type instead.",
      }[problem] || 'Voice input did not work. Type instead.';
      setStatus('chat-status', `${why} (${problem})${STANDALONE_NOTE}`, 'offline');
      if (problem !== 'no-speech') openTyping();
      return;
    }
    if (!said) { idleOrb(); return; }
    $('msg-input').value = said;
    // With the typing box open the student may want to fix the words first;
    // otherwise what they said goes straight to Juno, like a real call.
    if ($('type-row').hidden) sendMessage(); else { idleOrb(); $('msg-input').focus(); }
  }

  cancelListening = () => {
    if (!micBusy) return;
    micBusy = false;
    recognitionActive = false;
    stopping = true;
    heard = '';
    carried = '';
    clearTimers();
    $('heard').textContent = '';
    try { recognition.abort(); } catch (err) { /* already stopped */ }
  };

  // A plain click, not touchstart: the HTML standard only counts a finished
  // tap (touchend) or a click as the user really acting, and Safari is
  // strict about that for the microphone.
  micBtn.addEventListener('click', (e) => {
    e.preventDefault();
    if (paused || sending) return;
    if (!recognition) {
      openTyping();
      setStatus('chat-status', "Voice isn't available in this browser. Type your answer instead." + STANDALONE_NOTE, 'offline');
      return;
    }
    if (micBusy) { askToStop(); return; }  // tap again to stop
    clearTimeout(autoTimer);
    startListening(false);
  });

  startListening = (auto) => {
    if (!recognition || micBusy) return;
    if (window.speechSynthesis && window.speechSynthesis.speaking) window.speechSynthesis.cancel();
    attempt += 1;
    micBusy = true;
    attemptAuto = auto;
    voiceError = null;
    heard = '';
    carried = '';
    stopping = false;
    stitches = 0;
    stitchedAt = 0;
    $('heard').textContent = '';
    // Respond straight away, before the browser confirms it is listening.
    setOrb('listening', auto ? 'Listening… just speak' : 'Starting the microphone…');
    try {
      recognition.start();
    } catch (err) {
      finish('no-start');
      return;
    }
    later(START_MS, () => {
      if (recognitionActive) return;
      try { recognition.abort(); } catch (err) { /* never started */ }
      finish('no-start');
    });
    later(NOTHING_MS, () => {
      if (heard.trim()) return;
      voiceError = 'no-speech';
      try { recognition.abort(); } catch (err) { /* already stopped */ }
      finish();
    });
    later(MAX_MS, askToStop);
  };

  if (!SpeechRec) return;  // no speech recognition here: the mic opens typing instead
  voiceAvailable = true;
  recognition = new SpeechRec();
  recognition.lang = 'en-US';
  // Chrome and Edge, left to themselves, decide the student has finished at
  // the first short pause - about a second - and send half a sentence while
  // they look for the next English word. Their "keep listening" mode doesn't
  // stop at pauses, so there Juno decides instead: SILENCE_MS without a new
  // word. Safari's version of that mode is broken (results can stop arriving
  // entirely), so Safari and every iPhone browser keep single-sentence mode.
  const keepListening = !!window.chrome && !/iPad|iPhone|iPod/.test(navigator.userAgent);
  recognition.continuous = keepListening;
  // Words appear while the student speaks, so they can see it is working.
  recognition.interimResults = true;
  recognition.maxAlternatives = 1;

  recognition.onstart = () => {
    if (!micBusy) return;
    recognitionActive = true;
    setOrb('listening', attemptAuto ? 'Listening… just speak' : 'Listening…');
  };
  recognition.onresult = (e) => {
    if (!micBusy) return;
    recognitionActive = true;
    let text = '';
    for (let i = 0; i < e.results.length; i++) text += e.results[i][0].transcript;
    heard = carried ? carried + ' ' + text : text;
    $('heard').textContent = heard;
    if (text.trim()) { lastWordAt = Date.now(); setOrb('listening', 'Listening… tap when you’re done'); }
    clearTimeout(silenceTimer);
    const wait = patienceMs();
    if (Number.isFinite(wait)) silenceTimer = setTimeout(() => { if (micBusy) askToStop(); }, wait);
  };
  recognition.onerror = (e) => { voiceError = e.error; };
  // Safari and iPhone end listening by themselves at the first short pause.
  // If the student has been talking, the browser stopping is not the end of
  // their answer: start listening again, keep what was heard, and let the
  // wait time decide. If the browser refuses to restart, what was heard so
  // far is sent, which is how it always worked.
  function stitch() {
    const now = Date.now();
    // An engine that keeps ending at once with nothing new is not going to
    // work; send what there is rather than spin.
    if ((now - stitchedAt < 700 && heard.trim() === carried) || stitches >= 40) { finish(); return; }
    stitchedAt = now;
    stitches += 1;
    recognitionActive = false;
    carried = heard.trim();
    voiceError = null;
    clearTimeout(silenceTimer);
    const wait = patienceMs() - (now - lastWordAt);
    if (Number.isFinite(wait)) {
      if (wait <= 0) { finish(); return; }
      silenceTimer = setTimeout(() => { if (micBusy) askToStop(); }, wait);
    }
    try { recognition.start(); } catch (err) { finish(); return; }
    later(START_MS, () => { if (!recognitionActive) finish(); });
  }

  recognition.onend = () => {
    const fatal = voiceError && !['no-speech', 'aborted'].includes(voiceError);
    if (micBusy && !stopping && !paused && !fatal && heard.trim()) { stitch(); return; }
    finish();
  };
}

// --- Pause and Help me -----------------------------------------------------

$('pause-btn').onclick = () => {
  paused = !paused;
  if (paused) {
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    cancelListening();
    pausedAt = Date.now();
    drawClock();
    $('pause-btn').textContent = 'Resume';
    $('mic-btn').disabled = true;
    $('help-btn').disabled = true;
  } else {
    clockPausedTotal += Date.now() - pausedAt;
    pausedAt = 0;
    $('pause-btn').textContent = 'Pause';
    $('mic-btn').disabled = false;
    $('help-btn').disabled = false;
  }
  idleOrb();
  if (!paused) autoListen(300);
};

let helpLoading = false;
// Say something once, for the student to hear and repeat. Unlike Juno's turn,
// this never opens the microphone afterwards.
function sayAloud(text) {
  if (!window.speechSynthesis) return;
  if (micBusy) { cancelListening(); idleOrb(); }
  clearTimeout(autoTimer);
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(speakable(text));
  u.lang = 'en-US';
  u.rate = Math.min(VOICE_RATES[$('voice-speed').value] || VOICE_RATES.normal, 0.85);
  const voice = pickVoice(cachedVoices.length ? cachedVoices : window.speechSynthesis.getVoices());
  if (voice) u.voice = voice;
  window.speechSynthesis.speak(u);
}

function playButton(text, label) {
  const btn = make('button', 'play-btn', '▶');
  btn.setAttribute('aria-label', label || 'Listen');
  btn.onclick = () => sayAloud(text);
  return btn;
}

let wordLoading = false;
async function lookUpWord(span) {
  if (wordLoading) return;
  const word = span.textContent;
  const sentence = (span.closest('#juno-line, .text') || span).textContent;
  document.querySelectorAll('.w.picked').forEach((w) => w.classList.remove('picked'));
  span.classList.add('picked');
  try { localStorage.setItem('juno_tapped_word', '1'); } catch (e) { /* hint shows again */ }
  $('tap-hint').hidden = true;
  if (micBusy) { cancelListening(); idleOrb(); }
  clearTimeout(autoTimer);
  $('word-title').textContent = word;
  $('word-text').textContent = 'Un momento…';
  $('word-card').hidden = false;
  wordLoading = true;
  try {
    const data = await api('/api/define', { word, sentence });
    const box = $('word-text');
    box.textContent = '';
    const row = make('div', 'say-row');
    const text = make('div');
    text.append(make('strong', '', data.meaning));
    if (data.example) text.append(make('span', 'example', data.example));
    row.append(playButton(data.example ? data.word + '. ' + data.example : data.word, 'Listen to ' + data.word), text);
    box.append(row);
  } catch (e) {
    $('word-text').textContent = e.message;
  } finally {
    wordLoading = false;
  }
}
document.addEventListener('click', (e) => {
  const span = e.target.closest ? e.target.closest('.w') : null;
  if (span && $('chat-screen').contains(span)) lookUpWord(span);
});
$('word-close').onclick = () => {
  $('word-card').hidden = true;
  document.querySelectorAll('.w.picked').forEach((w) => w.classList.remove('picked'));
};

function showHelp(data) {
  const box = $('help-text');
  if (!data.answers || !data.answers.length) { fillRich(box, data.help); return; }
  box.textContent = '';
  if (data.explanation) box.append(make('p', '', data.explanation));
  data.answers.forEach((a) => {
    const row = make('div', 'say-row');
    const text = make('div');
    text.append(make('strong', '', a.en));
    if (a.es) text.append(make('span', '', a.es));
    row.append(playButton(a.en, 'Listen'), text);
    box.append(row);
  });
}

$('help-btn').onclick = async () => {
  if (helpLoading || paused) return;
  clearTimeout(autoTimer);
  if (micBusy) { cancelListening(); idleOrb(); }
  helpLoading = true;
  $('help-btn').disabled = true;
  $('help-card').hidden = false;
  $('help-text').textContent = 'Un momento…';
  try {
    const data = await api('/api/help', {}, {
      onWaking: () => { $('help-text').textContent = 'Juno se está despertando, un momento…'; },
    });
    showHelp(data);
  } catch (e) {
    $('help-text').textContent = e.message;
  } finally {
    helpLoading = false;
    $('help-btn').disabled = paused;
  }
};
$('help-close').onclick = () => { $('help-card').hidden = true; };

// Try an empty passphrase first - if no access code is configured server-side,
// this succeeds immediately and the auth screen never has to be shown.
//
// This is the very first network call the page makes, before the student has
// touched anything - and on Render's free tier, a service that's been idle
// goes to sleep, so this exact call is usually the one that wakes it back up.
// Every other call in this file passes onWaking to show that; this one
// previously did not, so a cold start looked like nothing - the static
// "Loading…" text just sat there for up to a minute with no sign anything
// was happening, which is indistinguishable from broken. It isn't scoped to
// one screen (onWaking usually targets setup-status/chat-status) because at
// this point the auth screen is still showing, not the setup screen - lede
// sits outside every screen div, so it's visible no matter which one is up.
api('/api/auth', { passphrase: '' }, {
  onWaking: () => { $('lede').textContent = 'Juno is waking up — this can take up to a minute after a quiet spell…'; },
}).then(() => {
  $('lede').textContent = 'A live practice call, corrected as you go.';
  afterAuth();
}).catch(() => {
  $('lede').textContent = 'A live practice call, corrected as you go. Enter the access code to begin.';
});

async function checkForUnfinishedClass() {
  try {
    const data = await api('/api/resume', {
      access_code: $('student-code') ? $('student-code').value : null,
    });
    const last = data.last_session;
    adoptServerName(data.name);
    levelEstimate = data.level || null;
    showReminders(data.reminders);
    renderHello();
    $('last-time').hidden = !(last && (last.what_we_did || last.next_recommendation));
    if (last) {
      $('last-time-text').textContent = last.what_we_did || '';
      $('last-time-next').textContent = last.next_recommendation
        ? 'Next focus: ' + last.next_recommendation : '';
    }
    if (!data.open_call) { $('resume-notice').style.display = 'none'; pendingResume = null; return; }
    const call = data.open_call;
    pendingResume = call;
    const when = new Date(call.updated_at).toLocaleString();
    $('resume-detail').textContent =
      `${call.turns} message${call.turns === 1 ? '' : 's'}, last saved ${when}.`;
    $('resume-notice').style.display = 'block';
  } catch (e) {
    // Nothing to recover, or we couldn't ask. Either way the student can
    // just start a new class; this is never worth an error in their face.
  }
}

let pendingResume = null;

$('resume-btn').onclick = () => {
  if (!pendingResume) return;
  currentCallId = pendingResume.call_id;
  classInProgress = true;
  clearChat();
  pendingResume.transcript.forEach((m) => {
    bubble(m.role === 'user' ? 'you' : 'juno', m.content);
  });
  showSaved(pendingResume.updated_at);
  $('resume-notice').style.display = 'none';
  startClass(pendingResume.mode);
  autoListen();
};

$('resume-report-btn').onclick = async () => {
  if (!pendingResume) return;
  $('resume-report-btn').disabled = true;
  try {
    const data = await api('/api/end', { call_id: pendingResume.call_id });
    classInProgress = false;
    renderRecap(data.report);
    $('resume-notice').style.display = 'none';
    show('report-screen');
  } catch (e) {
    $('start-error').textContent = e.message;
  } finally {
    $('resume-report-btn').disabled = false;
  }
};

$('resume-discard-btn').onclick = async () => {
  try { await api('/api/abandon', {}); } catch (e) { /* nothing to undo */ }
  pendingResume = null;
  classInProgress = false;
  $('resume-notice').style.display = 'none';
};

function afterAuth() {
  $('lede').textContent = 'A live practice call, corrected as you go.';
  $('feedback-btn').style.display = 'inline-block';
  restoreIdentity();
  showInstallCard();
  show('today-screen');
  checkForUnfinishedClass();
  fetch('/scenarios.json', { credentials: 'same-origin' }).then((r) => r.json()).then((data) => {
    scenarios = data;
    if (!scenarios.business_packs) throw new Error('scenarios.json is an old version.');
    const sel = $('scenario');
    // Grouped by sector, and each group opens with its own "surprise me" -
    // a flat list of every scenario across every pack is unusable once there
    // are more than a couple of packs, and hides which sector a title is from.
    scenarios.business_packs.forEach((p) => {
      const group = document.createElement('optgroup');
      group.label = p.label;
      const anyInPack = document.createElement('option');
      anyInPack.value = `pack:${p.id}`;
      anyInPack.textContent = `Surprise me — ${p.label}`;
      group.appendChild(anyInPack);
      p.scenarios.forEach((s) => {
        const opt = document.createElement('option');
        opt.value = s.id;
        opt.textContent = `${s.title} — ${s.expression} (${s.cefr})`;
        group.appendChild(opt);
      });
      sel.appendChild(group);
    });
  }).catch((e) => { $('start-error').textContent = 'Could not load scenarios: ' + e.message; });
  setupVoiceInput();
}

$('student-code').addEventListener('change', checkForUnfinishedClass);

$('auth-btn').onclick = async () => {
  $('auth-error').textContent = '';
  try {
    await api('/api/auth', { passphrase: $('passphrase').value });
    afterAuth();
  } catch (e) {
    $('auth-error').textContent = e.message;
  }
};

$('mode').onchange = () => {
  $('scenario-row').style.display = $('mode').value === 'business' ? 'block' : 'none';
};

$('start-btn').onclick = async () => {
  if ($('start-btn').disabled) return;   // no double-starts
  $('start-error').textContent = '';
  $('start-btn').disabled = true;
  setStatus('setup-status', 'Connecting…');
  try {
    const mode = $('mode').value;
    // "pack:<id>" means any scenario from that sector; a bare value is one
    // specific scenario. Empty is any scenario from any pack.
    const choice = mode === 'business' ? $('scenario').value : '';
    const data = await api('/api/start', {
      student: $('student').value || 'demo',
      access_code: $('student-code') ? $('student-code').value : null,
      mode,
      level: $('level').value,
      scenario_id: choice.startsWith('pack:') ? null : (choice || null),
      pack: choice.startsWith('pack:') ? choice.slice(5) : null,
      easier: $('easier').checked,
    }, { onWaking: () => setStatus('setup-status',
        'Juno is waking up — this takes up to a minute after a quiet spell.',
        'waking') });
    currentCallId = data.call_id;
    classInProgress = true;
    clearChat();
    bubble('juno', data.reply);
    showSaved(data.saved_at);
    setStatus('setup-status', '');
    startClass(mode);
    if (!speak(data.reply)) autoListen();
  } catch (e) {
    $('start-error').textContent = e.message;
    setStatus('setup-status', '', e.kind === 'network' ? 'offline' : '');
  } finally {
    $('start-btn').disabled = false;
  }
};

let sending = false;
let currentCallId = null;
let classInProgress = false;

async function sendMessage() {
  if (sending) return;               // a second Enter while the first is in flight
  const input = $('msg-input');
  const text = input.value.trim();
  if (!text) return;

  // Generated once per attempt and reused if we retry, so the server can
  // recognise a repeat instead of adding the same turn twice.
  const key = `${currentCallId || 'call'}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

  sending = true;
  const before = junoLine;
  bubble('you', text);
  setJunoLine('…', true);
  input.value = '';
  $('help-card').hidden = true;
  $('send-btn').disabled = true;
  $('msg-input').disabled = true;
  $('help-btn').disabled = true;
  $('chat-error').textContent = '';
  setOrb('thinking', 'Juno is thinking…');
  try {
    const data = await api('/api/message', { text, idempotency_key: key },
      { onWaking: () => setStatus('chat-status',
          'Juno is waking up — one moment.', 'waking') });
    bubble('juno', data.reply);
    showSaved(data.saved_at);
    sending = false;
    idleOrb();
    if (!speak(data.reply)) autoListen();
    if (data.turns_left !== undefined && data.turns_left <= 5) {
      $('chat-error').textContent =
        `${data.turns_left} messages left in this class — press End when you're ready for your report.`;
    }
  } catch (e) {
    setJunoLine(before);
    $('chat-error').textContent = e.message;
    setStatus('chat-status',
      e.kind === 'network' ? 'Not connected — your class is saved.' : '',
      e.kind === 'network' ? 'offline' : 'saved');
  } finally {
    sending = false;
    if (!$('mic-btn').classList.contains('speaking')) idleOrb();
    $('send-btn').disabled = false;
    $('msg-input').disabled = false;
    $('help-btn').disabled = paused;
    if (!$('type-row').hidden) input.focus();
  }
}
$('send-btn').onclick = sendMessage;
$('msg-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') sendMessage(); });

function make(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function describeLength(seconds) {
  const m = Math.floor(seconds / 60), s = seconds % 60;
  const mins = m === 1 ? '1 minute' : `${m} minutes`;
  if (!m) return `${s} seconds`;
  return s ? `${mins} ${s} seconds` : mins;
}

// Session notes, laid out like the Juno 2.0 design. Everything the model wrote
// goes in as text, so nothing in a report can ever run as page code.
function renderRecap(r, seconds) {
  const el = $('recap');
  el.innerHTML = '';
  el.append(make('p', 'eyebrow', 'Session notes'));
  const title = make('h1', 'notes-title');
  if (seconds) {
    title.append('Your class lasted', document.createElement('br'), make('i', '', describeLength(seconds)));
  } else {
    title.textContent = 'Your class recap';
  }
  el.append(title);
  if (r.what_we_did) el.append(make('p', 'notes-summary', r.what_we_did));

  const section = (label) => el.append(make('p', 'notes-label', label));

  if (r.corrections.length) {
    section(r.corrections.length === 1 ? 'Your correction' : `Your ${r.corrections.length} corrections`);
    r.corrections.forEach((c) => {
      const card = make('div', 'note-card');
      card.append(make('p', 'said', c.said), make('p', 'better', c.better));
      if (c.note) card.append(make('p', 'why', c.note));
      el.append(card);
    });
  }

  const expressions = [
    ...r.word_traps.map((w) => [w.we_say, `Instead of “${w.you_said}”. ${w.problem}`]),
    ...r.vocabulary_learned.map((v) => [v.term, v.meaning]),
  ];
  if (expressions.length) {
    section('Better expressions');
    const list = make('div', 'note-list');
    expressions.forEach(([term, why]) => {
      const row = make('div');
      row.append(make('strong', '', term), make('span', '', why));
      list.append(row);
    });
    el.append(list);
  }

  if (r.pronunciation.length) {
    section(r.pronunciation.length === 1 ? 'Pronunciation target' : 'Pronunciation targets');
    r.pronunciation.forEach((p) => {
      const card = make('div', 'note-card pron');
      const play = make('button', 'play-btn', '▶');
      play.setAttribute('aria-label', `Hear “${p.word}”`);
      play.onclick = () => {
        if (!window.speechSynthesis) return;
        window.speechSynthesis.cancel();
        const u = new SpeechSynthesisUtterance(speakable(p.word));
        u.lang = 'en-US';
        u.rate = 0.85;
        const voice = pickVoice(cachedVoices.length ? cachedVoices : window.speechSynthesis.getVoices());
        if (voice) u.voice = voice;
        window.speechSynthesis.speak(u);
      };
      const text = make('div');
      text.append(make('strong', '', `${p.word}  ${p.ipa || ''}`.trim()), make('span', '', p.watch_for));
      card.append(play, text);
      el.append(card);
    });
  }

  if (r.what_went_well.length) {
    section('What went well');
    const list = make('div', 'note-list');
    r.what_went_well.forEach((w) => list.append(make('div', '', w)));
    el.append(list);
  }

  $('next-task').textContent = r.next_recommendation || 'Keep talking: one more class this week.';
  const homework = $('homework');
  homework.innerHTML = '';
  (r.homework || []).forEach((h) => homework.append(make('li', '', h)));
  homework.hidden = !(r.homework || []).length;
  window.scrollTo(0, 0);
}

$('end-btn').onclick = async () => {
  if ($('end-btn').disabled) return;
  $('end-btn').disabled = true;
  $('send-btn').disabled = true;
  $('chat-error').textContent = '';
  if (window.speechSynthesis) window.speechSynthesis.cancel();
  cancelListening();
  // The report reads the whole conversation back, so it is the slowest thing
  // in the app - saying so beats a button that looks broken.
  setStatus('chat-status', 'Writing your report — this takes a few seconds…', 'waking');
  try {
    const data = await api('/api/end', { call_id: currentCallId });
    const seconds = Math.max(0, Math.floor(((pausedAt || Date.now()) - clockStart - clockPausedTotal) / 1000));
    classInProgress = false;
    stopClass();
    renderRecap(data.report, seconds);
    setStatus('chat-status', '');
    show('report-screen');
  } catch (e) {
    $('chat-error').textContent = e.message;
    setStatus('chat-status', 'Your class is saved — you can try again.', 'saved');
  } finally {
    $('end-btn').disabled = false;
    $('send-btn').disabled = false;
  }
};

// Closing the tab mid-class no longer loses anything, but the report is only
// written on End, so it is still worth a word.
window.addEventListener('beforeunload', (e) => {
  if (!classInProgress) return;
  e.preventDefault();
  e.returnValue = '';
});

function shortDate(iso) {
  return new Date(iso + 'T12:00:00').toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

function levelPhrase(o) {
  if (!o.next_band) return `${o.band}: the top of the scale`;
  const f = o.fraction;
  if (f < 0.15) return `Just into ${o.band}`;
  if (f < 0.4) return `A third of the way to ${o.next_band}`;
  if (f < 0.6) return `Halfway to ${o.next_band}`;
  if (f < 0.85) return `Two thirds of the way to ${o.next_band}`;
  return `Almost at ${o.next_band}`;
}

function renderLevel(el, level) {
  if (!level || !level.ready) {
    const have = level ? level.rated_classes : 0, need = level ? level.needed : 2;
    el.append(make('div', 'level-wait', have
      ? `Your level estimate appears after ${need} full classes. You have ${have} so far.`
      : `Your level estimate appears after ${need} full classes. Juno needs to hear you speak a good while first.`));
    return;
  }
  const o = level.overall;
  const card = make('div', 'level-card');
  const bands = make('div', 'row-bands');
  bands.append(make('span', '', o.band), make('small', '', levelPhrase(o)), make('span', '', o.next_band || ''));
  const track = make('div', 'track'), fill = make('div', 'fill'), dot = make('div', 'dot');
  fill.style.width = Math.round(o.fraction * 100) + '%';
  dot.style.left = Math.round(o.fraction * 100) + '%';
  track.append(fill, dot);
  const n = level.rated_classes;
  card.append(bands, track, make('p', '', `Estimated by Juno from your last ${n} classes. A guide, not an exam result.`));
  el.append(card);
}

function renderProgress(p) {
  const el = $('progress-body');
  el.innerHTML = '';
  el.append(make('p', 'eyebrow', 'Your progress'));
  const title = make('h1', 'notes-title');
  if (p.classes_total) {
    title.append(p.classes_total === 1 ? '1 class,' : `${p.classes_total} classes,`,
      document.createElement('br'), make('i', '', p.minutes_total === 1 ? '1 minute' : `${p.minutes_total} minutes`));
  } else {
    title.append('Your first class', document.createElement('br'), make('i', '', 'is waiting'));
  }
  el.append(title);
  if (!p.classes_total) {
    el.append(make('p', 'notes-summary', 'Finish a class with End and your numbers will start to appear here.'));
  } else if (p.streak_days > 1) {
    el.append(make('p', 'notes-summary', `${p.streak_days} days in a row. Keep it going.`));
  }

  renderLevel(el, p.level);

  const stats = make('div', 'stat-row');
  [[p.minutes_week, 'minutes this week'], [p.exchanges_total, 'things you said to Juno'], [p.words_learned, 'new words and phrases']]
    .forEach(([n, label]) => { const box = make('div', 'stat'); box.append(make('b', '', String(n)), make('span', '', label)); stats.append(box); });
  el.append(stats);

  el.append(make('p', 'notes-label', 'Minutes in class — last 7 days'));
  const peak = Math.max(1, ...p.week.map((d) => d.minutes));
  const bars = make('div', 'bars'), days = make('div', 'bar-days');
  p.week.forEach((d, i) => {
    const bar = make('div', 'bar' + (d.minutes ? (i === p.week.length - 1 ? ' today' : ' some') : ''));
    bar.style.height = Math.max(4, Math.round((d.minutes / peak) * 100)) + '%';
    bar.title = `${shortDate(d.date)}: ${d.minutes} min`;
    bars.append(bar);
    days.append(make('span', '', d.weekday.slice(0, 1)));
  });
  el.append(bars, days);

  if (p.level && p.level.ready) {
    el.append(make('p', 'notes-label', 'Your skills'));
    const box = make('div', 'note-card');
    p.level.skills.forEach((k) => {
      const row = make('div', 'skill');
      const head = make('div', 'skill-head');
      head.append(make('span', '', k.label), make('b', '', k.band + (k.plus && k.next_band ? '+' : '')));
      const bar = make('div', 'skill-bar'), fill = make('i');
      fill.style.width = Math.round(((k.value - 1) / 6) * 100) + '%';
      bar.append(fill);
      row.append(head, bar);
      box.append(row);
    });
    box.append(make('p', 'why', 'Each bar runs from A1 to C2.'));
    el.append(box);
  }

  if (p.working_on.length) {
    el.append(make('p', 'notes-label', 'What you are working on'));
    const list = make('div', 'note-list');
    p.working_on.forEach((w) => list.append(make('div', '', w)));
    el.append(list);
  }
  if (p.recent_words.length) {
    el.append(make('p', 'notes-label', 'Latest words and phrases'));
    const list = make('div', 'note-list');
    p.recent_words.forEach((w) => list.append(make('div', '', w)));
    el.append(list);
  }
  if (p.recent.length) {
    el.append(make('p', 'notes-label', 'Recent classes'));
    p.recent.forEach((c) => {
      const card = make('div', 'note-card');
      card.append(make('p', 'meta', `${shortDate(c.date)} · ${MODE_NAMES[c.mode] || c.mode} · ${c.minutes} min`));
      if (c.what_we_did) card.append(make('p', 'body', c.what_we_did));
      el.append(card);
    });
  }
  if (p.next_recommendation) {
    const card = make('div', 'task-card');
    card.append(make('p', 'eyebrow', 'Next focus'), make('h2', '', p.next_recommendation));
    el.append(card);
  }
}

async function loadProgress() {
  show('progress-screen');
  const el = $('progress-body');
  el.textContent = 'Loading…';
  try {
    const data = await api('/api/progress', {
      student: $('student').value,
      access_code: $('student-code').value,
      tz_offset: new Date().getTimezoneOffset(),
    }, { onWaking: () => { el.textContent = 'Juno is waking up — this can take up to a minute…'; } });
    renderProgress(data);
  } catch (e) {
    el.textContent = e.message;
  }
  window.scrollTo(0, 0);
}

let loadedNotes = [];

function renderNotesList() {
  const el = $('notes-list');
  el.innerHTML = '';
  if (!loadedNotes.length) {
    el.append(make('p', 'notes-summary', 'No notes yet. Finish a class with End and its notes will be kept here.'));
    return;
  }
  loadedNotes.forEach((n) => {
    const when = new Date(n.started_at).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
    const card = make('button', 'note-card note-open');
    card.append(make('p', 'meta', `${when} · ${MODE_NAMES[n.mode] || n.mode} · ${n.minutes} min`),
                make('p', 'body', n.report.what_we_did || 'Class notes'));
    card.onclick = () => { renderRecap(n.report, null); show('report-screen'); };
    el.append(card);
  });
}

async function loadNotes() {
  show('notes-screen');
  const el = $('notes-list');
  el.textContent = 'Loading…';
  try {
    const data = await api('/api/notes', { student: $('student').value, access_code: $('student-code').value },
      { onWaking: () => { el.textContent = 'Juno is waking up — this can take up to a minute…'; } });
    loadedNotes = data.notes || [];
    renderNotesList();
  } catch (e) {
    el.textContent = e.message;
  }
  window.scrollTo(0, 0);
}

function goTab(tab) {
  if (tab === 'today') { show('today-screen'); checkForUnfinishedClass(); }
  else if (tab === 'practice') show('practice-screen');
  else if (tab === 'progress') loadProgress();
  else if (tab === 'notes') loadNotes();
  window.scrollTo(0, 0);
}
document.querySelectorAll('#tabs button').forEach((btn) => btn.addEventListener('click', () => goTab(btn.dataset.tab)));

// Name and code are asked once and remembered on this device.
const IDENTITY_KEY = 'juno_identity';

function saveIdentity() {
  try {
    localStorage.setItem(IDENTITY_KEY, JSON.stringify({ name: $('student').value.trim(), code: $('student-code').value.trim() }));
  } catch (e) { /* private window: they will be asked again next time */ }
}

let levelEstimate = null;

// --- Practice reminders by email (only offered once the teacher has set it up) ---
(function fillHours() {
  const select = $('email-hour');
  for (let h = 6; h <= 22; h++) {
    const opt = document.createElement('option');
    opt.value = String(h);
    opt.textContent = String(h).padStart(2, '0') + ':00';
    if (h === 18) opt.selected = true;
    select.append(opt);
  }
})();

function showReminders(r) {
  if (!r || !r.available) { $('email-card').hidden = true; return; }
  $('email-card').hidden = false;
  if (r.email && !$('email-address').value) $('email-address').value = r.email;
  if (r.hour !== null && r.hour !== undefined) $('email-hour').value = String(r.hour);
  const messages = {
    on: 'Reminders are on.',
    pending: 'We sent you an email. Open it and press the link to turn reminders on.',
    stopped: 'Reminders are off.',
  };
  $('email-status').textContent = messages[r.state] || '';
  $('email-stop').hidden = !(r.state === 'on' || r.state === 'pending');
  $('email-consent').checked = r.state === 'on';
  $('email-save').textContent = r.state === 'on' ? 'Save changes' : 'Turn on reminders';
}

$('email-save').onclick = async () => {
  $('email-error').textContent = '';
  if (!$('email-consent').checked) { $('email-error').textContent = 'Tick the box to turn reminders on.'; return; }
  $('email-save').disabled = true;
  try {
    const data = await api('/api/reminders', {
      student: $('student').value, access_code: $('student-code').value,
      email: $('email-address').value, hour: $('email-hour').value,
      tz_offset: new Date().getTimezoneOffset(),
    });
    showReminders({ available: true, email: data.email, hour: data.hour, state: data.state });
  } catch (e) {
    $('email-error').textContent = e.message;
  } finally {
    $('email-save').disabled = false;
  }
};
$('email-stop').onclick = async () => {
  $('email-error').textContent = '';
  try {
    await api('/api/reminders', { student: $('student').value, access_code: $('student-code').value, action: 'stop' });
    showReminders({ available: true, email: $('email-address').value, hour: Number($('email-hour').value), state: 'stopped' });
  } catch (e) {
    $('email-error').textContent = e.message;
  }
};

function renderHello() {
  const hour = new Date().getHours();
  const part = hour < 12 ? 'Good morning' : hour < 19 ? 'Good afternoon' : 'Good evening';
  const hello = $('hello');
  hello.innerHTML = '';
  hello.append(part + ',', document.createElement('br'), make('i', '', $('student').value.trim() || 'welcome'));
  $('today-date').textContent = new Date().toLocaleDateString(undefined, { weekday: 'long' }) + ' · your practice space';
  $('level-badge-text').textContent = levelEstimate || $('level').value;
  $('level-badge-note').textContent = levelEstimate ? 'Estimated' : 'Level';
}

function showProfile(open) {
  $('profile-card').hidden = !open;
  $('profile-change').hidden = open;
}

function restoreIdentity() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(IDENTITY_KEY)) || {}; } catch (e) { saved = {}; }
  if (saved.name && !$('student').value) $('student').value = saved.name;
  if (saved.code && !$('student-code').value) $('student-code').value = saved.code;
  showProfile(!($('student').value.trim() || $('student-code').value.trim()));
  renderHello();
}

// A student who types only a personal code is greeted by the name their
// teacher registered, not by "welcome".
function adoptServerName(name) {
  if (!name || name === 'Student' || $('student').value.trim()) return;
  $('student').value = name;
  saveIdentity();
  renderHello();
}

$('profile-save').onclick = () => {
  if (!$('student').value.trim() && !$('student-code').value.trim()) {
    $('profile-error').textContent = 'Type your name to begin.';
    return;
  }
  $('profile-error').textContent = '';
  saveIdentity();
  showProfile(false);
  renderHello();
  checkForUnfinishedClass();
};
$('profile-change').onclick = () => showProfile(true);
$('level').addEventListener('change', renderHello);
$('level-badge').onclick = () => goTab('practice');

function selectMode(mode) {
  $('mode').value = mode;
  $('mode').onchange();
  document.querySelectorAll('#practice-screen .mode-card').forEach((c) => c.classList.toggle('on', c.dataset.mode === mode));
}
document.querySelectorAll('#practice-screen .mode-card').forEach((c) => c.addEventListener('click', () => selectMode(c.dataset.mode)));
document.querySelectorAll('#today-screen .mode-card').forEach((c) => c.addEventListener('click', () => { selectMode(c.dataset.mode); goTab('practice'); }));
selectMode('free');

$('again-btn').onclick = () => goTab('today');

$('feedback-btn').onclick = () => {
  const panel = $('feedback-panel');
  panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
  $('feedback-error').textContent = '';
  $('feedback-thanks').style.display = 'none';
};
$('feedback-cancel').onclick = () => { $('feedback-panel').style.display = 'none'; };
$('feedback-submit').onclick = async () => {
  const text = $('feedback-text').value.trim();
  $('feedback-error').textContent = '';
  $('feedback-thanks').style.display = 'none';
  if (!text) { $('feedback-error').textContent = 'Type something first.'; return; }
  $('feedback-submit').disabled = true;
  try {
    await api('/api/feedback', { text, student: $('student').value, mode: $('mode').value });
    $('feedback-text').value = '';
    $('feedback-thanks').style.display = 'block';
  } catch (e) {
    $('feedback-error').textContent = e.message;
  } finally {
    $('feedback-submit').disabled = false;
  }
};
</script>
</body>
</html>
"""


def _notice_page(title: str, message: str, site_url: str) -> bytes:
    """The page a student lands on from a link in an email."""
    link = f'<p><a href="{html_escape(site_url)}">Abrir Juno</a></p>' if site_url else ""
    return (f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>{html_escape(title)}</title></head>
<body style="margin:0;background:#F6F2EA;font-family:system-ui,sans-serif;color:#191714">
<div style="max-width:460px;margin:12vh auto;padding:0 22px">
<h1 style="font-weight:600;font-size:26px">{html_escape(title)}</h1>
<p style="line-height:1.5">{html_escape(message)}</p>{link}</div></body></html>""").encode("utf-8")


ADMIN_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Juno teacher page</title>
<style>
  body{margin:0;background:#0E0C16;color:#CFC9DD;font-family:system-ui,sans-serif;font-size:15px}
  .wrap{max-width:760px;margin:0 auto;padding:24px 18px 60px}
  h1{color:#F6F2EA;font-weight:600;font-size:26px;margin:0 0 6px}
  h2{color:#F6F2EA;font-weight:600;font-size:17px;margin:26px 0 10px}
  p.sub{color:#8B84A8;margin:0 0 22px}
  .panel{background:#1A1628;border:1px solid rgba(255,255,255,.10);border-radius:18px;padding:18px;margin-bottom:14px}
  input,textarea{width:100%;padding:12px 14px;border-radius:12px;border:1px solid rgba(255,255,255,.14);background:#252036;color:#F6F2EA;font-size:16px;box-sizing:border-box;font-family:inherit}
  textarea{resize:vertical;line-height:1.45;font-size:15px}
  button{border:none;border-radius:999px;padding:11px 18px;background:#7A4BEE;color:#fff;font-weight:600;font-size:14px;cursor:pointer}
  button:disabled{opacity:.45;cursor:default}
  button.ghost{background:transparent;border:1px solid rgba(255,255,255,.2);color:#CFC9DD}
  .row{display:flex;gap:10px;margin-top:12px;flex-wrap:wrap;align-items:center}
  .row input{flex:1;min-width:150px}
  code{background:#252036;padding:3px 8px;border-radius:8px;color:#B79BFF;font-size:14px;white-space:nowrap}
  .err{color:#FF7A6B;margin-top:10px}.ok{color:#5FD0A0;margin-top:10px}
  .muted{color:#8B84A8;font-size:13.5px}
  .tip{background:#241C3B;border:1px solid rgba(155,124,255,.35);border-radius:14px;padding:12px 14px;font-size:14px;line-height:1.5;margin-bottom:14px}
  .card{background:#1A1628;border:1px solid rgba(255,255,255,.10);border-radius:18px;padding:16px;margin-bottom:12px}
  .top{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:6px}
  .top strong{color:#F6F2EA;font-size:17px}
  .chip{margin-left:auto;font-size:12px;border-radius:999px;padding:4px 10px;background:#252036;color:#CFC9DD;white-space:nowrap}
  .chip.send{background:#3B2A7A;color:#E4DAFF}
  .chip.good{background:#173B2E;color:#8FE3BE}
  .facts{color:#8B84A8;font-size:13.5px;margin-bottom:10px}
  .card textarea{margin-top:10px}
  .soft{color:#E3A33B;font-size:13px;margin-top:8px}
  .detail{margin-top:14px;padding-top:12px;border-top:1px solid rgba(255,255,255,.10);font-size:14px;line-height:1.5}
  .detail p{margin:0 0 8px}
  .detail .big{color:#F6F2EA;font-weight:600;font-size:15px}
  .detail .label{color:#8B84A8;font-size:11px;letter-spacing:.12em;text-transform:uppercase;margin:14px 0 4px}
  .detail ul{margin:0;padding-left:18px}
  .detail li{margin-bottom:4px}
</style></head><body><div class="wrap">
<h1>Juno teacher page</h1>
<p class="sub">Personal codes, and reminders you send yourself.</p>
<div class="panel" id="login">
  <input type="password" id="code" placeholder="Teacher code" autocomplete="current-password">
  <div class="row"><button id="open">Open</button></div>
  <div class="err" id="login-err"></div>
</div>
<div id="main" hidden>
  <div class="tip">Best time to send: from 18:00 to 19:00. Before 17:30 most adults are still working. One reminder a day at most, and softer at weekends. Messages open in WhatsApp already written; you read them, change what you like, and press send yourself.</div>
  <div class="panel">
    <strong style="color:#F6F2EA">New student</strong>
    <div class="row"><input id="new-name" placeholder="First name"><input id="new-phone" placeholder="WhatsApp, with country code (optional)"><button id="create">Create code</button></div>
    <div id="created"></div>
  </div>
  <div class="panel">
    <strong style="color:#F6F2EA">Hourly check</strong>
    <p class="muted" style="margin:6px 0 0">Every hour the timer closes classes a student left without pressing End (so their notes and progress appear), and emails students who turned reminders on, on weekdays at their own hour. This button runs it now.</p>
    <div class="row"><button class="ghost" id="run-reminders">Run it now</button></div>
    <div id="run-result"></div>
  </div>
  <h2>Students</h2>
  <div id="list"></div>
</div>
<script>
const $ = (i) => document.getElementById(i);
let adminCode = '';
async function call(body) {
  const res = await fetch('/api/admin', {method: 'POST', headers: {'content-type': 'application/json'},
    body: JSON.stringify(Object.assign({admin_code: adminCode, tz_offset: new Date().getTimezoneOffset()}, body))});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || 'Something went wrong.');
  return data;
}
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}
function ago(days) {
  if (days === null || days === undefined) return 'never';
  if (days === 0) return 'today';
  return days === 1 ? 'yesterday' : days + ' days ago';
}
async function guarded(button, work) {
  button.disabled = true;
  try { await work(); } catch (e) { alert(e.message); } finally { button.disabled = false; }
}
function card(s) {
  const r = s.reminder;
  const box = el('div', 'card');
  const top = el('div', 'top');
  top.append(el('strong', '', s.display_name));
  if (s.access_code) {
    top.append(el('code', '', s.access_code));
  } else {
    const give = el('button', 'ghost', 'Give a code');
    give.onclick = () => guarded(give, async () => { await call({action: 'assign', student_id: s.student_id}); await refresh(); });
    top.append(give);
  }
  top.append(el('span', 'chip ' + (['welcome', 'streak', 'nudge', 'back_3', 'back_7', 'closing'].includes(r.kind) ? 'send' : r.kind === 'done' ? 'good' : ''), r.label));
  box.append(top);

  const bits = ['Last seen: ' + ago(s.last_seen_days), 'last class: ' + ago(s.days_since_last),
    s.classes + (s.classes === 1 ? ' class' : ' classes')];
  if (s.unfinished_classes) bits.push(s.unfinished_classes + ' not finished yet');
  if (s.streak_days > 1) bits.push(s.streak_days + '-day streak');
  bits.push(s.minutes_week + ' min this week');
  if (s.level) bits.push('level ' + s.level);
  if (s.reminded_ago_days !== null) bits.push('reminded ' + ago(s.reminded_ago_days));
  if (s.email_state === 'on') bits.push('email on, ' + String(s.remind_hour).padStart(2, '0') + ':00');
  else if (s.email_state === 'pending') bits.push('email not confirmed yet');
  else if (s.email_state === 'stopped') bits.push('email stopped');
  box.append(el('div', 'facts', bits.join(' · ')));

  const phoneRow = el('div', 'row');
  phoneRow.style.marginTop = '0';
  const phone = el('input');
  phone.placeholder = 'WhatsApp, with country code';
  phone.value = s.phone || '';
  const save = el('button', 'ghost', 'Save number');
  save.onclick = () => guarded(save, async () => { await call({action: 'set_phone', student_id: s.student_id, phone: phone.value}); await refresh(); });
  phoneRow.append(phone, save);
  box.append(phoneRow);

  if (r.message) {
    const text = el('textarea');
    text.rows = 7;
    text.value = r.message;
    box.append(text);
    const actions = el('div', 'row');
    const send = el('button', '', 'Open WhatsApp');
    send.disabled = !s.phone;
    send.onclick = () => {
      window.open('https://wa.me/' + s.phone.replace('+', '') + '?text=' + encodeURIComponent(text.value), '_blank');
      guarded(send, async () => { await call({action: 'mark_reminded', student_id: s.student_id}); await refresh(); });
    };
    const copy = el('button', 'ghost', 'Copy message');
    copy.onclick = async () => { try { await navigator.clipboard.writeText(text.value); copy.textContent = 'Copied'; } catch (e) { text.select(); } };
    actions.append(send, copy);
    if (!s.phone) actions.append(el('span', 'muted', 'Add a number to send it on WhatsApp.'));
    box.append(actions);
    if (r.soft) box.append(el('div', 'soft', 'It is the weekend: this one is optional.'));
  }
  const detail = el('div', 'detail');
  detail.hidden = true;
  const more = el('button', 'ghost', 'See progress');
  more.style.marginTop = '12px';
  more.style.marginRight = '8px';
  more.onclick = () => guarded(more, async () => {
    if (!detail.hidden) { detail.hidden = true; more.textContent = 'See progress'; return; }
    showProgress(detail, await call({action: 'progress', student_id: s.student_id}));
    detail.hidden = false;
    more.textContent = 'Hide progress';
  });
  box.append(more);
  const pause = el('button', 'ghost', s.paused ? 'Resume reminders' : 'Pause reminders');
  pause.style.marginTop = '12px';
  pause.onclick = () => guarded(pause, async () => { await call({action: 'set_paused', student_id: s.student_id, paused: !s.paused}); await refresh(); });
  box.append(pause, detail);
  return box;
}
const MODES = {free: 'Free talk', business: 'Business English', structured: 'Structured class'};
function showProgress(box, p) {
  box.replaceChildren();
  const level = p.level || {};
  if (level.ready) {
    const o = level.overall;
    box.append(el('p', 'big', 'Level estimate: ' + o.band + (o.plus && o.next_band ? '+' : '')
      + ' (from the last ' + level.rated_classes + ' classes)'));
    box.append(el('p', 'muted', level.skills.map((k) => k.label + ' ' + k.band + (k.plus && k.next_band ? '+' : '')).join(' · ')));
  } else {
    box.append(el('p', 'muted', 'No level estimate yet: it needs 2 full classes, and has ' + (level.rated_classes || 0) + '.'));
  }
  box.append(el('p', '', p.classes_total + ' classes · ' + p.minutes_total + ' min in total · ' + p.minutes_week
    + ' min this week · ' + p.streak_days + '-day streak · ' + p.words_learned + ' new words and phrases'));
  const list = (title, items) => {
    if (!items.length) return;
    box.append(el('p', 'label', title));
    const ul = el('ul');
    items.forEach((t) => ul.append(el('li', '', t)));
    box.append(ul);
  };
  list('Working on', p.working_on);
  list('Recent classes', p.recent.map((c) => c.date + ' · ' + (MODES[c.mode] || c.mode) + ' · ' + c.minutes + ' min'
    + (c.what_we_did ? ' — ' + c.what_we_did : '')));
  list('Latest words', p.recent_words);
  if (p.next_recommendation) list('Next focus', [p.next_recommendation]);
}
async function refresh() {
  const data = await call({action: 'list'});
  const list = $('list');
  list.replaceChildren();
  if (!data.students.length) list.append(el('p', 'muted', 'No students yet. Create the first one above.'));
  data.students.forEach((s) => list.append(card(s)));
}
$('open').onclick = async () => {
  adminCode = $('code').value;
  $('login-err').textContent = '';
  try { await refresh(); $('login').hidden = true; $('main').hidden = false; }
  catch (e) { $('login-err').textContent = e.message; }
};
$('run-reminders').onclick = () => guarded($('run-reminders'), async () => {
  const r = await call({action: 'run_reminders'});
  const c = r.classes_closed;
  let text = 'Classes closed: ' + c.reported + (c.dropped ? ' (and ' + c.dropped + ' empty ones dropped)' : '') + '.';
  if (r.checked !== undefined) {
    const skipped = Object.entries(r.skipped).map(([why, n]) => n + ' ' + why).join(', ');
    text += ' Emails: checked ' + r.checked + ', sent ' + r.sent + (skipped ? ', skipped ' + skipped : '') + '.';
  } else {
    text += ' Email reminders are not set up.';
  }
  $('run-result').replaceChildren(el('div', 'ok', text));
  await refresh();
});
$('code').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('open').click(); });
$('create').onclick = async () => {
  const name = $('new-name').value.trim();
  if (!name) return;
  try {
    const data = await call({action: 'create', name, phone: $('new-phone').value});
    const box = $('created'); box.replaceChildren();
    const line = el('div', 'ok', name + ': '); line.append(el('code', '', data.access_code));
    box.append(line);
    $('new-name').value = ''; $('new-phone').value = '';
    await refresh();
  } catch (e) { $('created').replaceChildren(el('div', 'err', e.message)); }
};
</script></div></body></html>
"""

client = anthropic.Anthropic()


def fold_unfinished_class(call: dict) -> str:
    """Close a class the student never ended: write its report and fold what
    it taught into their memory, exactly as pressing End would have. A class
    with almost nothing said is just dropped. Returns 'reported' or 'dropped'."""
    if call["turns"] < 2:
        store.abandon_call(call["call_id"])
        return "dropped"
    report = tutor.generate_report(client, call["transcript"])
    memory = tutor.load_student(call["student_id"])
    memory = tutor.apply_report_to_student(
        memory, report, call["mode"],
        {"id": call["scenario_id"]} if call["scenario_id"] else None)
    tutor.save_student(memory)
    store.finish_call(call["call_id"], report)
    return "reported"


def progress_payload(student: dict, tz_offset: int) -> dict:
    """The numbers behind the Progress screen, for the student or the teacher.
    All of it comes from saved classes and Juno's memory of the student."""
    numbers = store.progress_for(student["student_id"], tz_offset)
    memory = tutor.load_student(student["student_id"])
    words = memory.get("vocab_acquired_log") or []
    numbers.update({
        "name": student["display_name"],
        "words_learned": len(words),
        "recent_words": [w.split(" — ")[0] for w in words[-6:]][::-1],
        "grammar_covered": (memory.get("grammar_covered") or [])[-5:][::-1],
        "working_on": (memory.get("recurring_error_patterns") or [])[:5],
        "next_recommendation": memory.get("next_recommendation") or "",
    })
    return numbers


def close_stale_classes(idle_hours: float = 3) -> dict:
    """Run by the hourly timer, so a student who closed the tab and never came
    back still gets their notes, and the teacher sees the class."""
    done = {"reported": 0, "dropped": 0, "failed": 0}
    for call in store.stale_open_calls(idle_hours):
        try:
            done[fold_unfinished_class(call)] += 1
        except Exception as e:  # noqa: BLE001
            done["failed"] += 1
            print(f"[juno] could not close an old class: {e!r}", file=sys.stderr, flush=True)
    return done


class Handler(BaseHTTPRequestHandler):
    def _load_session(self) -> dict:
        cookies = http.cookies.SimpleCookie()
        cookies.load(self.headers.get("Cookie", ""))
        self._cookies = cookies
        self._pending_cookies = []

        sid = cookies["juno_sid"].value if "juno_sid" in cookies else None
        if not sid or sid not in SESSIONS:
            sid = secrets.token_urlsafe(24)
            SESSIONS[sid] = {"authed": not ACCESS_PASSPHRASE, "call_count": 0}
            self._set_cookie("juno_sid", sid)
        self._sid = sid
        return SESSIONS[sid]

    def _set_cookie(self, name: str, value: str, max_age: int | None = None) -> None:
        secure = "; Secure" if RUNNING_DEPLOYED else ""
        age = f"; Max-Age={max_age}" if max_age else ""
        self._pending_cookies.append(
            f"{name}={value}; Path=/; HttpOnly; SameSite=Lax{secure}{age}"
        )

    def _cookie_headers(self) -> list[str]:
        return getattr(self, "_pending_cookies", [])

    def _student_cookie(self) -> str | None:
        cookies = getattr(self, "_cookies", None)
        if cookies and "juno_student" in cookies:
            return cookies["juno_student"].value
        return None

    def _resolve_student(self, data: dict) -> dict:
        """Work out which student this request belongs to.

        Identity is no longer the typed name. In order of authority:

        1. An individual access code, if the student has one. This identifies
           them exactly, on any device — the way out of the shared-passphrase
           pilot.
        2. A long-lived `juno_student` cookie holding their internal id. Two
           students both called Maria, on their own machines, are two records
           that never touch each other's memory.
        3. Otherwise a new student is created.

        The typed name only ever sets `display_name`, so a student can correct
        their own spelling without becoming a different person and losing
        everything Juno has learned about them.
        """
        code = (data.get("access_code") or "").strip()
        if code:
            found = store.student_by_access_code(code)
            if found:
                student_id = found["student_id"]
                self._set_cookie("juno_student", student_id, STUDENT_COOKIE_MAX_AGE)
                typed = (data.get("student") or "").strip()
                if typed and typed != found["display_name"]:
                    store.set_display_name(student_id, typed)
                store.touch_student(student_id)
                return store.get_student(student_id)

        typed = (data.get("student") or "").strip()
        cookie_id = self._student_cookie()
        if cookie_id:
            known = store.get_student(cookie_id)
            if known:
                if typed and typed != known["display_name"]:
                    store.set_display_name(cookie_id, typed)
                store.touch_student(cookie_id)
                return store.get_student(cookie_id)

        student_id = store.create_student(typed or "Student")
        self._set_cookie("juno_student", student_id, STUDENT_COOKIE_MAX_AGE)
        return store.get_student(student_id)

    def _send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for cookie in self._cookie_headers():
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for cookie in self._cookie_headers():
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._load_session()
        if self.path in ("/", "/index.html"):
            self._send_bytes(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/admin" and ADMIN_CODE:
            self._send_bytes(ADMIN_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path.partition("?")[0] in ("/r/confirm", "/r/stop"):
            self._handle_reminder_link()
        elif self.path == "/manifest.webmanifest":
            self._send_bytes(json.dumps(MANIFEST).encode("utf-8"), "application/manifest+json")
        elif self.path == "/sw.js":
            self._send_bytes(SERVICE_WORKER.encode("utf-8"), "text/javascript; charset=utf-8")
        elif self.path in STATIC_FILES:
            name, content_type = STATIC_FILES[self.path]
            self._send_bytes((STATIC_DIR / name).read_bytes(), content_type)
        elif self.path == "/scenarios.json":
            self._send_bytes(tutor.SCENARIOS_PATH.read_bytes(), "application/json")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        session = self._load_session()
        if self.path.partition("?")[0] == "/r/stop":
            # Mail programs' one-click unsubscribe: a form post, not JSON.
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length:
                self.rfile.read(length)
            self._handle_reminder_link()
            return
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw or b"{}")

            if self.path == "/api/auth":
                self._handle_auth(session, data)
            elif self.path == "/api/start":
                self._require_auth(session)
                self._handle_start(session, data)
            elif self.path == "/api/message":
                self._require_auth(session)
                self._handle_message(session, data)
            elif self.path == "/api/end":
                self._require_auth(session)
                self._handle_end(session, data)
            elif self.path == "/api/resume":
                self._require_auth(session)
                self._handle_resume(session, data)
            elif self.path == "/api/abandon":
                self._require_auth(session)
                self._handle_abandon(session, data)
            elif self.path == "/api/progress":
                self._require_auth(session)
                self._handle_progress(session, data)
            elif self.path == "/api/reminders":
                self._require_auth(session)
                self._handle_reminder_settings(session, data)
            elif self.path == "/api/cron/reminders":
                self._handle_cron(data)
            elif self.path == "/api/notes":
                self._require_auth(session)
                self._handle_notes(session, data)
            elif self.path == "/api/feedback":
                self._require_auth(session)
                self._handle_feedback(session, data)
            elif self.path == "/api/admin":
                self._handle_admin(data)
            elif self.path == "/api/help":
                self._require_auth(session)
                self._handle_help(session, data)
            elif self.path == "/api/define":
                self._require_auth(session)
                self._handle_define(session, data)
            else:
                self._send_json({"error": "not found"}, 404)
        except _AuthError:
            self._send_json({"error": "Not authenticated."}, 401)
        except tutor.UnknownScenario as e:
            # Bad input from the page, not a server fault - usually a stale
            # tab holding scenario ids from an older scenarios.json.
            self._send_json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            # The detail goes to the server log, not to the browser. An
            # exception message can carry request context, internal paths, or
            # whatever a library chose to interpolate into it - none of which
            # a student's browser should ever receive. They get a sentence
            # they can act on; the operator gets the traceback.
            print(f"[juno] unhandled error on {self.path}: {e!r}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            self._send_json(
                {"error": "Something went wrong on our side. Please try again — "
                          "your class is saved."},
                500,
            )

    def _require_auth(self, session: dict) -> None:
        if not session.get("authed"):
            raise _AuthError()

    def _handle_auth(self, session: dict, data: dict) -> None:
        if not ACCESS_PASSPHRASE:
            session["authed"] = True
            self._send_json({"ok": True})
            return
        def norm(text: str) -> bytes:
            # "SR 2026", "sr2026 " and "Sr2026" are all what the teacher said.
            return "".join(str(text).split()).casefold().encode("utf-8")

        if secrets.compare_digest(norm(data.get("passphrase") or ""), norm(ACCESS_PASSPHRASE)):
            session["authed"] = True
            self._send_json({"ok": True})
        else:
            self._send_json({"error": "Wrong passphrase."}, 401)

    def _call_model(self, system_blocks, messages, *, student_id, call_id,
                    kind: str) -> str:
        """One turn against the model: cached if possible, billed either way.

        Caching is an optimisation, never a dependency. If the API rejects the
        cached shape — an unsupported block layout, a prefix under the model's
        minimum, a change on their side — the class carries on uncached rather
        than failing in front of a student. That fallback is the whole reason
        this is one function instead of an inline call.
        """
        flat = "\n".join(b["text"] for b in system_blocks)
        try:
            response = client.messages.create(
                model=tutor.MODEL, max_tokens=1024,
                system=system_blocks, messages=messages,
                cache_control={"type": "ephemeral"},
            )
        except anthropic.BadRequestError as e:
            print(f"[juno] cache-shape rejected, retrying uncached: {e}",
                  file=sys.stderr)
            response = client.messages.create(
                model=tutor.MODEL, max_tokens=1024,
                system=flat, messages=messages,
            )

        self._record_metrics(response, student_id=student_id, call_id=call_id,
                             kind=kind)
        return next(b.text for b in response.content if b.type == "text")

    def _record_metrics(self, response, *, student_id: str, call_id: str,
                        kind: str) -> None:
        """Log what a turn cost, and bank it against the student's limits.

        Deliberately narrow: counts, an id, and money. No transcript, no
        message text, no name, no key — a log that carries the class content
        would be a second copy of the student's data in a place nobody is
        guarding.
        """
        # Wrapped whole: a student in the middle of a class must never lose it
        # because the accounting hit something unexpected. Observability is
        # worth having, never worth a failed turn.
        try:
            usage = getattr(response, "usage", None)
            cost = store.estimate_cost(usage)
            store.record_usage(student_id, cost, turns=1)

            def _n(attr):
                return int(getattr(usage, attr, 0) or 0) if usage else 0

            print(json.dumps({
                "event": "turn",
                "kind": kind,
                "call_id": call_id,
                "student_id": student_id,   # internal random id, not a name
                "input_tokens": _n("input_tokens"),
                "cache_read_tokens": _n("cache_read_input_tokens"),
                "cache_write_tokens": _n("cache_creation_input_tokens"),
                "output_tokens": _n("output_tokens"),
                "cost_usd": round(cost, 6),
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }), file=sys.stderr)

            spend = store.daily_spend()
            if (store.DAILY_COST_CEILING_USD > 0
                    and spend >= store.DAILY_COST_CEILING_USD * 0.8):
                print(f"[juno] WARNING: today's spend is ${spend:.2f} of the "
                      f"${store.DAILY_COST_CEILING_USD:.2f} ceiling",
                      file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[juno] metrics failed (class unaffected): {e}", file=sys.stderr)

    def _handle_start(self, session: dict, data: dict) -> None:
        student = self._resolve_student(data)
        student_id = student["student_id"]

        try:
            store.check_can_start_call(student_id)
        except store.LimitReached as e:
            self._send_json({"error": str(e)}, 429)
            return

        self._close_unfinished_classes(student_id)
        memory = tutor.load_student(student_id)
        memory["display_name"] = student["display_name"]
        mode = data.get("mode") if data.get("mode") in ("business", "structured") else "free"

        level = data.get("level")
        if level not in tutor.LEVEL_RULES:
            level = memory.get("cefr_level", "B1").rstrip("+")
        if level not in tutor.LEVEL_RULES:
            level = "B1"

        scenario = tutor.pick_scenario(data.get("scenario_id"), data.get("pack")) if mode == "business" else None

        # The cached half depends only on the level; the student's memory and
        # the scenario go in the uncached half.
        system_blocks = tutor.build_system_blocks(level, mode, scenario, memory,
                                                  easier=bool(data.get("easier")))
        messages = [{"role": "user", "content": "(the call has just connected — open it)"}]

        call_id = store.create_call(
            student_id, mode, level,
            scenario["id"] if scenario else None,
            json.dumps(system_blocks), messages,
        )
        store.record_call_started(student_id)

        reply = self._call_model(system_blocks, messages,
                                 student_id=student_id, call_id=call_id,
                                 kind="open")
        messages.append({"role": "assistant", "content": reply})
        store.save_turn(call_id, messages, 0)

        session["call_id"] = call_id
        self._send_json({
            "reply": reply,
            "call_id": call_id,
            "scenario": scenario["title"] if scenario else None,
            "display_name": student["display_name"],
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })

    def _current_call(self, session: dict) -> dict | None:
        call_id = session.get("call_id")
        if not call_id:
            return None
        call = store.get_call(call_id)
        return call if call and call["status"] == "open" else None

    def _handle_message(self, session: dict, data: dict) -> None:
        # A retry of a request we already answered replays the stored answer
        # instead of asking the model again: no duplicated turn in the
        # transcript, and nothing billed twice.
        key = (data.get("idempotency_key") or "").strip()[:80]
        replayed = store.replayed_response(key) if key else None
        if replayed is not None:
            self._send_json(replayed)
            return

        call = self._current_call(session)
        if not call:
            self._send_json({"error": "No class in progress. Start one first."}, 400)
            return

        text = (data.get("text") or "").strip()
        if not text:
            self._send_json({"error": "Empty message."}, 400)
            return
        if len(text) > store.MAX_MESSAGE_CHARS:
            self._send_json({"error": "That message is too long to send."}, 400)
            return

        try:
            store.check_can_send_turn(call)
        except store.LimitReached as e:
            self._send_json({"error": str(e)}, 429)
            return

        student_id = call["student_id"]
        system_blocks = json.loads(call["system"])
        messages = call["transcript"] + [{"role": "user", "content": text}]

        reply = self._call_model(system_blocks, messages,
                                 student_id=student_id, call_id=call["call_id"],
                                 kind="turn")
        messages.append({"role": "assistant", "content": reply})

        turns = call["turns"] + 1
        store.save_turn(call["call_id"], messages, turns)

        payload = {
            "reply": reply,
            "turns": turns,
            "turns_left": max(0, store.MAX_TURNS_PER_CALL - turns),
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if key:
            store.remember_response(key, call["call_id"], payload)
        self._send_json(payload)

    def _handle_resume(self, session: dict, data: dict) -> None:
        """Hand back an unfinished class so it can be picked up again.

        Looked up by student rather than by browser session, so it survives a
        closed tab, a different device, and a server restart.
        """
        student = self._resolve_student(data)
        last = None
        history = tutor.load_student(student["student_id"]).get("session_history") or []
        if history:
            last = {"date": history[-1].get("date"),
                    "what_we_did": history[-1].get("what_we_did"),
                    "next_recommendation": history[-1].get("next_recommendation")}
        call = store.open_call_for(student["student_id"])
        name = student["display_name"]
        level = store.level_label(student["student_id"])
        email_reminders = dict(store.reminder_settings(student["student_id"]), available=emailer.configured())
        if not call:
            self._send_json({"open_call": None, "last_session": last, "name": name, "level": level,
                             "reminders": email_reminders})
            return
        session["call_id"] = call["call_id"]
        self._send_json({"last_session": last, "name": name, "level": level,
                         "reminders": email_reminders, "open_call": {
            "call_id": call["call_id"],
            "mode": call["mode"],
            "level": call["level"],
            "turns": call["turns"],
            "started_at": call["started_at"],
            "updated_at": call["updated_at"],
            "transcript": call["transcript"][1:],  # drop the synthetic opener
        }})

    def _reminder_links(self, token: str) -> tuple[str, str]:
        return autoreminders.links(self._site_url(), token)

    def _handle_reminder_link(self) -> None:
        """Confirm or stop email reminders from the link in an email."""
        action = self.path.partition("?")[0]
        query = self.path.partition("?")[2]
        token = dict(part.partition("=")[::2] for part in query.split("&") if part).get("t", "")
        student = store.student_by_reminder_token(token)
        site = self._site_url()
        if not student:
            body = _notice_page("Enlace no válido",
                                "Este enlace ya no sirve. Puedes volver a pedir los recordatorios desde Juno.", site)
            status = 404
        elif action == "/r/confirm":
            store.set_email_state(student["student_id"], "on")
            body = _notice_page("Recordatorios activados",
                                "Gracias. Recibirás como máximo un correo al día, solo si ese día no has practicado.", site)
            status = 200
        else:
            store.set_email_state(student["student_id"], "stopped")
            body = _notice_page("No recibirás más correos",
                                "Listo, hemos parado los recordatorios. Puedes volver a pedirlos cuando quieras desde Juno.", site)
            status = 200
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _handle_reminder_settings(self, session: dict, data: dict) -> None:
        """A student asks for (or stops) email reminders."""
        if not emailer.configured():
            self._send_json({"error": "Email reminders are not switched on."}, 404)
            return
        student = self._resolve_student(data)
        sid = student["student_id"]
        if data.get("action") == "stop":
            store.set_email_state(sid, "stopped")
            self._send_json({"state": "stopped"})
            return
        try:
            hour = int(data.get("hour"))
            tz_offset = int(data.get("tz_offset") or 0)
        except (TypeError, ValueError):
            self._send_json({"error": "Choose an hour of the day."}, 400)
            return
        try:
            saved = store.save_reminder_settings(sid, data.get("email") or "", hour, tz_offset)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
            return
        if saved["state"] == "pending":
            now = time.time()
            last = REMINDER_CONFIRM_SENT.get(sid, 0)
            if now - last < 120:
                self._send_json({"error": "We just sent you an email. Check your inbox, or try again in a couple of minutes."}, 429)
                return
            confirm_url, stop_url = self._reminder_links(saved["token"])
            subject, text = emailer.confirmation(student["display_name"], saved["hour"], confirm_url, stop_url)
            try:
                emailer.send(saved["email"], subject, text, unsubscribe_url=stop_url)
            except emailer.SendFailed as e:
                print(f"[juno] confirmation email not sent: {e}", file=sys.stderr, flush=True)
                self._send_json({"error": "We could not send the email right now. Please try again later."}, 503)
                return
            REMINDER_CONFIRM_SENT[sid] = now
        self._send_json({"state": saved["state"], "email": saved["email"], "hour": saved["hour"]})

    def _handle_cron(self, data: dict) -> None:
        """Called by the outside timer. Behind its own secret, and absent without one."""
        secret = os.environ.get("JUNO_CRON_SECRET", "")
        if not secret:
            self._send_json({"error": "not found"}, 404)
            return
        given = self.headers.get("X-Cron-Secret", "")
        if not secrets.compare_digest(given, secret):
            time.sleep(0.5)
            self._send_json({"error": "Wrong secret."}, 401)
            return
        result = {"classes_closed": close_stale_classes()}
        if emailer.configured():
            result.update(autoreminders.run(self._site_url()))
        self._send_json(result)

    def _handle_notes(self, session: dict, data: dict) -> None:
        """Every finished class's session notes, for the Notes tab."""
        student = self._resolve_student(data)
        self._send_json({"notes": store.notes_for(student["student_id"])})

    def _handle_progress(self, session: dict, data: dict) -> None:
        """The numbers behind the Progress screen. All of it is read from
        saved classes and Juno's memory of the student - nothing is estimated."""
        student = self._resolve_student(data)
        try:
            tz_offset = int(data.get("tz_offset") or 0)
        except (TypeError, ValueError):
            tz_offset = 0
        self._send_json(progress_payload(student, tz_offset))

    def _handle_end(self, session: dict, data: dict) -> None:
        # Accept an explicit call_id so a class can be closed and reported on
        # later, from a different tab, without ever having pressed End call.
        call_id = (data.get("call_id") or "").strip() or session.get("call_id")
        call = store.get_call(call_id) if call_id else None
        if not call:
            self._send_json({"error": "No class to finish."}, 400)
            return
        if call["status"] != "open":
            if call.get("report"):
                self._send_json({"report": call["report"]})
                return
            self._send_json({"error": "That class is already closed."}, 400)
            return

        # The report is its own request, with its own tools and no cache
        # marker - as it always was.
        report = tutor.generate_report(client, call["transcript"])

        memory = tutor.load_student(call["student_id"])
        memory = tutor.apply_report_to_student(
            memory, report, call["mode"],
            {"id": call["scenario_id"]} if call["scenario_id"] else None,
        )
        tutor.save_student(memory)
        store.finish_call(call["call_id"], report)
        session["call_id"] = None
        self._send_json({"report": report})

    def _handle_admin(self, data: dict) -> None:
        """Personal codes: list students, create one, or give one to an existing
        student. Behind its own code, and absent entirely unless one is set."""
        if not ADMIN_CODE:
            self._send_json({"error": "not found"}, 404)
            return
        if not secrets.compare_digest(str(data.get("admin_code") or ""), ADMIN_CODE):
            time.sleep(0.5)  # slows down guessing
            self._send_json({"error": "Wrong teacher code."}, 401)
            return
        action = data.get("action")
        if action == "list":
            try:
                tz_offset = int(data.get("tz_offset") or 0)
            except (TypeError, ValueError):
                tz_offset = 0
            site = self._site_url()
            students = []
            for facts in store.students_overview(tz_offset):
                # Placeholder records from visitors who never gave a name or code.
                if not (facts["access_code"] or facts["phone"] or facts["classes"]
                        or facts["display_name"] != "Student"):
                    continue
                facts["reminder"] = reminders.suggest(facts, site)
                students.append(facts)
            students.sort(key=lambda f: f["reminder"]["kind"] not in reminders.KINDS_THAT_SEND)
            self._send_json({"students": students, "site_url": site})
        elif action == "run_reminders":
            result = {"classes_closed": close_stale_classes()}
            if emailer.configured():
                result.update(autoreminders.run(self._site_url()))
            self._send_json(result)
        elif action == "progress":
            student = store.get_student(str(data.get("student_id") or ""))
            if not student:
                self._send_json({"error": "No such student."}, 404)
                return
            try:
                tz_offset = int(data.get("tz_offset") or 0)
            except (TypeError, ValueError):
                tz_offset = 0
            self._send_json(progress_payload(student, tz_offset))
        elif action == "create":
            name = (data.get("name") or "").strip()
            if not name:
                self._send_json({"error": "Type the student's name first."}, 400)
                return
            try:
                phone = store.normalise_phone(data.get("phone") or "")
            except ValueError as e:
                self._send_json({"error": str(e)}, 400)
                return
            code = store.make_access_code(name)
            student_id = store.create_student(name, access_code=code)
            if phone:
                store.set_phone(student_id, phone)
            self._send_json({"access_code": code})
        elif action in ("assign", "set_phone", "set_paused", "mark_reminded"):
            student = store.get_student(str(data.get("student_id") or ""))
            if not student:
                self._send_json({"error": "No such student."}, 404)
                return
            sid = student["student_id"]
            if action == "assign":
                code = store.set_access_code(sid, store.make_access_code(student["display_name"]))
                self._send_json({"access_code": code})
            elif action == "set_phone":
                try:
                    self._send_json({"phone": store.set_phone(sid, str(data.get("phone") or ""))})
                except ValueError as e:
                    self._send_json({"error": str(e)}, 400)
            elif action == "set_paused":
                store.set_reminders_paused(sid, bool(data.get("paused")))
                self._send_json({"ok": True})
            else:
                store.mark_reminded(sid)
                self._send_json({"ok": True})
        else:
            self._send_json({"error": "Unknown action."}, 400)

    def _site_url(self) -> str:
        """Where students open Juno, for the links inside reminders."""
        configured = os.environ.get("JUNO_PUBLIC_URL", "").strip().rstrip("/")
        if configured:
            return configured
        host = (self.headers.get("Host") or "").strip()
        if not host:
            return ""
        local = host.startswith(("localhost", "127.", "0.0.0.0"))
        return f"{'http' if local else 'https'}://{host}"

    def _close_unfinished_classes(self, student_id: str) -> None:
        """Fold a student's abandoned classes into their memory.

        A student who closes the tab and later starts a new class never
        pressed End, so what that class taught Juno about them was lost. Do it
        for them now, before the new class reads their memory. A failure here
        must never stop the new class from starting.
        """
        try:
            for call in store.open_calls_for(student_id):
                fold_unfinished_class(call)
        except Exception as e:  # noqa: BLE001
            print(f"[juno] could not close an old class (new class unaffected): {e!r}",
                  file=sys.stderr, flush=True)

    def _handle_help(self, session: dict, data: dict) -> None:
        """Explain, in Spanish, what Juno just asked and how to answer it.

        An aside, not a turn: nothing is added to the class transcript, so the
        conversation and the report are unchanged by asking for help.
        """
        call = self._current_call(session)
        if not call:
            self._send_json({"error": "No class in progress."}, 400)
            return
        if (store.DAILY_COST_CEILING_USD > 0
                and store.daily_spend() >= store.DAILY_COST_CEILING_USD):
            self._send_json({"error": "Juno has reached today's usage limit. "
                                      "Please try again tomorrow."}, 429)
            return
        used = session.setdefault("help_used", {}).get(call["call_id"], 0)
        if used >= MAX_HELP_PER_CALL:
            self._send_json({"error": "Ya has pedido mucha ayuda en esta clase. "
                                      "Inténtalo con tus palabras: no pasa nada "
                                      "si no es perfecto."}, 429)
            return

        recent = call["transcript"][1:][-6:]  # skip the synthetic opener
        if not recent:
            self._send_json({"error": "Nothing to help with yet."}, 400)
            return
        lines = "\n".join(
            f"{'Juno' if m['role'] == 'assistant' else 'Student'}: {m['content']}"
            for m in recent
        )

        response = client.beta.messages.create(
            model=tutor.MODEL,
            max_tokens=2000,
            system=HELP_SYSTEM.format(level=call["level"]),
            messages=[{"role": "user",
                       "content": f"The class so far, most recent last:\n\n{lines}"}],
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        self._record_metrics(response, student_id=call["student_id"],
                             call_id=call["call_id"], kind="help")
        text = next((b.text for b in response.content if b.type == "text"), "").strip()
        if response.stop_reason == "refusal" or not text:
            text = ("Ahora mismo no puedo ayudarte con esto. "
                    "Intenta responder con una frase sencilla.")
        session["help_used"][call["call_id"]] = used + 1
        explanation, answers = [], []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith(">>"):
                english, _, spanish = line[2:].partition("||")
                if english.strip():
                    answers.append({"en": english.strip(), "es": spanish.strip()})
            elif line:
                explanation.append(line)
        self._send_json({"help": text, "explanation": " ".join(explanation), "answers": answers[:3]})

    def _handle_define(self, session: dict, data: dict) -> None:
        """What one word Juno said means, in Spanish, with a short example."""
        call = self._current_call(session)
        if not call:
            self._send_json({"error": "No class in progress."}, 400)
            return
        word = "".join(c for c in str(data.get("word") or "") if c.isalpha() or c in "'’-")
        word = word.strip("'’-")[:40]
        if not word:
            self._send_json({"error": "No word to look up."}, 400)
            return
        sentence = " ".join(str(data.get("sentence") or "").split())[:300]
        key = (word.lower(), sentence.lower())
        if key in DEFINE_CACHE:
            self._send_json(DEFINE_CACHE[key])
            return
        if (store.DAILY_COST_CEILING_USD > 0
                and store.daily_spend() >= store.DAILY_COST_CEILING_USD):
            self._send_json({"error": "Juno has reached today's usage limit. "
                                      "Please try again tomorrow."}, 429)
            return
        counts = session.setdefault("lookups", {})
        today = store._today()
        if counts.get(today, 0) >= MAX_LOOKUPS_PER_DAY:
            self._send_json({"error": "Has consultado muchas palabras hoy. "
                                      "Pregúntale a Juno: What does it mean?"}, 429)
            return
        response = client.beta.messages.create(
            model=tutor.MODEL,
            max_tokens=300,
            system=DEFINE_SYSTEM,
            messages=[{"role": "user", "content": f"Sentence: {sentence}\nWord tapped: {word}"}],
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        self._record_metrics(response, student_id=call["student_id"],
                             call_id=call["call_id"], kind="define")
        counts[today] = counts.get(today, 0) + 1
        text = next((b.text for b in response.content if b.type == "text"), "").strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if response.stop_reason == "refusal" or not lines:
            self._send_json({"error": "Ahora mismo no puedo explicar esta palabra."}, 502)
            return
        result = {"word": word, "meaning": lines[0], "example": lines[1] if len(lines) > 1 else ""}
        if len(DEFINE_CACHE) > 2000:
            DEFINE_CACHE.clear()
        DEFINE_CACHE[key] = result
        self._send_json(result)

    def _handle_abandon(self, session: dict, data: dict) -> None:
        call = self._current_call(session)
        if call:
            store.abandon_call(call["call_id"])
        session["call_id"] = None
        self._send_json({"ok": True})

    def _handle_feedback(self, session: dict, data: dict) -> None:
        text = (data.get("text") or "").strip()
        if not text:
            self._send_json({"error": "Feedback can't be empty."}, 400)
            return
        entry = {
            "text": text[:MAX_FEEDBACK_LENGTH],
            "student": (data.get("student") or "").strip() or None,
            "mode": (data.get("mode") or "").strip() or None,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }
        if store.using_postgres():
            store.add_feedback(entry)
        else:
            FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
            with FEEDBACK_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        self._send_json({"ok": True})

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        pass  # keep stdout quiet; errors still surface in the browser


class _AuthError(Exception):
    pass


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY first, same as with tutor.py.")
    if RUNNING_DEPLOYED and not ACCESS_PASSPHRASE:
        sys.exit(
            "Running with PORT set (looks like a real deployment) but "
            "JUNO_ACCESS_PASSPHRASE is not set. Refusing to start unprotected on "
            "the public internet — set that environment variable first."
        )

    # Open the database and bring any name-keyed memory files across to real
    # student ids. The migration copies rather than moves, so the old files
    # stay untouched, and it is safe to run on every boot.
    store.connect()
    if store.using_postgres():
        tutor.MEMORY_STORE = store
        print("Juno is saving everything to the Postgres database.")
    migrated = store.migrate_legacy_students(tutor.STUDENTS_DIR)
    if store.using_postgres():
        imported = store.import_memory_files(tutor.STUDENTS_DIR)
        if imported:
            print(f"Copied {imported} student memory file(s) into the database.")
    for entry in migrated:
        print(f"Migrated student memory '{entry['legacy_id']}' "
              f"-> {entry['student_id']} ({entry['display_name']})")
    store.prune_idempotency()

    url = f"http://127.0.0.1:{PORT}" if not RUNNING_DEPLOYED else f"port {PORT}"

    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        sys.exit(
            f"Could not start — port {PORT} is already in use.\n"
            "This almost always means an old copy of this server is still running in "
            "another Terminal window or tab. Find that window and press Ctrl+C there, "
            "or close all Terminal windows and try again. If it's already running fine, "
            f"just open {url} in your browser instead of starting a new one."
        )

    if RUNNING_DEPLOYED:
        print(f"Juno is running on {url} (access code {'required' if ACCESS_PASSPHRASE else 'NOT required — set JUNO_ACCESS_PASSPHRASE'}).")
    else:
        print(f"Juno is running. Opening {url} in your browser now.")
        print("Leave this Terminal window open. Press Ctrl+C here to stop it when you're done.")
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
