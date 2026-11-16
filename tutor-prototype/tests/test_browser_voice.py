"""Execute existing browser speech fallback logic with a deterministic audio stub."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent.parent))
import web


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class BrowserQueueTest(unittest.TestCase):
    def run_js(self, body):
        queue = web.INDEX_HTML.split("function makeSpeechQueue(onDone) {")[1].split("$('speak-toggle').onchange")[0]
        script = "function makeSpeechQueue(onDone) {" + queue + "\n" + body
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_cancelled_queue_never_plays_late_or_remaining_sentences(self):
        result = self.run_js("""
const played = [];
const $ = () => ({checked:true});
let paused = false;
const makeUtterance = text => ({text});
let current;
const window = {speechSynthesis:{speak(u){current=u;played.push(u.text)},cancel(){if(current)current.onend()}}};
let done = 0;
const queue = makeSpeechQueue(() => done++);
queue.push('First'); queue.push('Second');
queue.cancel(); queue.push('Late'); queue.finish();
console.log(JSON.stringify({played,done}));
""")
        self.assertEqual(result, {"played": ["First"], "done": 0})

    def test_one_shot_and_replayed_replies_still_speak(self):
        send = web.INDEX_HTML.split("let pendingTurn = null;")[1].split("$('send-btn').onclick")[0]
        result = self.run_js("""
const played = [], nodes = {};
const $ = id => nodes[id] || (nodes[id] = {checked:true, hidden:true,value:'Hello',classList:{contains:()=>false}});
let paused=false,sending=false,junoLine='Previous',classLimitMs=0,wrapUpSent=false,currentCallId='fictional';
const makeUtterance = text => ({text});
const window = {speechSynthesis:{speak(u){played.push(u.text)},cancel(){}}};
const crypto = require('crypto').webcrypto;
const bubble=()=>{}, setJunoLine=()=>{}, showSaved=()=>{}, idleOrb=()=>{}, setOrb=()=>{}, setStatus=()=>{}, autoListen=()=>{}, afterSpeaking=()=>{};
const streamMessage=async()=>({reply:'Reply.',saved_at:'fictional',turns_left:20});
let pendingTurn = null;
""" + send + "\nsendMessage().then(()=>console.log(JSON.stringify(played)));\n")
        self.assertEqual(result, ["Reply."])
