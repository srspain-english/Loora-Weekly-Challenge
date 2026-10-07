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
import traceback
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anthropic

import store  # identity, limits, saved calls, cost metrics
import tutor  # the exact tested logic: prompts, tiers, report schema

RUNNING_DEPLOYED = bool(os.environ.get("PORT"))
HOST = "0.0.0.0" if RUNNING_DEPLOYED else "127.0.0.1"
PORT = int(os.environ.get("PORT", 8765))
ACCESS_PASSPHRASE = os.environ.get("JUNO_ACCESS_PASSPHRASE", "")
MAX_FEEDBACK_LENGTH = 2000
FEEDBACK_PATH = tutor.DATA_DIR / "feedback.jsonl"
# A year: the point of this cookie is that a student stays the same person
# between classes without having to be given a code first.
STUDENT_COOKIE_MAX_AGE = 365 * 24 * 3600

# "Help me" during a class. Capped per class: each press is a paid request.
MAX_HELP_PER_CALL = 15
HELP_SYSTEM = (
    "A Spanish-speaking adult is in a live English conversation class with an "
    "AI teacher called Juno. They pressed 'Help me' because they don't know "
    "how to answer. Reply in Spanish, in plain text with no markdown, in under "
    "90 words:\n"
    "1. What Juno just said or asked, explained simply.\n"
    "2. One or two short answers in English they could say, suited to CEFR "
    "level {level}, each followed by its meaning in Spanish in brackets.\n"
    "Don't correct the student and don't continue the conversation yourself."
)

# Per-browser-session state, keyed by a random cookie value. Necessary as
# soon as more than one person can reach this server at once - a single
# global dict (fine for one local user) would let concurrent students
# overwrite each other's calls.
SESSIONS: dict[str, dict] = {}

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0E131D">
<title>Juno Teaching Assistant</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@600;700&family=Instrument+Sans:wght@400;500;600&family=Instrument+Serif&display=swap');

  :root{
    color-scheme:dark;
    --bg:#0E131D; --surface:#171E2B; --surface-2:#1F2737;
    --line:rgba(255,255,255,.09);
    --ink:#F4EEE4; --ink-soft:#C9CCD3; --muted:#8A92A2;
    --accent:#FF4A1C; --accent-hi:#FF8A5C; --accent-tint:rgba(255,74,28,.12);
    --ok:#5FD0A0; --warn:#E3A33B; --bad:#FF7A6B;
  }
  *{box-sizing:border-box}
  [hidden]{display:none !important}
  html,body{margin:0}
  body{
    min-height:100vh;min-height:100dvh;color:var(--ink-soft);
    background-color:var(--bg);
    background-image:radial-gradient(120% 60% at 50% 0%, #1E2638 0%, var(--bg) 62%);
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
  .live-dot{width:7px;height:7px;border-radius:50%;background:var(--accent);box-shadow:0 0 10px var(--accent);flex-shrink:0}
  .brand-mark{color:var(--ink)}
  #feedback-btn{margin-left:auto;padding:7px 14px;font-size:12px;letter-spacing:.04em;text-transform:none}
  .lede{font-family:'Instrument Serif',Georgia,serif;color:var(--ink);font-size:30px;line-height:1.2;margin:26px 0 24px}
  body.in-call .topbar, body.in-call .lede{display:none}
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
  #setup-screen, #chat-screen, #report-screen{display:none}

  .status{display:flex;align-items:center;justify-content:center;gap:7px;font-size:12.5px;color:var(--muted);margin-top:10px;min-height:18px;text-align:center}
  .status .dot{width:7px;height:7px;border-radius:50%;background:var(--muted);flex-shrink:0}
  .status:has(#chat-status-text:empty) .dot, .status:has(#setup-status-text:empty) .dot{display:none}
  .status.waking .dot{background:var(--warn);animation:pulse 1.4s ease-in-out infinite}
  .status.offline .dot{background:var(--bad)}
  .status.offline{color:var(--ink-soft)}
  .status.saved .dot{background:var(--ok)}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}

  .notice{background:var(--accent-tint);border:1px solid rgba(255,74,28,.45);border-radius:16px;padding:16px 18px;margin-bottom:16px;font-size:14px}
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
    display:flex;align-items:center;justify-content:center;color:#2A120A;
    background:radial-gradient(circle at 34% 28%, #FFC6AA 0%, #FF8A5C 24%, #FF4A1C 58%, #C2330E 100%);
    box-shadow:0 20px 70px rgba(255,74,28,.32), inset 0 -12px 26px rgba(0,0,0,.18);
    transition:transform .15s ease, filter .3s ease;
  }
  #mic-btn::before{content:"";position:absolute;inset:-20px;border-radius:50%;border:1px solid rgba(255,138,92,.28);pointer-events:none}
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
  #chat-screen:has(#help-card:not([hidden])) #mic-btn, #chat-screen:has(#type-row:not([hidden])) #mic-btn{--orb:clamp(76px,12vh,116px)}
  #chat-screen:has(#help-card:not([hidden])) #mic-btn::before, #chat-screen:has(#type-row:not([hidden])) #mic-btn::before{inset:-12px}
  #chat-screen:has(#help-card:not([hidden])) .juno-line, #chat-screen:has(#type-row:not([hidden])) .juno-line{font-size:22px;line-height:1.3;max-height:20vh;margin-bottom:22px}
  #chat-screen:has(#help-card:not([hidden])) .orb-state, #chat-screen:has(#type-row:not([hidden])) .orb-state{margin-top:14px}
  .help-card{
    background:var(--surface-2);border:1px solid rgba(255,74,28,.4);border-radius:16px;
    padding:14px 16px;margin-top:10px;font-size:15px;line-height:1.5;color:var(--ink);
    max-height:32vh;overflow-y:auto;flex-shrink:0;
  }
  .help-head{display:flex;align-items:center;justify-content:space-between;font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--accent-hi);margin-bottom:6px}
  #help-text{white-space:pre-line}

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
  .foot-toggles{display:flex;gap:16px;flex-wrap:wrap;justify-content:flex-end}
  .call-foot label{margin:0;text-transform:none;letter-spacing:0;font-size:13px;display:flex;align-items:center;gap:6px;cursor:pointer}
  .link{background:none;border:none;border-radius:0;padding:4px 0;color:var(--ink-soft);font-family:inherit;font-weight:500;font-size:13px;text-decoration:underline;text-underline-offset:3px}

  /* Report */
  .recap-title{font-family:'Instrument Serif',Georgia,serif;font-weight:400;font-size:30px;margin:0 0 6px}
  .recap h3{font-size:14px;margin:22px 0 8px}
  .recap p{margin:0;line-height:1.55}
  .recap table{width:100%;border-collapse:collapse;font-size:14px}
  .recap td{padding:8px 4px;border-bottom:1px solid var(--line);vertical-align:top;line-height:1.5}
  .recap ul{margin:4px 0;padding-left:20px;font-size:14px;line-height:1.55}
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

  <div id="resume-notice" class="notice" style="display:none">
    <h2>You have an unfinished class</h2>
    <p id="resume-detail">Started earlier and never closed.</p>
    <div class="row">
      <button id="resume-btn">Continue it</button>
      <button class="ghost" id="resume-report-btn">Just get my report</button>
      <button class="ghost" id="resume-discard-btn">Discard</button>
    </div>
  </div>

  <div id="setup-screen" class="panel">
    <label for="student">Your name</label>
    <input type="text" id="student" placeholder="e.g. Maria">

    <label for="student-code">Your personal code <span class="hint">(optional — lets Juno recognise you on any device)</span></label>
    <input type="text" id="student-code" placeholder="Leave empty if your teacher hasn't given you one">

    <label for="mode">Mode</label>
    <select id="mode">
      <option value="free">Free Conversation</option>
      <option value="business">Business English</option>
      <option value="structured">Structured Class</option>
    </select>

    <label for="level">Level</label>
    <select id="level">
      <option value="A2">A2</option>
      <option value="B1" selected>B1</option>
      <option value="B2">B2</option>
      <option value="C1">C1</option>
    </select>

    <div id="scenario-row" style="display:none">
      <label for="scenario">Scenario <span class="hint">(optional — random if left as "Surprise me")</span></label>
      <select id="scenario"><option value="">Surprise me</option></select>
    </div>

    <div class="row" style="margin-top:22px">
      <button id="start-btn" style="flex:1">Start class</button>
    </div>
    <div class="error" id="start-error"></div>
    <div class="status" id="setup-status"><span class="dot"></span><span id="setup-status-text"></span></div>

    <div class="privacy">
      <details>
        <summary>What Juno saves about you</summary>
        <ul>
          <li><b>What it keeps:</b> your first name, your level, the transcript of each class, and the report at the end — corrections, vocabulary, and what to work on next.</li>
          <li><b>What it is for:</b> so Juno remembers you between classes and does not repeat what you already know. Nothing else.</li>
          <li><b>Who can see it:</b> you, and your teacher at S&amp;R Spain. It is not shared with anyone else and is not used to train anything.</li>
          <li><b>Please do not type confidential information</b> — real client names, personal data about colleagues, anything under NDA. Practise with the situation, not the specifics.</li>
          <li><b>To have your data deleted:</b> ask your teacher and it is removed.</li>
        </ul>
      </details>
    </div>
  </div>

  <div id="chat-screen">
    <div class="call-top">
      <span class="live-dot"></span><span id="call-label">Juno · Free talk</span>
      <span id="call-clock">0:00</span>
    </div>

    <div class="stage">
      <p class="juno-line" id="juno-line" aria-live="polite"></p>
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
    <div class="call-foot">
      <button class="link" id="type-toggle">Type instead</button>
      <div class="foot-toggles">
        <label><input type="checkbox" id="handsfree-toggle" checked> Hands-free</label>
        <label><input type="checkbox" id="speak-toggle" checked> Juno speaks</label>
      </div>
    </div>
  </div>

  <div id="report-screen" class="panel">
    <h2 class="recap-title">Class recap</h2>
    <div id="recap" class="recap"></div>
    <div class="row" style="margin-top:24px">
      <button id="again-btn">New class</button>
    </div>
  </div>
</div>

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

function show(id) {
  ['auth-screen', 'setup-screen', 'chat-screen', 'report-screen'].forEach((s) => {
    $(s).style.display = s !== id ? 'none' : (s === 'chat-screen' ? 'flex' : 'block');
  });
  document.body.classList.toggle('in-call', id === 'chat-screen');
}

// --- The class screen ------------------------------------------------------
// Juno's newest line is shown large above the mic; everything before it
// goes into the transcript underneath.

let junoLine = '';

// Juno sometimes marks a correction with **double asterisks**. On screen
// that part is highlighted; out loud the symbols are dropped, or the
// voice reads them as "asterisk".
function fillRich(el, text) {
  el.textContent = '';
  text.split('**').forEach((part, i) => {
    const clean = part.replace(/[*#`]/g, '');
    if (i % 2) {
      const b = document.createElement('strong');
      b.textContent = clean;
      el.appendChild(b);
    } else {
      el.appendChild(document.createTextNode(clean));
    }
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
  fillRich(body, text);
  row.append(name, body);
  const log = $('chat-log');
  log.appendChild(row);
  log.scrollTop = log.scrollHeight;
}

function setJunoLine(text, waiting) {
  const el = $('juno-line');
  fillRich(el, text);
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

function speak(text) {
  if (!$('speak-toggle').checked || !window.speechSynthesis || paused) return false;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(speakable(text));
  u.lang = 'en-US';
  u.rate = 1;
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
    try { recognition.stop(); } catch (err) { /* already stopped */ }
    later(END_MS, () => finish());
  };

  function finish(reason) {
    if (!micBusy) return;
    micBusy = false;
    recognitionActive = false;
    clearTimers();
    $('heard').textContent = '';
    if (paused) { idleOrb(); return; }
    const said = heard.trim();
    heard = '';
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
      setStatus('chat-status', `${why} (${problem})`, 'offline');
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
    heard = '';
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
      setStatus('chat-status', "Voice isn't available in this browser. Type your answer instead.", 'offline');
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
    heard = text;
    $('heard').textContent = text;
    if (text.trim()) setOrb('listening', 'Listening… tap when you’re done');
    clearTimeout(silenceTimer);
    silenceTimer = setTimeout(() => { if (micBusy) askToStop(); }, SILENCE_MS);
  };
  recognition.onerror = (e) => { voiceError = e.error; };
  recognition.onend = () => finish();
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
    fillRich($('help-text'), data.help);
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
    if (!data.open_call) return;
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
  $('feedback-btn').style.display = 'inline-block';
  show('setup-screen');
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

function renderRecap(r) {
  const el = $('recap');
  const rows = (arr, fn) => arr.map(fn).join('');
  el.innerHTML = `
    <h3>What we did today</h3><p>${r.what_we_did}</p>
    <h3>Your corrections</h3>
    <table>${rows(r.corrections, (c) => `<tr><td class="tag">${c.tier}</td><td><b>${c.said}</b> → <b>${c.better}</b><br><span style="color:var(--muted)">${c.note}</span></td></tr>`)}</table>
    ${r.word_traps.length ? `<h3>Word traps</h3><ul>${rows(r.word_traps, (w) => `<li><b>${w.you_said}</b> (${w.problem}) → <b>${w.we_say}</b></li>`)}</ul>` : ''}
    ${r.pronunciation.length ? `<h3>Pronunciation</h3><ul>${rows(r.pronunciation, (p) => `<li>${p.word} ${p.ipa} — ${p.watch_for}</li>`)}</ul>` : ''}
    <h3>Vocabulary</h3><ul>${rows(r.vocabulary_learned, (v) => `<li><b>${v.term}</b> — ${v.meaning}</li>`)}</ul>
    <h3>What went well</h3><ul>${rows(r.what_went_well, (w) => `<li>${w}</li>`)}</ul>
    ${r.homework.length ? `<h3>Before next class</h3><ul>${rows(r.homework, (h) => `<li>${h}</li>`)}</ul>` : ''}
    <h3>Next session focus</h3><p>${r.next_recommendation}</p>
  `;
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
    classInProgress = false;
    stopClass();
    renderRecap(data.report);
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

$('again-btn').onclick = () => show('setup-screen');

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

client = anthropic.Anthropic()


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
        elif self.path == "/scenarios.json":
            self._send_bytes(tutor.SCENARIOS_PATH.read_bytes(), "application/json")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        session = self._load_session()
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
            elif self.path == "/api/feedback":
                self._require_auth(session)
                self._handle_feedback(session, data)
            elif self.path == "/api/help":
                self._require_auth(session)
                self._handle_help(session, data)
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
        if secrets.compare_digest(str(data.get("passphrase") or ""), ACCESS_PASSPHRASE):
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
        system_blocks = tutor.build_system_blocks(level, mode, scenario, memory)
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
        call = store.open_call_for(student["student_id"])
        if not call:
            self._send_json({"open_call": None})
            return
        session["call_id"] = call["call_id"]
        self._send_json({"open_call": {
            "call_id": call["call_id"],
            "mode": call["mode"],
            "level": call["level"],
            "turns": call["turns"],
            "started_at": call["started_at"],
            "updated_at": call["updated_at"],
            "transcript": call["transcript"][1:],  # drop the synthetic opener
        }})

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
        self._send_json({"help": text})

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
        FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
        with FEEDBACK_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        self._send_json({"ok": True})

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        pass  # keep stdout quiet; errors still surface in the browser


class _AuthError(Exception):
    pass


def main() -> None:
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
    migrated = store.migrate_legacy_students(tutor.STUDENTS_DIR)
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
