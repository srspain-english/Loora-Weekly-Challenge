# Juno by S&R Spain — visual prototype

This private preview uses the supplied Fluent Practice design with Juno's existing class and report handlers. Alex Test is fictional. All AI answers are scripted. No API key, paid service or production database is used. Data disappears when you stop the preview.

## Try it on a Mac

Check out `feature/juno-3-voice-prototype`, then open Terminal in the repository folder:

```sh
python3 -m venv .preview-venv
.preview-venv/bin/pip install -r tutor-prototype/requirements.txt
cd juno-ui
npm ci
npm run build
../.preview-venv/bin/python preview.py
```

Install Node.js 22 and Python 3.11 or newer first if needed. Open http://127.0.0.1:5400 in your browser. Stop with Control-C. The server listens only on your own computer. Do not expose it to the internet: its fictional account deliberately bypasses normal login.

You can choose free conversation, business scenarios or a structured class; type a reply, request help, select words for definitions, pause, resume and finish. Notes and progress are calculated by Juno's existing code from those fictional classes. They are not real assessments. Existing browser speech is optional; microphone availability depends on your browser. No Deepgram or paid avatar is connected.

## Verification

```sh
npm run build
npx playwright install chromium
JUNO_TEST_PYTHON=../.preview-venv/bin/python npm test
```

The tests start their own temporary preview database and mock every AI response. For backend regression tests, run ` .preview-venv/bin/python tutor-prototype/tests/run_offline.py` from the repository root. This blocks external network access.

See [INTEGRATION.md](INTEGRATION.md) for the integration boundaries and rollback plan. Nothing here deploys or replaces the current Juno interface.
