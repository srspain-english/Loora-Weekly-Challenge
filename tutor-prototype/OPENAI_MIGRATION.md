# OpenAI migration: development branch only

## Scope and provider contract

`ai.Connection` is the sole API boundary. `web.py` and the CLI in `tutor.py`
use it for conversation, help, definitions, normal reports and abandoned-class
reports. Client creation is lazy: imports and offline tests require no key.
The browser's NDJSON protocol and Web Speech fallback are preserved. Deepgram
and a new voice transport are deferred.

Only completed OpenAI responses are accepted. Text delta events feed the existing
sentence splitter. Failure after a partial stream does not retry or commit an
incomplete exchange. The browser preserves its request key/input on an error.
The saved class contains the last *complete* exchange, not any partial reply
that may already have played. Resolving delivered-audio history is future voice work.

Report JSON uses the existing field names, enums and array/object structure,
with strict object validation. Corrections allow 0–8 entries, not a compulsory
minimum of three. Each correction quotation must appear in an actual student
turn, excluding the synthetic opener and report request. No invalid, refused or
incomplete report updates memory. Schema correctness cannot prove educational
quality; that needs a separately approved, capped live evaluation.

The teaching contract test fingerprints the original methodology constants,
lesson-context builders and report-to-memory update logic. Scenarios are unchanged.
The report receives the saved lesson context, including its grammar target and
prior error patterns. No real records were loaded to implement or test this change.

## Persistence and concurrency

Additive tables: `ai_reservations`, `call_locks`, `turn_attempts`. Existing
student IDs, calls, reports and progress fields remain compatible. Legacy
provider-specific prompt blocks are read as plain text without rewriting calls.

Messages are guarded per call. Idempotency keys are scoped to student + call;
the transcript and replay result commit together. An uncertain attempt cannot
be resent automatically using the same key. Reports, cron and recovery share
call/student guards; memory + report commit in one database transaction.

New report-derived memory is stored in `student_memory` on SQLite as well as
Postgres, and takes precedence over legacy JSON. Existing JSON files remain
available as an import/fallback and are never rewritten by report finalisation.
A rollback to code that reads only JSON would therefore see stale local memory;
do not roll back a live SQLite installation by replacing application files alone.
The CLI's explicit save keeps the database and local JSON in sync.

All operations on the shared database connection are serialised. Cross-process
coordination uses database rows, including a locked daily-spend row on Postgres.
An interrupted write transaction is never replayed statement by statement.

## Spending protection and unknown outcomes

Prices are for the pinned model names, Standard tier, requests below the
long-context threshold, and 30-minute cache writes. Prompt size is conservatively
bounded using serialized UTF-8 bytes plus framing allowance. Oversized requests
are rejected rather than truncating teaching history. Output has a finite cap,
including reasoning. Cache read/write categories are not counted twice; longer
cache-write retention is accounted for if reported. Unknown models/prices fail
closed. The budget is an application estimate, not an invoice guarantee.

Before each request, reserve maximum estimated input/output cost atomically.
Completed usage settles once against that request's original UTC day. If usage
is unknown, its reservation remains held. Failure/refusal still costs money when
usage exists. Calls, help, definitions and all report paths share this boundary.
Zero/negative/non-finite budgets block paid work. SDK retries are disabled.

A process killed during work can leave a `call_locks` row or pending reservation.
These do not expire automatically: an abandoned lock must not let a second worker
repeat a possibly running request. An operator must stop/drain workers, verify the
request has ended, inspect the affected request/call IDs and provider accounting,
then clear only the verified stale lock and reconcile only that reservation.
Never clear every lock or pending allowance on startup. No administrative
reconciliation endpoint is exposed to students.

## Testing and release gates

`python tests/run_offline.py` runs the full suite without credentials, production
configuration or outbound connections. It creates temporary fictional data.
Optional local Postgres database: `JUNO_TEST_DATABASE_URL`, database name beginning
`juno_test_`. The test reset refuses non-test URLs. Database names are only a guard;
operators must still create a disposable database.

Regression coverage includes teaching fingerprints, genuine zero/one/two-error
reports, schema/refusal/incomplete handling, actual SDK request/stream parsing
through MockTransport, concurrent messages and reports, foreign-call rejection,
uncertain billing and budget reservations. Existing UI, recovery, progress,
scenario, reminders and privacy tests are retained with OpenAI mocks.

No live OpenAI call, voice-quality assessment, real latency measurement or
provider invoice reconciliation has been performed. No real student migration,
production deployment, load test or end-user browser microphone test is included.

## Rollback

The production branch is untouched. Development commits are reversible; no merge
or deployment is part of this work. Reverting the new connection module alone
while keeping migrated callers is not a valid rollback: revert the whole migration
series together in an isolated development checkout.

Before any future release, back up the database and any legacy JSON memory; test
restoration using fictional fixtures. Retain the previous application image.
Additive database tables do not require destructive rollback migrations. Do not
restore an old database over student progress written since a release.

Future Juno has no Anthropic fallback. If OpenAI is unavailable, preserve the
class and explain the failure. Deployment and live API testing require separate
user approval; an OpenAI server-side key will only be needed at that later stage.

## Verified offline results (2026-11-16)

Python 3.12, OpenAI SDK 3.51.0, psycopg 3.3.9 and jsonschema 4.26.0,
in a fresh virtual environment with no Anthropic package installed:

- SQLite: 270 discovered, 265 passed, 5 Postgres-specific tests skipped.
- Disposable local Postgres 17: 270 discovered, 269 passed, 1 SQLite-only test skipped.
- No failures on either backend. Every distinct test ran on at least one backend.
- Browser script syntax and deterministic speech-queue behavior were checked with Node.
- SDK JSON and streaming parsing were exercised through an in-memory transport.

These results do not authorize deployment or live API use.
