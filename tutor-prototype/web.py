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
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anthropic
from google.cloud import texttospeech

import tutor  # the exact tested logic: prompts, tiers, report schema

RUNNING_DEPLOYED = bool(os.environ.get("PORT"))
HOST = "0.0.0.0" if RUNNING_DEPLOYED else "127.0.0.1"
PORT = int(os.environ.get("PORT", 8765))
ACCESS_PASSPHRASE = os.environ.get("JUNO_ACCESS_PASSPHRASE", "")
MAX_MESSAGES_PER_CALL = 40
MAX_CALLS_PER_SESSION = 8
MAX_FEEDBACK_LENGTH = 2000
FEEDBACK_PATH = tutor.BASE_DIR / "data" / "feedback.jsonl"

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
  'Microsoft Aria Online (Natural) - English (United States)',
  'Microsoft Jenny Online (Natural) - English (United States)',
];

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
    const match = voices.find((v) => v.name === name);
    if (match) return match;
  }
  const enhanced = voices.find((v) => v.lang && v.lang.startsWith('en') && /enhanced|premium|natural/i.test(v.name));
  if (enhanced) return enhanced;
  const cloud = voices.find((v) => v.lang && v.lang.startsWith('en') && v.localService === false);
  if (cloud) return cloud;
  return voices.find((v) => v.lang && v.lang.startsWith('en')) || null;
}

async function speak(text) {
  if (!$('speak-toggle').checked) return;
  try {
    const res = await fetch('/api/speak', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ text }),
      credentials: 'same-origin',
    });
    if (!res.ok) {
      const err = await res.json();
      console.error('TTS error:', err.error);
      return;
    }
    const audioBlob = await res.blob();
    const audioUrl = URL.createObjectURL(audioBlob);
    const audio = new Audio(audioUrl);
    audio.play();
  } catch (err) {
    console.error('Failed to play audio:', err);
  }
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
    scenarios.business_packs.forEach((p) => {
      p.scenarios.forEach((s) => {
        const opt = document.createElement('option');
        opt.value = s.id;
        opt.textContent = `${s.title} (${s.cefr})`;
        sel.appendChild(opt);
      });
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
    const data = await api('/api/start', {
      student: $('student').value || 'demo',
      mode,
      level: $('level').value,
      scenario_id: mode === 'business' ? ($('scenario').value || null) : null,
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
        sid = cookies["juno_sid"].value if "juno_sid" in cookies else None
        if not sid or sid not in SESSIONS:
            sid = secrets.token_urlsafe(24)
            SESSIONS[sid] = {"authed": not ACCESS_PASSPHRASE, "call_count": 0}
            self._new_sid = sid
        else:
            self._new_sid = None
        self._sid = sid
        return SESSIONS[sid]

    def _cookie_header(self) -> str | None:
        if getattr(self, "_new_sid", None):
            secure = "; Secure" if RUNNING_DEPLOYED else ""
            return f"juno_sid={self._new_sid}; Path=/; HttpOnly; SameSite=Lax{secure}"
        return None

    def _send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        cookie = self._cookie_header()
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        cookie = self._cookie_header()
        if cookie:
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
            elif self.path == "/api/feedback":
                self._require_auth(session)
                self._handle_feedback(session, data)
            elif self.path == "/api/speak":
                self._require_auth(session)
                self._handle_speak(data)
            else:
                self._send_json({"error": "not found"}, 404)
        except _AuthError:
            self._send_json({"error": "Not authenticated."}, 401)
        except Exception as e:  # noqa: BLE001 - surface any failure to the browser
            self._send_json({"error": str(e)}, 500)

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

    def _handle_start(self, session: dict, data: dict) -> None:
        if session.get("call_count", 0) >= MAX_CALLS_PER_SESSION:
            self._send_json({"error": "Call limit reached for this browser session."}, 429)
            return
        session["call_count"] = session.get("call_count", 0) + 1

        student_id = (data.get("student") or "demo").strip().lower().replace(" ", "_") or "demo"
        student = tutor.load_student(student_id)
        mode = data.get("mode") if data.get("mode") in ("business", "structured") else "free"

        level = data.get("level")
        if level not in tutor.LEVEL_RULES:
            level = student.get("cefr_level", "B1").rstrip("+")
        if level not in tutor.LEVEL_RULES:
            level = "B1"

        scenario = tutor.pick_scenario(data.get("scenario_id"), data.get("pack")) if mode == "business" else None

        system = tutor.build_system_prompt(level, mode, scenario, student)
        messages = [{"role": "user", "content": "(the call has just connected — open it)"}]
        response = client.messages.create(
            model=tutor.MODEL, max_tokens=1024, system=system, messages=messages
        )
        reply = next(b.text for b in response.content if b.type == "text")
        messages.append({"role": "assistant", "content": reply})

        session["call"] = {
            "student": student, "mode": mode, "scenario": scenario,
            "system": system, "messages": messages, "message_count": 0,
        }
        self._send_json({"reply": reply, "scenario": scenario["title"] if scenario else None})

    def _handle_message(self, session: dict, data: dict) -> None:
        call = session.get("call")
        if not call:
            self._send_json({"error": "No call in progress. Start a call first."}, 400)
            return
        if call["message_count"] >= MAX_MESSAGES_PER_CALL:
            self._send_json({"error": "Message limit reached for this call. End the call to see your report."}, 429)
            return
        text = (data.get("text") or "").strip()
        if not text:
            self._send_json({"error": "Empty message."}, 400)
            return

        call["message_count"] += 1
        call["messages"].append({"role": "user", "content": text})
        response = client.messages.create(
            model=tutor.MODEL, max_tokens=1024,
            system=call["system"], messages=call["messages"],
        )
        reply = next(b.text for b in response.content if b.type == "text")
        call["messages"].append({"role": "assistant", "content": reply})
        self._send_json({"reply": reply})

    def _handle_end(self, session: dict, data: dict) -> None:
        call = session.get("call")
        if not call:
            self._send_json({"error": "No call in progress."}, 400)
            return
        report = tutor.generate_report(client, call["messages"])
        student = tutor.apply_report_to_student(call["student"], report, call["mode"], call["scenario"])
        tutor.save_student(student)
        session["call"] = None
        self._send_json({"report": report})

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

    def _handle_speak(self, data: dict) -> None:
        text = (data.get("text") or "").strip()
        if not text:
            self._send_json({"error": "Text cannot be empty."}, 400)
            return

        client = texttospeech.TextToSpeechClient()
        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice = texttospeech.VoiceSelectionParams(
            language_code="en-US",
            name="en-US-Neural2-A",
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            pitch=0.0,
            speaking_rate=1.0,
        )

        response = client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config,
        )
        self._send_bytes(response.audio_content, "audio/mpeg")

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
