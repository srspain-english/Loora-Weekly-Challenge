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
FEEDBACK_PATH = tutor.BASE_DIR / "data" / "feedback.jsonl"
# A year: the point of this cookie is that a student stays the same person
# between classes without having to be given a code first.
STUDENT_COOKIE_MAX_AGE = 365 * 24 * 3600

# Per-browser-session state, keyed by a random cookie value. Necessary as
# soon as more than one person can reach this server at once - a single
# global dict (fine for one local user) would let concurrent students
# overwrite each other's calls.
SESSIONS: dict[str, dict] = {}

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Juno Teaching Assistant</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@600;700&family=Instrument+Sans:wght@400;500;600&display=swap');

  :root{
    --bg:#FFFDF9; --bg-sunk:#F3EEE1; --card:#FFFFFF;
    --ink:#17130E; --ink-soft:#3A332A; --muted:#8B8073;
    --line:#E8E0CE; --accent:#FF4A1C; --accent-tint:#FFF1E8;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink-soft);font-family:'Instrument Sans',system-ui,sans-serif}
  h1,h2{font-family:'Space Grotesk',sans-serif;color:var(--ink)}
  .wrap{max-width:640px;margin:0 auto;padding:28px 18px 60px}
  .brand{display:flex;align-items:baseline;gap:8px;margin-bottom:6px}
  .brand-mark{font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:18px}
  .brand-sub{color:var(--muted);font-size:13px}
  .lede{color:var(--muted);font-size:14px;margin:0 0 26px}
  .panel{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:22px}
  label{display:block;font-size:12.5px;color:var(--muted);margin:14px 0 6px;text-transform:uppercase;letter-spacing:.04em}
  label:first-child{margin-top:0}
  input[type=text], input[type=password], select, textarea{
    width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:10px;
    font-family:inherit;font-size:14px;background:var(--bg);color:var(--ink);
  }
  textarea{resize:vertical}
  button{
    font-family:'Space Grotesk',sans-serif;font-weight:600;font-size:14px;cursor:pointer;
    border:none;border-radius:999px;padding:11px 20px;background:var(--accent);color:#fff;
  }
  button:disabled{opacity:.5;cursor:default}
  button.ghost{background:transparent;color:var(--ink-soft);border:1px solid var(--line)}
  button.icon{
    width:44px;height:44px;padding:0;border-radius:50%;font-size:18px;
    display:flex;align-items:center;justify-content:center;flex-shrink:0;
  }
  button.icon.recording{background:#B0392A}
  .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  .error{color:#B0392A;font-size:13px;margin-top:10px}
  #auth-screen{display:block}
  #setup-screen{display:none}
  #chat-screen{display:none}
  #report-screen{display:none}
  .chat-log{
    height:420px;overflow-y:auto;border:1px solid var(--line);border-radius:14px;
    padding:16px;background:var(--bg-sunk);display:flex;flex-direction:column;gap:10px;
  }
  .bubble{max-width:82%;padding:10px 14px;border-radius:14px;font-size:14.5px;line-height:1.5}
  .bubble.juno{background:var(--card);border:1px solid var(--line);align-self:flex-start}
  .bubble.you{background:var(--ink);color:#F6F1E7;align-self:flex-end}
  .bubble .who{display:block;font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;opacity:.6;margin-bottom:3px}
  .composer{display:flex;gap:8px;margin-top:12px}
  .composer input[type=text]{
    flex:1;padding:11px 14px;border:1px solid var(--line);border-radius:999px;
    font-family:inherit;font-size:14.5px;background:var(--bg);color:var(--ink);
  }
  .voice-row{display:flex;align-items:center;gap:8px;margin-top:10px;font-size:12.5px;color:var(--muted)}
  .voice-row label{margin:0;text-transform:none;letter-spacing:0;display:flex;align-items:center;gap:6px;cursor:pointer}
  .recap h3{font-family:'Space Grotesk',sans-serif;font-size:14px;margin:20px 0 8px;color:var(--ink)}
  .recap h3:first-child{margin-top:0}
  .recap table{width:100%;border-collapse:collapse;font-size:13.5px}
  .recap td{padding:6px 4px;border-bottom:1px solid var(--line);vertical-align:top}
  .recap ul{margin:4px 0;padding-left:20px;font-size:13.8px}
  .tag{font-size:10px;text-transform:uppercase;color:var(--muted);font-family:ui-monospace,monospace}
  #feedback-btn{margin-left:auto;padding:6px 14px;font-size:12px}
  #feedback-panel{margin-bottom:18px}
  #feedback-thanks{color:var(--muted);font-size:13px;margin-top:8px}
  @media (prefers-color-scheme: dark){
    :root{
      --bg:#161310; --bg-sunk:#1E1A15; --card:#211D18;
      --ink:#F5EFE3; --ink-soft:#DCD3C2; --muted:#9C917F;
      --line:#332C23; --accent:#FF6A42; --accent-tint:#2E1E17;
    }
    .bubble.you{color:#17130E}
  }
</style>
</head>
<body>
<div class="wrap">
  <div class="brand">
    <span class="brand-mark">Juno</span><span class="brand-sub">Teaching Assistant</span>
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

  <div id="setup-screen" class="panel">
    <label for="student">Student name</label>
    <input type="text" id="student" placeholder="e.g. maria">

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
      <label for="scenario">Scenario <span style="text-transform:none;color:var(--muted)">(optional — random if left as "Surprise me")</span></label>
      <select id="scenario"><option value="">Surprise me</option></select>
    </div>

    <div class="row" style="margin-top:18px">
      <button id="start-btn">Start call</button>
    </div>
    <div class="error" id="start-error"></div>
  </div>

  <div id="chat-screen" class="panel">
    <div class="chat-log" id="chat-log"></div>
    <div class="composer">
      <button class="icon ghost" id="mic-btn" title="Hold to talk" style="display:none">🎤</button>
      <input type="text" id="msg-input" placeholder="Type your reply…">
      <button id="send-btn">Send</button>
    </div>
    <div class="voice-row">
      <label><input type="checkbox" id="speak-toggle" checked> Juno speaks replies aloud</label>
    </div>
    <div class="row" style="margin-top:12px;justify-content:flex-end">
      <button class="ghost" id="end-btn">End call</button>
    </div>
    <div class="error" id="chat-error"></div>
  </div>

  <div id="report-screen" class="panel">
    <h2 style="margin-top:0">Class recap</h2>
    <div id="recap" class="recap"></div>
    <div class="row" style="margin-top:20px">
      <button id="again-btn">New call</button>
    </div>
  </div>
</div>

<script>
const $ = (id) => document.getElementById(id);
let scenarios = null;
let recognition = null;
let recognitionActive = false;

async function api(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body || {}),
    credentials: 'same-origin',
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Something went wrong.');
  return data;
}

function show(id) {
  ['auth-screen', 'setup-screen', 'chat-screen', 'report-screen'].forEach(
    (s) => ($(s).style.display = s === id ? 'block' : 'none')
  );
}

function bubble(who, text) {
  const el = document.createElement('div');
  el.className = 'bubble ' + (who === 'juno' ? 'juno' : 'you');
  el.innerHTML = `<span class="who">${who === 'juno' ? 'Juno' : 'You'}</span>${text.replace(/</g, '&lt;')}`;
  const log = $('chat-log');
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
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
  if (!$('speak-toggle').checked || !window.speechSynthesis) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = 'en-US';
  u.rate = 1;
  u.pitch = 1;
  const voice = pickVoice(cachedVoices.length ? cachedVoices : window.speechSynthesis.getVoices());
  if (voice) u.voice = voice;
  window.speechSynthesis.speak(u);
}

function setupVoiceInput() {
  const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRec) return; // Safari and some browsers don't support this - mic stays hidden
  recognition = new SpeechRec();
  recognition.lang = 'en-US';
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;
  recognition.onresult = (e) => {
    $('msg-input').value = e.results[0][0].transcript;
  };
  recognition.onerror = () => { recognitionActive = false; $('mic-btn').classList.remove('recording'); };
  recognition.onend = () => { recognitionActive = false; $('mic-btn').classList.remove('recording'); };

  const micBtn = $('mic-btn');
  micBtn.style.display = 'flex';
  const start = (e) => {
    e.preventDefault();
    if (recognitionActive) return;
    recognitionActive = true;
    micBtn.classList.add('recording');
    try { recognition.start(); } catch (err) { /* already started, ignore */ }
  };
  const stop = (e) => {
    e.preventDefault();
    if (!recognitionActive) return;
    recognition.stop();
  };
  micBtn.addEventListener('mousedown', start);
  micBtn.addEventListener('touchstart', start);
  micBtn.addEventListener('mouseup', stop);
  micBtn.addEventListener('mouseleave', stop);
  micBtn.addEventListener('touchend', stop);
}

// Try an empty passphrase first - if no access code is configured server-side,
// this succeeds immediately and the auth screen never has to be shown.
api('/api/auth', { passphrase: '' }).then(() => {
  $('lede').textContent = 'A live practice call, corrected as you go.';
  afterAuth();
}).catch(() => {
  $('lede').textContent = 'A live practice call, corrected as you go. Enter the access code to begin.';
});

function afterAuth() {
  $('feedback-btn').style.display = 'inline-block';
  show('setup-screen');
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
  $('start-error').textContent = '';
  $('start-btn').disabled = true;
  try {
    const mode = $('mode').value;
    // "pack:<id>" means any scenario from that sector; a bare value is one
    // specific scenario. Empty is any scenario from any pack.
    const choice = mode === 'business' ? $('scenario').value : '';
    const data = await api('/api/start', {
      student: $('student').value || 'demo',
      mode,
      level: $('level').value,
      scenario_id: choice.startsWith('pack:') ? null : (choice || null),
      pack: choice.startsWith('pack:') ? choice.slice(5) : null,
    });
    $('chat-log').innerHTML = '';
    bubble('juno', data.reply);
    speak(data.reply);
    show('chat-screen');
  } catch (e) {
    $('start-error').textContent = e.message;
  } finally {
    $('start-btn').disabled = false;
  }
};

async function sendMessage() {
  const input = $('msg-input');
  const text = input.value.trim();
  if (!text) return;
  bubble('you', text);
  input.value = '';
  $('send-btn').disabled = true;
  $('chat-error').textContent = '';
  try {
    const data = await api('/api/message', { text });
    bubble('juno', data.reply);
    speak(data.reply);
  } catch (e) {
    $('chat-error').textContent = e.message;
  } finally {
    $('send-btn').disabled = false;
    input.focus();
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
  $('end-btn').disabled = true;
  try {
    const data = await api('/api/end', {});
    renderRecap(data.report);
    show('report-screen');
  } catch (e) {
    $('chat-error').textContent = e.message;
  } finally {
    $('end-btn').disabled = false;
  }
};

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
