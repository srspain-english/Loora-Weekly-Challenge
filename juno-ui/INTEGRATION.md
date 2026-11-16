# Safe integration approach

## What was inspected

The uploaded `fluent-practice-frontend-review.zip` contains a React/Tailwind/wouter interface. Its conversations, progress percentages and pronunciation scores were demonstration content, not a learning engine. Its cream/purple/green palette, typefaces, navigation, cards, responsive layouts and conversation orb are retained. Branding is Juno by S&R Spain. Fonts are bundled locally instead of fetched from Google. Unused Replit tooling was excluded. The export declares MIT in package metadata but contains no standalone licence file; keep the supplied export as design provenance.

Juno's `tutor-prototype/web.py`, `tutor.py`, `store.py`, `ai.py`, `private_mac_test.py`, existing page and automated tests provide the teaching instructions, API handlers, account identity, storage and canonical reports. These files are unchanged by this prototype. The new interface lives entirely in `juno-ui/` so the existing application continues to serve its original page.

## Boundaries

`preview.py` creates a fresh temporary database through the existing private-test factory, fixes identity to fictional Alex, replaces model responses and blocks outbound connections. It rejects PostgreSQL or a database outside that temporary directory. It never runs production startup/import/recovery routines. Its reset endpoint checks loopback Host and same Origin and clears only this temporary database. It is for local review only; never deploy this authentication-bypassing preview server.

The UI uses the existing same-origin `/api/start`, `/api/message`, `/api/help`, `/api/define`, `/api/end`, `/api/resume`, `/api/progress`, `/api/notes` and scenario catalogue contracts. Stream deltas are displayed as they arrive. Message IDs survive retries. Reports and memory updates go through existing idempotency and budget safeguards. The server owns student identity; no account identity or progress is trusted from browser storage. Only the selected class-mode preference is stored locally.

The current teaching engine, prompts, corrections, three class modes and complete report schema are unchanged. Notes display the canonical report, including zero corrections. Progress uses actual temporary Juno records. Unsupported speaking percentages, pronunciation grades and progress-to-B2 percentages were removed. The former Coach card maps to Juno's structured class; vocabulary practice uses report vocabulary. The avatar area is a labelled placeholder with no service behind it.

## Before any live integration

Keep this mock preview as an independent review harness. Build a separate authenticated route using Juno's existing signed sessions and authorization; do not ship preview.py or its reset route. Verify session expiry, account isolation, CSRF, stream disconnect/retry behaviour, abandoned-class recovery, deduplication and report completeness using fictional accounts. Preserve the current interface as a switchable fallback. Validate browser microphone and speech on Mac Safari/Chrome and real phones with consent; automated tests cover typed interaction and local traffic, not speech-recognition quality.

Review database backups, restore into a separate test database, validate migrations and recovery paths, and ensure no preview startup can point to production. No schema migration is required for this UI. Live model quality, browser speech behaviour, accessibility with assistive technology and production authentication remain separate approval-gated testing work. No API keys or new voice/avatar providers are needed for this phase.

## Rollback

The production route and backend files are untouched. Remove or revert the commits adding `juno-ui/` to discard this prototype. Stop the local server with Control-C to delete its temporary records. No production data recovery is necessary. Any future rollout should retain the old interface behind a server-side feature flag and practise restoring a separate database before release.
