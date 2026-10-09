# Juno: isolated mocked HTTPS staging

This is a preparation runbook, **not authorization to create or deploy a service**.
No hosting resources were created by this change. Production uses
`claude/mila-platform-feasibility-ogq7xo`; its service, disk, environment, and
On Commit deployment settings must remain untouched.

## What the new launcher does

Run `python3 -B staging_server.py` only in the separate staging service described
below. It imports the existing application with a deterministic, zero-token mock
Anthropic client. It does not call the production launcher, change teaching
prompts, or change microphone behavior. `web.py`, `tutor.py`, `local_mock.py`, and
`dev_mock.py` do not import staging code; all local launcher safeguards remain.

Startup verifies Render's runtime service ID, development branch, HTTPS URL,
service name, Git metadata, actual checkout commit, explicit configuration, and
an actual `/var/data` mount. Unknown `JUNO_*` settings, Anthropic configuration,
external database overrides, common credential variables, and proxy overrides
are rejected. No fallback to a real Anthropic transport exists. Outbound TCP/UDP
socket connections are blocked within the staging application process. A
browser content security policy restricts connections and assets to staging;
the external Google font uses the browser's fallback font instead.

Only the configured HTTPS Host and Origin are accepted; forwarded protocol must
be `https`. `/healthz` is a minimal nonstudent health check. Application requests,
health checks, and mock calls fail closed on invalid configuration or storage.
Render terminates HTTPS; the Python backend listens on its platform `PORT`.
Secure cookies are enabled by the existing application. There is no TLS bypass.

The storage marker binds the directory to the staging service, hostname, stable
instance UUID, and development branch. It cannot be adopted by another service.
Unmarked nonempty directories and symlinks are refused. One running staging
process is allowed per mounted disk. Accounts created through the staging API
must have names beginning with `Synthetic `, and the page displays a staging
warning. This cannot determine whether arbitrary typed conversation text is
real: operators and testers must use synthetic information exclusively.

## Manual creation checklist — after explicit approval only

1. Review and approve the Phase 3B files, then separately authorize a commit and
   push to `juno-v2-development`. Record the resulting **full 40-character SHA**
   as `APPROVED_PHASE_3B_SHA`. The current pre-Phase-3B commit
   `97225186839103957f18bd536a1a0946976965d8` does **not** contain these changes and
   must not be used to deploy this launcher. No deployable Phase 3B SHA exists
   until these uncommitted changes are saved and reviewed.
2. With separate authorization for paid hosting, manually create a **new** paid
   Render Python web service for `srspain-english/Loora-Weekly-Challenge`, named
   `juno-staging-<unique-suffix>`, branch `juno-v2-development`. Use root directory
   `tutor-prototype`, a single instance, no autoscaling, Auto-Deploy **Off**, and
   Pull Request Previews **Off**. Do not duplicate the production service or
   attach production environment groups, custom domains, credentials, or disks.
3. Select the previously reviewed paid plan and a separate persistent disk
   (initially 1 GB is enough for synthetic validation), mounted at `/var/data`.
   Confirm the disk is attached only to this staging service. A disk excludes
   horizontal scaling and may introduce deployment downtime; that is acceptable
   for this isolated test service. Recheck pricing in the creation screen before
   incurring charges; this preparation incurs no hosting or API charges.
4. Build command: `python3 -m pip install anthropic==1.12.1`. Start command:
   `python3 -B staging_server.py`. Health check path: `/healthz`. Explicitly select
   Python `3.12.14` with Render's `PYTHON_VERSION` setting if supported. These
   are the interpreter and SDK versions tested locally. If the runtime or SDK
   cannot be reproduced, validate the replacement separately before deployment.
5. Generate an independent passphrase and instance UUID in a private local
   terminal. Do not paste the passphrase into Git, logs, chat, or this document:

   ```sh
   python3 -c 'import secrets; print("staging_" + secrets.token_urlsafe(32))'
   python3 -c 'import uuid; print(uuid.uuid4())'
   ```

   Store the passphrase securely and set the following **only in the new staging
   service's environment**. The UUID remains fixed across restarts and deploys.

   | Variable | Required value |
   | --- | --- |
   | `JUNO_ENVIRONMENT` | `staging` |
   | `JUNO_MODEL_MODE` | `mock` |
   | `JUNO_STAGING_SYNTHETIC_ONLY` | `true` |
   | `JUNO_STAGING_SERVICE_ID` | Actual new staging `srv-...` ID |
   | `JUNO_STAGING_HOSTNAME` | Actual `juno-staging-<suffix>.onrender.com` hostname, no scheme |
   | `JUNO_STAGING_INSTANCE_ID` | Generated canonical UUID |
   | `JUNO_STAGING_COMMIT` | `APPROVED_PHASE_3B_SHA`, full lowercase SHA |
   | `JUNO_STAGING_INITIALIZE` | `true` for the first empty disk only |
   | `JUNO_ACCESS_PASSPHRASE` | Independently generated `staging_` plus 43 random characters |
   | `JUNO_DATA_DIR` | `/var/data/juno-staging` |

6. Let Render supply `RENDER`, `RENDER_SERVICE_ID`, `RENDER_SERVICE_NAME`,
   `RENDER_EXTERNAL_URL`, `RENDER_GIT_BRANCH`, `RENDER_GIT_COMMIT`, and `PORT`.
   **Never override these platform identity values to satisfy a guard.** Do not
   add an Anthropic key, `ANTHROPIC_BASE_URL`, `DATABASE_URL`, `JUNO_DB_PATH`,
   production passphrase, tokens, or other `JUNO_*` settings. Render's source
   checkout authentication is outside the running application; no repository
   token is required in its runtime environment.
7. Review service, branch, commit, mount, environment, commands, and deployment
   settings together before the separately approved manual deploy. Creation may
   trigger an initial build before all identity fields can be entered; the
   launcher deliberately refuses incomplete configuration. Do not weaken it to
   make that initial attempt succeed. Manually deploy the approved commit only.
8. After the first healthy startup, set `JUNO_STAGING_INITIALIZE=false` and
   manually redeploy the same approved staging commit. This makes missing data
   fail rather than silently initialize a replacement directory. Keep it false
   for every subsequent restart or deployment. Future approved staging changes
   require updating `JUNO_STAGING_COMMIT` to their full SHA and a manual deploy.

The name and environment checks prevent this launcher from running under the
known production configuration. They cannot defend against an administrator
deliberately relabeling production as staging or copying production files or
credentials into this new service. Separate resources and careful operator
review remain mandatory. Never create a staging service from a production disk
snapshot or an export of student records.

## Persistent layout

All student and security state is under `JUNO_DATA_DIR`:

- `juno.db`: students, hashed personal codes, lessons/messages/reports, usage,
  and persistent login-attempt records (`login_failures`). SQLite WAL and shared
  memory side files are managed alongside it by the application.
- `students/*.json`: learning memory keyed by the existing student ID.
- `feedback.jsonl`: synthetic feedback.
- `.juno-staging.json`: service and instance provenance.
- `.staging-data.lock`: coordinating application writes and consistent backups.

The service-wide process lock is `/var/data/.staging-service.lock`; snapshots
are separate under `/var/data/juno-staging-backups`. No seed data or migration
runs at startup. Use the normal New student flow with names such as `Synthetic
Student A` and `Synthetic Student B`, retain their synthetic codes securely for
the test, and never provision real codes. Existing-student provisioning is
already covered by synthetic regression tests, not by running a migration on
production.

## Consistent backups

After deployment is separately approved, use the **staging service's** shell,
with its verified environment and working directory `tutor-prototype`. Do not
run generic code-provisioning tools or other direct file/SQL writers during a
backup; those tools do not participate in the staging lock. Every application
POST holds a shared lock; the backup holds an exclusive lock and waits for
in-flight writes, briefly pausing new POSTs. Long lessons are not interrupted,
but an in-flight response may delay the snapshot.

```sh
snapshot="/var/data/juno-staging-backups/backup-$(python3 -c 'import uuid; print(uuid.uuid4())')"
python3 -B staging_data.py backup --output "$snapshot"
python3 -B staging_data.py verify --snapshot "$snapshot"
```

Both commands must exit zero. Backup uses SQLite's online backup API and closes
the copy as a standalone database with no WAL dependence, then copies memory,
feedback, and provenance while application writers remain blocked. It includes
login-failure records and personal-code hashes, but not the passphrase, provider
credentials, session cookies, TLS keys, or environment files. Verification
checks the exact allowed file list, SHA-256 hashes, service identity, and SQLite
integrity. A checksum is corruption detection, not protection against an
attacker able to rewrite both the data and manifest.

Schedule **no automated resource now**. Once approved, take backups daily and
before each staging deploy or restore. Keep at least seven daily snapshots and
one snapshot for each pending rollback, with explicit disk-capacity monitoring.
A snapshot on the same disk is not a disaster backup. Copy a verified snapshot
off the service through its authenticated Render SSH access and encrypt it with
a separately held backup key. For example, after obtaining the actual staging
SSH destination from its dashboard, run on a trusted operator machine:

```sh
# Replace these placeholders with the actual staging destination, snapshot,
# and a separately generated age public recipient. Never use production SSH.
ssh '<STAGING_SSH_DESTINATION>' 'tar -C /var/data/juno-staging-backups -czf - backup-<UUID>' | age -r '<BACKUP_PUBLIC_RECIPIENT>' -o juno-staging-backup.tar.gz.age
```

Use a shell with pipeline failure propagation (`set -o pipefail` in Bash),
check successful exit, and prove decryption and verification on a restore
rehearsal before relying on the archive. SSH access and an operator-side `age`
installation must be verified when hosting is authorized; neither was created
or exercised here. Protect SSH and decryption keys separately. Render disk
snapshots can be an additional safety net, but an uncoordinated disk snapshot
alone does not prove consistency between SQLite and JSON memory.

## Exact test restore, without overwriting active data

1. Choose a verified snapshot on the same staging service. For an off-host
   archive, decrypt it locally, inspect its member list for absolute/traversal
   paths, and upload/extract only that known verified snapshot into a new
   `backup-<UUID>` directory under the staging backup area. Never extract an
   untrusted archive or overwrite an existing snapshot. Run verification again.
2. In the staging shell:

   ```sh
   snapshot=/var/data/juno-staging-backups/backup-<UUID>
   restored="/var/data/juno-staging-restore-$(python3 -c 'import uuid; print(uuid.uuid4())')"
   python3 -B staging_data.py verify --snapshot "$snapshot"
   python3 -B staging_data.py restore --snapshot "$snapshot" --target "$restored"
   ```

   The target must be new. The helper refuses the active directory, existing
   targets, foreign service/instance backups, symlinks, extra files, and failed
   integrity checks. It writes the provenance marker last. Keep the original
   `JUNO_DATA_DIR` unchanged until the restore helper exits zero.
3. With explicit staging deployment approval, record the original directory,
   set staging `JUNO_DATA_DIR` to the exact new restore path, keep initialize
   `false`, and manually redeploy the same approved commit. The old runtime must
   stop; the service-wide lock refuses a simultaneous second staging writer.
4. Using only synthetic codes retained before backup, sign in again. Confirm
   previous lesson IDs/transcripts, exact recaps, memory, feedback, and unfinished
   lesson recovery; compare against the pre-backup fixtures. Restart once more
   and repeat. Browser sessions intentionally expire across process restarts;
   students reauthenticate without losing their persistent identity/history.
5. After review, either retain the restored directory or manually redeploy with
   the recorded original directory, initialize still false. Preserve both
   directories until the rehearsal is accepted; do not delete active storage.

This restore mechanism is deliberately bound to the **same staging service and
instance**. Full recovery into a new service ID after service deletion requires
a separately reviewed identity-rebinding procedure; it is not implemented and
must not be bypassed by editing markers or spoofing Render metadata.

## Validation and remaining hosting checks

Run `python3 tests/run_tests.py` with Anthropic, Playwright, Chromium, and OpenSSL
installed. The staging tests use temporary synthetic disks and Render metadata,
actual child server processes, and a local TLS proxy whose generated certificate
is explicitly trusted. They cover strict configuration, missing mount, wrong
service/branch/commit, provider credential rejection, provenance/symlinks,
outbound networking, runtime configuration failure, HTTPS cookies, ownership,
lessons, recaps, memory, restarts, writer-coordinated backup, checksum rejection,
and restarted restore recovery. Existing Chromium journeys also execute.

These tests do **not** establish that Render's actual checkout retains Git
metadata, forwards exactly the expected Host/protocol, or exposes the required
built-in variables. First authorized staging deployment must verify those
contracts, HTTPS redirects, certificate trust, `/healthz`, Secure cookies, disk
retention across manual redeployment, and authenticated off-host backup export.
If a contract differs, fail closed and review a fix rather than override guards.

Login rate limits continue to use the server-observed socket source, not
untrusted forwarded IP headers. Render proxy traffic may share that source;
validate concurrent synthetic classroom login and lockout recovery before any
student release. Browser sessions are process-local and single-instance use is
required. Memory JSON remains the existing architecture; backups coordinate
its writers but do not redesign concurrent lessons for one student.

After hosting approval, test the same two synthetic students in current real
Chrome, Edge, Safari, Firefox, Android Chrome, and iOS Safari. Test gate/code
errors, return after browser close, logout and another student on the same
browser, stale tabs, recaps, retained memory, forced process restart and resumed
lessons, HTTPS cookies, and cross-student access denials. Record OS/browser
versions and results. Safari requires real Apple devices; Chromium results do
not certify Safari, Edge, Firefox, mobile devices, or microphone hardware.
Mocked responses exercise classroom plumbing rather than real AI quality.
