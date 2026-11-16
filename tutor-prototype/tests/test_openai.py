"""OpenAI protocol, report, budget and concurrency regressions. Fictional data only."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parent.parent)]
import ai
import store
import tutor
import web
from helpers import ServerTestCase, fake_reply, fake_report_response


class OpenAITest(ServerTestCase, unittest.TestCase):
    def setUp(self):
        self.opener = self.new_browser()
        web.ACCESS_PASSPHRASE = ""
        web.SESSIONS.clear()
        self.auth()

    def start(self, **data):
        with mock.patch.object(web.client.responses, "create", return_value=fake_reply("How are you?")):
            status, result = self.post("/api/start", {"mode": "free", **data})
        self.assertEqual(status, 200)
        return result

    def test_luna_streams_actual_deltas_and_keeps_browser_protocol(self):
        started = self.start()
        reply = fake_reply("Hello there. What did you do today?")
        reply.chunks = ["Hel", "lo there.", " What did", " you do today?"]
        with mock.patch.object(web.client.responses, "create", return_value=reply) as create:
            _, result = self.post("/api/message", {"text": "I walked home.", "idempotency_key": "one"})
        sent = create.call_args.kwargs
        self.assertTrue(sent["stream"])
        self.assertFalse(sent["store"])
        self.assertEqual(sent["model"], "gpt-6-luna")
        self.assertEqual(sent["reasoning"], {"effort": "none"})
        self.assertEqual(result["reply"], reply.output_text)
        self.assertEqual(store.get_call(started["call_id"])["turns"], 1)

    def test_report_allows_zero_one_or_two_real_errors_and_keeps_format(self):
        for mistakes in range(3):
            started = self.start()
            with mock.patch.object(web.client.responses, "create", return_value=fake_reply("Tell me more.")):
                self.post("/api/message", {"text": "I is happy. She go home."})
            response = fake_report_response()
            report = json.loads(response.output_text)
            report["corrections"] = [
                {"tier": "gentle_recast", "said": "I is happy.", "better": "I am happy.", "note": "Use am with I."},
                {"tier": "silent_log", "said": "She go home.", "better": "She goes home.", "note": "Use goes with she."}
            ][:mistakes]
            with mock.patch.object(web.client.responses, "create", return_value=fake_report_response(report)) as create:
                status, data = self.post("/api/end", {"call_id": started["call_id"]})
            self.assertEqual(status, 200)
            self.assertEqual(data["report"], report)
            sent = create.call_args.kwargs
            self.assertEqual(sent["model"], "gpt-6.1-terra")
            self.assertTrue(sent["text"]["format"]["strict"])
            self.assertEqual(sent["text"]["format"]["schema"]["properties"]["corrections"]["minItems"], 0)
            self.assertIn("correction", sent["instructions"].lower())

    def test_invalid_or_invented_report_never_changes_memory(self):
        started = self.start()
        sid = store.get_call(started["call_id"])["student_id"]
        before = tutor.load_student(sid)
        report = json.loads(fake_report_response().output_text)
        report["corrections"] = [{"tier": "immediate", "said": "An invented student quotation",
                                  "better": "Other words", "note": "Not evidence"}]
        for response in (fake_reply('{"what_we_did":"missing fields"}'), fake_report_response(report)):
            with mock.patch.object(web.client.responses, "create", return_value=response):
                status, _ = self.post("/api/end", {"call_id": started["call_id"]})
            self.assertEqual(status, 502)
            self.assertEqual(tutor.load_student(sid), before)
            self.assertEqual(store.get_call(started["call_id"])["status"], "open")

    def test_refusal_and_incomplete_reports_never_finish_class(self):
        started = self.start()
        refused = fake_report_response()
        refused.stop_reason = "refusal"
        incomplete = fake_report_response()
        incomplete.status = "incomplete"
        for response in (refused, incomplete):
            with mock.patch.object(web.client.responses, "create", return_value=response) as create:
                status, _ = self.post("/api/end", {"call_id": started["call_id"]})
            self.assertEqual(status, 502)
            self.assertEqual(create.call_count, 1)
            self.assertIsNone(store.get_call(started["call_id"])["report"])

    def test_concurrent_same_message_runs_one_model_and_one_exchange(self):
        started = self.start()
        entered, release = threading.Event(), threading.Event()
        result = []
        def respond(**kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return fake_reply("And then?")
        body = {"text": "I went home.", "idempotency_key": "duplicate"}
        with mock.patch.object(web.client.responses, "create", side_effect=respond) as create:
            thread = threading.Thread(target=lambda: result.append(self.post("/api/message", body)))
            thread.start()
            self.assertTrue(entered.wait(5))
            try:
                status, _ = self.post("/api/message", body)
                self.assertEqual(status, 409)
            finally:
                release.set()
                thread.join(5)
            _, replay = self.post("/api/message", body)
            self.assertEqual(replay["reply"], "And then?")
            self.assertEqual(create.call_count, 1)
        self.assertEqual(store.get_call(started["call_id"])["turns"], 1)

    def test_other_student_cannot_replay_or_end_a_class(self):
        first = self.start()
        with mock.patch.object(web.client.responses, "create", return_value=fake_reply("Private fictional reply")):
            self.post("/api/message", {"text": "Hello", "idempotency_key": "shared"})
        other = self.new_browser()
        self.auth(other)
        with mock.patch.object(web.client.responses, "create") as create:
            status, result = self.post("/api/end", {"call_id": first["call_id"]}, other)
            self.assertEqual(status, 404)
            status, result = self.post("/api/message", {"text": "Hello", "idempotency_key": "shared"}, other)
            self.assertEqual(status, 400)
            self.assertNotIn("Private", json.dumps(result))
            create.assert_not_called()

    def test_concurrent_end_and_cron_only_apply_one_report(self):
        started = self.start()
        call_id = started["call_id"]
        sid = store.get_call(call_id)["student_id"]
        with mock.patch.object(web.client.responses, "create", return_value=fake_reply("More?")):
            self.post("/api/message", {"text": "One fictional answer"})
            self.post("/api/message", {"text": "Two fictional answers"})
        entered, release = threading.Event(), threading.Event()
        result = []
        def respond(**kw):
            entered.set()
            self.assertTrue(release.wait(5))
            return fake_report_response()
        with mock.patch.object(web.client.responses, "create", side_effect=respond) as create:
            thread = threading.Thread(target=lambda: result.append(self.post("/api/end", {"call_id": call_id})))
            thread.start()
            self.assertTrue(entered.wait(5))
            try:
                with self.assertRaises(store.CallBusy):
                    web.fold_unfinished_class(store.get_call(call_id))
            finally:
                release.set()
                thread.join(5)
            self.assertEqual(result[0][0], 200)
            self.post("/api/end", {"call_id": call_id})
            self.assertEqual(create.call_count, 1)
        self.assertEqual(len(tutor.load_student(sid)["session_history"]), 1)

    def test_midstream_failure_preserves_history_and_cannot_rebill_same_key(self):
        started = self.start()
        def broken():
            yield SimpleNamespace(type="response.output_text.delta", delta="A sentence. Next ")
            raise RuntimeError("secret provider detail")
        with mock.patch.object(web.client.responses, "create", return_value=broken()) as create:
            _, data = self.post("/api/message", {"text": "Hello", "idempotency_key": "uncertain"})
            self.assertEqual(data["type"], "error")
            status, _ = self.post("/api/message", {"text": "Hello", "idempotency_key": "uncertain"})
            self.assertEqual(status, 409)
            self.assertEqual(create.call_count, 1)
        self.assertEqual(store.get_call(started["call_id"])["turns"], 0)
        row = store.connect().execute("SELECT status FROM ai_reservations WHERE kind = 'turn' AND call_id = ?", (started["call_id"],)).fetchone()
        self.assertEqual(row["status"], "uncertain")

    def test_budget_blocks_every_paid_path_before_request(self):
        started = self.start()
        with mock.patch.object(store, "DAILY_COST_CEILING_USD", 0.00000001), mock.patch.object(web.client.responses, "create") as create:
            self.assertEqual(self.post("/api/start", {"mode": "free"})[0], 429)
            # Stream already has headers, so a turn reports an error event.
            self.assertEqual(self.post("/api/message", {"text": "Hello"})[1]["type"], "error")
            self.assertEqual(self.post("/api/help", {})[0], 429)
            self.assertEqual(self.post("/api/define", {"word": "budget", "sentence": "a fictional budget"})[0], 429)
            self.assertEqual(self.post("/api/end", {"call_id": started["call_id"]})[0], 429)
            create.assert_not_called()

    def test_budget_reservations_are_atomic_and_settle_once(self):
        with mock.patch.object(store, "DAILY_COST_CEILING_USD", store.daily_spend() + 0.001):
            request = store.reserve_ai("gpt-6-luna", 4000, 1000)
            with self.assertRaises(store.LimitReached):
                store.reserve_ai("gpt-6-luna", 4000, 1000)
            before = store.daily_spend()
            store.settle_ai(request, fake_reply().usage)
            after = store.daily_spend()
            store.settle_ai(request, fake_reply().usage)
            self.assertGreater(after, before)
            self.assertEqual(store.daily_spend(), after)

    def test_custom_model_requires_explicit_rates(self):
        with self.assertRaises(store.LimitReached):
            store.reserve_ai("fictional-unknown-model", 100, 10)
        with mock.patch.dict(os.environ, {"JUNO_OPENAI_PRICES_JSON": '{"fictional-unknown-model":[1,0.1,1.25,5]}'}):
            request = store.reserve_ai("fictional-unknown-model", 100, 10)
            self.assertTrue(request.startswith("req_"))

    def test_help_definitions_and_reports_are_in_spend_ledger(self):
        started = self.start()
        with mock.patch.object(web.client.responses, "create", return_value=fake_reply("Una explicación.\n>> Yes || Sí")):
            self.post("/api/help", {})
        with mock.patch.object(web.client.responses, "create", return_value=fake_reply("presupuesto\nMy fictional budget.")):
            self.post("/api/define", {"word": "fictionalbudget"})
        with mock.patch.object(web.client.responses, "create", return_value=fake_report_response()):
            self.post("/api/end", {"call_id": started["call_id"]})
        kinds = {row["kind"] for row in store.connect().execute("SELECT kind FROM ai_reservations WHERE call_id = ?", (started["call_id"],)).fetchall()}
        self.assertEqual(kinds, {"open", "help", "define", "report"})


class PricesTest(unittest.TestCase):
    def test_cached_writes_reads_and_reasoning_not_double_counted(self):
        usage = {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 300},
                 "output_tokens": 100, "output_tokens_details": {"reasoning_tokens": 70}}
        self.assertAlmostEqual(store.estimate_cost(usage, "gpt-6.1-terra"), (500 + 10 + 375 + 500) / 1e6)
        usage["input_tokens_details"]["cache_write_tokens_details"] = {"ttl_12h_tokens": 300}
        self.assertAlmostEqual(store.estimate_cost(usage, "gpt-6.1-terra"), (500 + 10 + 750 + 500) / 1e6)

    def test_unknown_or_invalid_usage_is_not_zero_cost(self):
        for usage in (None, {"input_tokens": -1}, {"input_tokens": float("nan")},
                      {"input_tokens": 1, "output_tokens": 1, "input_tokens_details": {"cached_tokens": 2}}):
            with self.assertRaises(ValueError):
                store.estimate_cost(usage)

    def test_sdk_is_lazy_and_disables_hidden_retries(self):
        import openai
        connection = ai.Responses()
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "fictional-not-a-real-key"}), mock.patch.object(openai, "OpenAI") as constructor:
            constructor.assert_not_called()
            connection.create(model="gpt-6-luna", input="fictional")
            self.assertEqual(constructor.call_args.kwargs, {"max_retries": 0, "timeout": 45.0})

    def test_no_key_or_anthropic_is_needed_to_import_application(self):
        code = "import sys; sys.modules['anthropic']=None; import web,tutor,ai; print(tutor.TURN_MODEL,tutor.MODEL)"
        environment = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")}
        result = subprocess.run([sys.executable, "-c", code], cwd=Path(tutor.__file__).parent,
                                env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "gpt-6-luna gpt-6.1-terra")


class TeachingPreservationTest(unittest.TestCase):
    def test_original_teaching_rules_and_memory_updates_are_unchanged(self):
        import ast
        expected = json.loads((Path(__file__).parent / "teaching_contract.json").read_text())
        found = {}
        for node in ast.parse(Path(tutor.__file__).read_text()).body:
            name = node.name if isinstance(node, ast.FunctionDef) else node.targets[0].id if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) else None
            if name in expected:
                if isinstance(node, ast.FunctionDef) and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                    node.body.pop(0)
                found[name] = hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
        self.assertEqual(found, expected)


class SDKProtocolTest(ServerTestCase, unittest.TestCase):
    """Exercise the installed OpenAI SDK against an in-memory HTTP transport."""
    def exercise(self, streaming):
        import httpx2
        from openai import OpenAI
        seen = []
        payload = {
            "id": "resp_fictional", "object": "response", "created_at": 0,
            "model": "gpt-6-luna", "status": "completed", "error": None,
            "output": [{"id": "msg_fictional", "type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": "Hello. How are you?", "annotations": []}]}],
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
                      "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                      "output_tokens_details": {"reasoning_tokens": 0}},
        }
        def transport(request):
            seen.append(json.loads(request.content))
            if streaming:
                events = [{"type": "response.output_text.delta", "delta": "Hello. ", "sequence_number": 0,
                           "item_id": "msg_fictional", "output_index": 0, "content_index": 0},
                          {"type": "response.output_text.delta", "delta": "How are you?", "sequence_number": 1,
                           "item_id": "msg_fictional", "output_index": 0, "content_index": 0},
                          {"type": "response.completed", "response": payload, "sequence_number": 2}]
                return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                                      content="".join("data: " + json.dumps(e) + "\n\n" for e in events).encode())
            return httpx2.Response(200, json=payload)
        connection = ai.Connection()
        http_client = httpx2.Client(transport=httpx2.MockTransport(transport))
        sdk = OpenAI(api_key="fictional-not-a-real-key", http_client=http_client, max_retries=0)
        connection.responses._client = sdk
        pieces = []
        # Override the base test's deny-by-default API stub only for this real
        # SDK path; every HTTP request still terminates in MockTransport.
        with mock.patch.object(connection.responses, "create", side_effect=sdk.responses.create):
            result = connection.complete(model="gpt-6-luna", system="S&R test instructions", messages=[{"role": "user", "content": "Fictional hello"}],
                                         on_delta=pieces.append if streaming else None)
        sdk.close()
        self.assertEqual(result.text, "Hello. How are you?")
        self.assertEqual(seen[0]["instructions"], "S&R test instructions")
        self.assertFalse(seen[0]["store"])
        if streaming:
            self.assertEqual(pieces, ["Hello. ", "How are you?"])

    def test_actual_sdk_nonstreaming_contract(self):
        self.exercise(False)

    def test_actual_sdk_streaming_contract(self):
        self.exercise(True)


class BudgetProcessTest(ServerTestCase, unittest.TestCase):
    def test_separate_processes_cannot_reserve_the_same_remaining_budget(self):
        environment = dict(os.environ, JUNO_DB_PATH=str(store.DB_PATH),
                           JUNO_DAILY_COST_CEILING_USD=str(store.daily_spend() + 0.001))
        code = """import store
try:
    store.reserve_ai('gpt-6-luna', 4000, 1000)
    print('reserved')
except store.LimitReached:
    print('blocked')
"""
        workers = [subprocess.Popen([sys.executable, "-c", code], cwd=Path(store.__file__).parent,
                                    env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                   for _ in range(2)]
        outcomes = []
        for worker in workers:
            out, err = worker.communicate(timeout=15)
            self.assertEqual(worker.returncode, 0, err)
            outcomes.append(out.strip())
        self.assertCountEqual(outcomes, ["reserved", "blocked"])

    def test_unknown_cost_reservation_is_not_released(self):
        with mock.patch.object(store, "DAILY_COST_CEILING_USD", store.daily_spend() + 0.001):
            # Use a fresh day to isolate the reservation left by the process test.
            with mock.patch.object(store, "_today", return_value="2099-01-01"):
                request = store.reserve_ai("gpt-6-luna", 4000, 1000)
                store.settle_ai(request, None)
                with self.assertRaises(store.LimitReached):
                    store.reserve_ai("gpt-6-luna", 4000, 1000)


class FinalizationTransactionTest(ServerTestCase, unittest.TestCase):
    def test_database_failure_rolls_back_memory_and_report_together(self):
        sid = store.create_student("Fictional atomic student")
        call_id = store.create_call(sid, "free", "B1", None, "[]", [])
        report = json.loads(fake_report_response().output_text)
        before = tutor.load_student(sid)
        changed = tutor.apply_report_to_student(copy.deepcopy(before), report, "free", None)
        connection = store.connect()
        class FailBeforeCallUpdate:
            def execute(self, sql, params=()):
                if sql.startswith("UPDATE calls SET status = 'ended'"):
                    raise RuntimeError("fictional interrupted transaction")
                return connection.execute(sql, params)
            def commit(self):
                return connection.commit()
        with mock.patch.object(store, "connect", return_value=FailBeforeCallUpdate()):
            with self.assertRaises(RuntimeError):
                store.finalize_report(call_id, report, changed)
        self.assertEqual(tutor.load_student(sid), before)
        self.assertEqual(store.get_call(call_id)["status"], "open")
        self.assertIsNone(store.get_call(call_id)["report"])
