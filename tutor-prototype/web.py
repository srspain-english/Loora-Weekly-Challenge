#!/usr/bin/env python3
"""
Juno — local browser version.

Runs a tiny web server on your own Mac (nothing is uploaded anywhere,
nobody outside this computer can reach it) so a real student can use
Juno through an ordinary webpage instead of Terminal. This reuses the
exact same tested logic as tutor.py — the teaching rules, correction
tiers, and report format are identical, just reached over a browser
tab instead of stdin/stdout.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 web.py

Then open the link it prints (it also opens automatically). Press
Ctrl+C in Terminal when you're done to stop it.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anthropic

import tutor  # the exact tested logic: prompts, tiers, report schema

PORT = 8765

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
  input[type=text], select{
    width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:10px;
    font-family:inherit;font-size:14px;background:var(--bg);color:var(--ink);
  }
  button{
    font-family:'Space Grotesk',sans-serif;font-weight:600;font-size:14px;cursor:pointer;
    border:none;border-radius:999px;padding:11px 20px;background:var(--accent);color:#fff;
  }
  button:disabled{opacity:.5;cursor:default}
  button.ghost{background:transparent;color:var(--ink-soft);border:1px solid var(--line)}
  .row{display:flex;gap:10px;flex-wrap:wrap}
  .error{color:#B0392A;font-size:13px;margin-top:10px}
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
  .recap h3{font-family:'Space Grotesk',sans-serif;font-size:14px;margin:20px 0 8px;color:var(--ink)}
  .recap h3:first-child{margin-top:0}
  .recap table{width:100%;border-collapse:collapse;font-size:13.5px}
  .recap td{padding:6px 4px;border-bottom:1px solid var(--line);vertical-align:top}
  .recap ul{margin:4px 0;padding-left:20px;font-size:13.8px}
  .tag{font-size:10px;text-transform:uppercase;color:var(--muted);font-family:ui-monospace,monospace}
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
  <div class="brand"><span class="brand-mark">Juno</span><span class="brand-sub">Teaching Assistant</span></div>
  <p class="lede">Running locally on this computer — nothing leaves this machine except the messages sent to Claude.</p>

  <div id="setup-screen" class="panel">
    <label for="student">Student name</label>
    <input type="text" id="student" placeholder="e.g. maria">

    <label for="mode">Mode</label>
    <select id="mode">
      <option value="free">Free Conversation</option>
      <option value="business">Business English</option>
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
      <input type="text" id="msg-input" placeholder="Type your reply…">
      <button id="send-btn">Send</button>
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

async function api(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Something went wrong.');
  return data;
}

function show(id) {
  ['setup-screen', 'chat-screen', 'report-screen'].forEach(
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

fetch('/scenarios.json').then((r) => r.json()).then((data) => {
  scenarios = data;
  if (!scenarios.business_packs) {
    throw new Error("scenarios.json is an old version — re-download it.");
  }
  const sel = $('scenario');
  scenarios.business_packs.forEach((p) => {
    p.scenarios.forEach((s) => {
      const opt = document.createElement('option');
      opt.value = s.id;
      opt.textContent = `${s.title} (${s.cefr})`;
      sel.appendChild(opt);
    });
  });
}).catch((e) => {
  $('start-error').textContent = 'Could not load scenarios: ' + e.message;
});

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
</script>
</body>
</html>
"""

client = anthropic.Anthropic()
call_state: dict = {}


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw or b"{}")

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send_bytes(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/scenarios.json":
            self._send_bytes(tutor.SCENARIOS_PATH.read_bytes(), "application/json")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        try:
            data = self._read_json()
            if self.path == "/api/start":
                self._handle_start(data)
            elif self.path == "/api/message":
                self._handle_message(data)
            elif self.path == "/api/end":
                self._handle_end(data)
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001 - surface any failure to the browser
            self._send_json({"error": str(e)}, 500)

    def _handle_start(self, data: dict) -> None:
        student_id = (data.get("student") or "demo").strip().lower().replace(" ", "_") or "demo"
        student = tutor.load_student(student_id)
        mode = "business" if data.get("mode") == "business" else "free"

        level = data.get("level")
        if level not in tutor.LEVEL_RULES:
            level = student.get("cefr_level", "B1").rstrip("+")
        if level not in tutor.LEVEL_RULES:
            level = "B1"

        scenario = (
            tutor.pick_scenario(data.get("scenario_id"), data.get("pack"))
            if mode == "business" else None
        )

        system = tutor.build_system_prompt(level, mode, scenario, student)
        messages = [{"role": "user", "content": "(the call has just connected — open it)"}]
        response = client.messages.create(
            model=tutor.MODEL, max_tokens=1024, system=system, messages=messages
        )
        reply = next(b.text for b in response.content if b.type == "text")
        messages.append({"role": "assistant", "content": reply})

        call_state.clear()
        call_state.update({
            "student": student, "mode": mode, "scenario": scenario,
            "system": system, "messages": messages,
        })
        self._send_json({"reply": reply, "scenario": scenario["title"] if scenario else None})

    def _handle_message(self, data: dict) -> None:
        if "system" not in call_state:
            self._send_json({"error": "No call in progress. Start a call first."}, 400)
            return
        text = (data.get("text") or "").strip()
        if not text:
            self._send_json({"error": "Empty message."}, 400)
            return
        call_state["messages"].append({"role": "user", "content": text})
        response = client.messages.create(
            model=tutor.MODEL, max_tokens=1024,
            system=call_state["system"], messages=call_state["messages"],
        )
        reply = next(b.text for b in response.content if b.type == "text")
        call_state["messages"].append({"role": "assistant", "content": reply})
        self._send_json({"reply": reply})

    def _handle_end(self, data: dict) -> None:
        if "system" not in call_state:
            self._send_json({"error": "No call in progress."}, 400)
            return
        report = tutor.generate_report(client, call_state["messages"])
        student = tutor.apply_report_to_student(
            call_state["student"], report, call_state["mode"], call_state["scenario"]
        )
        tutor.save_student(student)
        call_state.clear()
        self._send_json({"report": report})

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        pass  # keep Terminal quiet; errors still surface in the browser


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY first, same as with tutor.py.")
    url = f"http://127.0.0.1:{PORT}"

    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        sys.exit(
            f"Could not start — port {PORT} is already in use.\n"
            "This almost always means an old copy of this server is still running in "
            "another Terminal window or tab. Find that window and press Ctrl+C there, "
            "or close all Terminal windows and try again. If it's already running fine, "
            f"just open {url} in your browser instead of starting a new one."
        )

    # Only announce success once the server has actually bound to the port.
    print(f"Juno is running. Opening {url} in your browser now.")
    print("Leave this Terminal window open. Press Ctrl+C here to stop it when you're done.")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
