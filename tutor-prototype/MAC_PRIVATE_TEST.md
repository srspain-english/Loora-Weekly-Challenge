# Test Juno 3.0 privately on your Mac

This starts the development version on your Mac. It does not publish a website
or change production. OpenAI receives the lesson text when you choose to start
or continue a class; browser speech recognition may also use its browser vendor's
online service. Use fictional conversation details throughout.

## 1. Prepare billing when you are ready for a paid test

At https://platform.openai.com create a separate project called **Juno Private Test**.
Use that project's billing controls, not the settings for the production project.

- If you have no usable API credits, the current minimum prepaid purchase is $5.
  Turn **automatic reload OFF** before purchasing. This credit is not the cost of
  one class. Check the current minimum and any tax on your billing screen.
- Under the test project's **Limits → Spend → Edit spend limit**, set **$1/month**
  and enable **Enforce a hard limit**. Alerts alone do not stop requests.
  Enforcement and prepaid cutoffs can lag; neither is an exact instantaneous cap.
- Create a key for this test project, allowing Responses requests with Luna and
  Terra. Paste it only into the local Terminal prompt when the launcher asks.
  Do not paste it into chat, a web form, source files or GitHub.
- Juno separately reserves spending against a **$0.25 daily allowance** in its
  test database. The allowance persists when you restart the same downloaded copy.

Allow about **$0.02–$0.10 for a six-minute conversation plus its report**. This is
an estimate, not a measured invoice. Conversation uses GPT-6 Luna, the report
GPT-6.1 Terra. Browser speech has no additional Juno speech-provider API fee.
No hosting or Deepgram service is needed. Never increase a limit merely to get
past an unknown error.

Sources checked 2026-11-16:
- https://help.openai.com/en/articles/8264644-setting-up-and-managing-prepaid-api-billing
- https://developers.openai.com/api/docs/guides/spend-limits
- https://openai.com/api/

## 2. Download and launch

1. Install desktop Google Chrome if needed. Install Python 3.12 or newer from
   https://www.python.org/downloads/macos/ if Python is missing or too old.
2. Download the **feature/juno-3-voice-prototype** branch as a ZIP and double-click
   the ZIP to unpack it. Keep this folder for the duration of your test; a new
   download is a separate isolated test environment.
3. Inside that folder, open **tutor-prototype**. Locate **Start Juno Private Test.command**.
4. Open **Terminal** using Spotlight (Command+Space, type Terminal, press Return).
5. Type `bash` followed by a space, drag **Start Juno Private Test.command** from
   Finder into Terminal, then press Return. Dragging inserts the full file path;
   you do not need to type any folders or run `chmod`.
6. The first launch installs software in a private temporary folder. It does not
   call OpenAI. When asked, paste your **test-project API key** into Terminal and
   press Return. The key stays invisible while you paste; this is normal.
7. A browser page opens. If it opens in Safari, copy the displayed local address
   into Chrome. The address starts with `http://127.0.0.1:` and works on this Mac
   only. It is not a shareable or hosted test URL.

The key is never written to disk by the launcher. It lasts only for that running
process. A later restart asks for it again.

## 3. Have the six-minute conversation

- Open the page in Chrome and allow the microphone when prompted.
- The test identity is fixed to **Alex Test**. No real student code is needed.
- Choose **Free Talk**, **6 minutes**, and leave **Juno speaks** enabled.
- Press **Start** (or the start-call button) when you are ready to use the paid API.
- Speak about an imaginary holiday, invented work situation or other fictional
  subject. The existing microphone and browser voice are used.
- The six-minute option requests a goodbye on your next answer after time is up;
  it is not a hard billing cutoff. Press **End call** to finish and read the report.
- After the report, stop the local server with **Control+C** in Terminal. Closing
  only the browser tab does not stop the server.

Opening the launcher or its webpage does not request a model response. Starting
or continuing a conversation, asking for help/definitions, ending a class for a
report, or starting a new class that closes an unfinished one can make paid calls.

## 4. If something goes wrong

- If you only lose the page, reopen the displayed address. The fictional class
  and spending allowance remain in the same test database.
- After a normal stop, run the same launcher again. It checks the database before
  starting and lets the application offer the unfinished fictional class.
- If it says **safety lock**, **uncertain spending**, **recovery**, or **unexpected
  identity**, stop. Copy that message to Codex, without your API key. Do not delete
  the database, clear its locks, redownload to reset the allowance, or repeatedly
  retry a paid request. A request can have been billed even if no reply arrived.
- This data is temporary, not durable student storage. macOS may eventually clean
  temporary files. Review your OpenAI test-project usage as the independent record.
- Keep the test folder until you have reviewed the report. Cleanup should happen
  only after any uncertain spending has been reconciled.

## Safety and verification

The launcher clears inherited production/database/mail/provider settings, refuses
unmarked folders or linked data paths, uses SQLite in a marked private temporary
folder, and never invokes the application's legacy student-import startup path.
The local server binds only to `127.0.0.1`, blocks foreign website origins/hosts,
fixes identity to Alex Test, and disables admin, reminders and cron endpoints.
It checks database integrity, interrupted work and pending/uncertain spending.
It neither clears these automatically nor runs a report at startup.

Automated tests use fictional records, a temporary database and mocked AI calls.
The launcher was exercised on Linux/POSIX; a real Mac, real browser microphone and
live OpenAI responses have not yet been tested. No production deployment or paid
API test was performed. Do not change production configuration for this test.
