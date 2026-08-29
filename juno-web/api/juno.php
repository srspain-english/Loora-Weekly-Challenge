<?php
/**
 * Juno — S&R Tutor backend.
 *
 * Same rules as the CLI prototype (tutor-prototype/tutor.py) and the
 * published Tutor Playbook: correction tiers, A2-C1 level adaptation,
 * Spanish policy, session arc, end-of-call report. Ported to PHP + raw
 * cURL against the Messages API so this runs on plain shared hosting with
 * no Composer / SSH step — upload and go.
 *
 * State (auth, transcript, counters) lives in the PHP session, not on the
 * client, so a visitor can't forge history or bypass the passphrase by
 * editing browser JS. No student-memory persistence in this version —
 * every call is this student's "first session." That's a known, deliberate
 * scope cut for a public, unauthenticated widget; real memory needs real
 * accounts, which come later per the playbook's own sequencing.
 */

declare(strict_types=1);
session_start();
header('Content-Type: application/json');

$configPath = __DIR__ . '/../config.php';
if (!file_exists($configPath)) {
    http_response_code(500);
    echo json_encode(['error' => 'Server not configured. Copy config.sample.php to config.php and fill it in.']);
    exit;
}
$config = require $configPath;

$scenarios = json_decode(file_get_contents(__DIR__ . '/../scenarios.json'), true);

// ---------------------------------------------------------------------
// Playbook rules — mirrors tutor-prototype/tutor.py exactly.
// ---------------------------------------------------------------------

const LEVEL_RULES = [
    'A2' => 'Correct meaning-breaking errors only; log everything else silently. Keep your own turns short, one idea per sentence, common vocabulary only. Ask closed or short-answer questions, one at a time.',
    'B1' => "Correct meaning-breaking errors immediately; give a gentle recast (reuse the correct form naturally in your next line) for recurring grammar patterns like tense or agreement. Speak at a natural pace and introduce everyday idiom deliberately. Ask open questions with one follow-up for detail.",
    'B2' => 'Correct meaning-breaking errors immediately; gentle recast anything that would read as unprofessional in the scenario. Log minor slips silently rather than interrupting. Speak at full native pace and push for specificity and complete sentences. Push for elaboration and opinion; introduce mild disagreement.',
    'C1' => 'Correct throughout, including register and word choice, not just grammar. Push toward sophisticated, idiomatic phrasing. No simplification of your own language — treat the student as a colleague, not a learner. Use abstract, hypothetical, or consultative framing.',
];

const SPANISH_POLICY = [
    'A2' => 'Release valve enabled: if the student is visibly stuck, you may name one Spanish word to unblock the thought, then bridge back to English in the same turn. Cap yourself at roughly once this session.',
    'B1' => 'Release valve enabled: if the student is visibly stuck, you may name one Spanish word to unblock the thought, then bridge back to English in the same turn. Cap yourself at roughly once this session.',
    'B2' => "Offer an English paraphrase first when the student is stuck. Only fall back to naming a single Spanish word if that paraphrase doesn't land.",
    'C1' => 'Disabled. If the student reaches for Spanish, ask them to describe around the word in English instead — that struggle is the exercise.',
];

const CORRECTION_TIERS = <<<'EOT'
Every error you notice gets exactly one of three treatments — decide per error, not per turn:

TIER 1 — Immediate correction. Only when the listener genuinely could not have understood, or the error would embarrass the student in the real situation being role-played. Stop, correct in one short line, get a quick repeat, move on.

TIER 2 — Gentle recast. The sentence was understood, but the error is a pattern worth surfacing (recurring tense mistake, false friend, unnatural collocation). Do not stop the student — reuse the corrected form naturally in your own next line.

TIER 3 — Silent log. Minor slips (articles, small prepositions, small pronunciation wobbles). Never surfaced in the conversation itself — remember it, it appears in the end-of-call report instead.

Working rule: when genuinely unsure between Tier 1 and Tier 2, choose Tier 2.
EOT;

const SESSION_ARC = <<<'EOT'
This is a text chat standing in for a call, so pace yourself by conversational turn:

- Turn 1: greet the student, introduce yourself as Juno (this is their first-ever session — you have no memory of them). State the mode for this call in one plain sentence.
- Turn 2: one low-stakes warm-up question. Correction suppressed to Tier 3 only — this turn calibrates level by ear.
- Middle turns: the actual mode content, at full correction tiering for the student's level.
- As the conversation winds toward a close: layer in one fluency push (expand a short answer with a connector, or repeat something faster and more confidently), then soften the topic.
- On close: give two sentences of spoken feedback before signing off — one genuine strength, one clear focus for next time — and never end on a correction.
EOT;

const PRONUNCIATION_POLICY = <<<'EOT'
You have no phoneme-level pronunciation scoring — you're reading text, not audio. Never invent a score. For words the student typed that are commonly mispronounced by Spanish-L1 speakers, you may flag the word with its IPA and one line of what to watch for plus a short drill — framed as "practice this," never as a measurement of what you heard.
EOT;

const HOUSE_STYLE = "House style for corrections: short and specific, the way S&R's own class recaps read — e.g. 'FAN, not fun — and a big fan OF' or 'the swallowed CAN'T, plus DELIVER is the verb (delivery is the noun)'. Never a generic 'grammar mistake' label.";

const REPORT_TOOL = [
    'name' => 'submit_session_report',
    'description' => 'Submit the structured end-of-call report for this tutoring session, in the S&R class-recap format.',
    'input_schema' => [
        'type' => 'object',
        'properties' => [
            'what_we_did' => ['type' => 'string', 'description' => "Two or three sentences summarizing the session."],
            'corrections' => [
                'type' => 'array', 'minItems' => 3, 'maxItems' => 8,
                'items' => [
                    'type' => 'object',
                    'properties' => [
                        'tier' => ['type' => 'string', 'enum' => ['immediate', 'gentle_recast', 'silent_log']],
                        'said' => ['type' => 'string'],
                        'better' => ['type' => 'string'],
                        'note' => ['type' => 'string'],
                    ],
                    'required' => ['tier', 'said', 'better', 'note'],
                ],
            ],
            'word_traps' => [
                'type' => 'array', 'maxItems' => 5,
                'items' => [
                    'type' => 'object',
                    'properties' => [
                        'you_said' => ['type' => 'string'],
                        'problem' => ['type' => 'string'],
                        'we_say' => ['type' => 'string'],
                    ],
                    'required' => ['you_said', 'problem', 'we_say'],
                ],
            ],
            'pronunciation' => [
                'type' => 'array',
                'items' => [
                    'type' => 'object',
                    'properties' => [
                        'word' => ['type' => 'string'],
                        'ipa' => ['type' => 'string'],
                        'watch_for' => ['type' => 'string'],
                    ],
                    'required' => ['word', 'ipa', 'watch_for'],
                ],
            ],
            'vocabulary_learned' => [
                'type' => 'array',
                'items' => [
                    'type' => 'object',
                    'properties' => ['term' => ['type' => 'string'], 'meaning' => ['type' => 'string']],
                    'required' => ['term', 'meaning'],
                ],
            ],
            'what_went_well' => ['type' => 'array', 'minItems' => 1, 'maxItems' => 4, 'items' => ['type' => 'string']],
            'homework' => ['type' => 'array', 'items' => ['type' => 'string']],
            'next_recommendation' => ['type' => 'string'],
        ],
        'required' => [
            'what_we_did', 'corrections', 'word_traps', 'pronunciation',
            'vocabulary_learned', 'what_went_well', 'homework', 'next_recommendation',
        ],
    ],
];

// ---------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------

function fail(int $code, string $message): void {
    http_response_code($code);
    echo json_encode(['error' => $message]);
    exit;
}

function call_anthropic(string $apiKey, string $model, string $system, array $messages, int $maxTokens, ?array $tools = null, ?array $toolChoice = null): array {
    $payload = ['model' => $model, 'max_tokens' => $maxTokens, 'system' => $system, 'messages' => $messages];
    if ($tools !== null) {
        $payload['tools'] = $tools;
    }
    if ($toolChoice !== null) {
        $payload['tool_choice'] = $toolChoice;
    }

    $ch = curl_init('https://api.anthropic.com/v1/messages');
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => json_encode($payload),
        CURLOPT_HTTPHEADER => [
            'content-type: application/json',
            'x-api-key: ' . $apiKey,
            'anthropic-version: 2023-06-01',
        ],
        CURLOPT_TIMEOUT => 60,
    ]);
    $raw = curl_exec($ch);
    if ($raw === false) {
        $err = curl_error($ch);
        curl_close($ch);
        throw new Exception('Network error calling Claude: ' . $err);
    }
    $status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    $data = json_decode($raw, true);
    if ($status >= 400) {
        $msg = $data['error']['message'] ?? "HTTP $status";
        throw new Exception("Claude API error: $msg");
    }
    return $data;
}

function extract_text(array $response): string {
    foreach ($response['content'] ?? [] as $block) {
        if (($block['type'] ?? '') === 'text') {
            return $block['text'];
        }
    }
    return '';
}

function extract_tool_input(array $response, string $toolName): ?array {
    foreach ($response['content'] ?? [] as $block) {
        if (($block['type'] ?? '') === 'tool_use' && ($block['name'] ?? '') === $toolName) {
            return $block['input'];
        }
    }
    return null;
}

function build_system_prompt(string $level, string $mode, ?array $scenario): string {
    $parts = [];
    $parts[] = "You are Juno, the S&R Spain English tutor. You follow the S&R Tutor Playbook exactly. "
        . "You are warm, direct, and economical with words — not a customer service bot, no disclaimers, "
        . "no padded enthusiasm. Keep your own turns to 1-3 sentences unless the scenario calls for more. "
        . "Introduce yourself as Juno once, at the very start of the call.";
    $parts[] = "\nSTUDENT LEVEL: $level\n" . LEVEL_RULES[$level];
    $parts[] = "\nSPANISH USAGE POLICY: " . SPANISH_POLICY[$level];
    $parts[] = "\nCORRECTION LAYER:\n" . CORRECTION_TIERS;
    $parts[] = "\nSESSION ARC:\n" . SESSION_ARC;
    $parts[] = "\nPRONUNCIATION POLICY:\n" . PRONUNCIATION_POLICY;
    $parts[] = "\n" . HOUSE_STYLE;
    $parts[] = "\nThis is a public demo widget with no login — you have no memory of this student from any "
        . "prior call. Treat every call as a genuine first meeting.";

    if ($mode === 'free') {
        $parts[] = "\nMODE: Free Conversation. No fixed objective beyond speaking time and comfort. Bias "
            . "correction toward Tier 3. Follow whatever the student says interest in.";
    } else {
        $parts[] = "\nMODE: Business English — Abadía Retuerta pack.\n"
            . "Target expression: \"{$scenario['expression']}\" ({$scenario['title']}).\n"
            . "Your role: act as {$scenario['role']}.\n"
            . "Question domain: {$scenario['domain']}.\n"
            . "Correction instruction for this scenario: {$scenario['correction_instruction']}.\n"
            . "Push instruction: {$scenario['push_instruction']}.\n"
            . "Closing instruction: {$scenario['closing_instruction']}.\n"
            . "Stay in character as that role for the whole conversation.";
    }

    $parts[] = "\nThe student ends the call by clicking End Call. Nothing you say should assume you know "
        . "that's coming — react naturally when it happens.";

    return implode("\n", $parts);
}

// ---------------------------------------------------------------------
// Request handling
// ---------------------------------------------------------------------

$body = json_decode(file_get_contents('php://input'), true) ?? [];
$action = $body['action'] ?? '';

if ($action === 'auth') {
    $ok = hash_equals((string) $config['access_passphrase'], (string) ($body['passphrase'] ?? ''));
    if (!$ok) {
        fail(401, 'Wrong passphrase.');
    }
    $_SESSION['juno_authed'] = true;
    $_SESSION['juno_call_count'] = 0;
    echo json_encode(['ok' => true]);
    exit;
}

if (empty($_SESSION['juno_authed'])) {
    fail(401, 'Not authenticated.');
}

try {
    if ($action === 'start') {
        if (($_SESSION['juno_call_count'] ?? 0) >= $config['max_calls_per_session']) {
            fail(429, 'Call limit reached for this browser session. Refresh to start over.');
        }
        $_SESSION['juno_call_count'] = ($_SESSION['juno_call_count'] ?? 0) + 1;

        $mode = ($body['mode'] ?? 'free') === 'business' ? 'business' : 'free';
        $level = in_array($body['level'] ?? '', ['A2', 'B1', 'B2', 'C1'], true) ? $body['level'] : 'B1';

        $scenario = null;
        if ($mode === 'business') {
            $wantedId = $body['scenario_id'] ?? null;
            $library = $scenarios['business'];
            $scenario = $wantedId
                ? current(array_filter($library, fn($s) => $s['id'] === $wantedId)) ?: $library[array_rand($library)]
                : $library[array_rand($library)];
        }

        $system = build_system_prompt($level, $mode, $scenario ?: null);
        $messages = [['role' => 'user', 'content' => '(the call has just connected — open it)']];

        $response = call_anthropic($config['anthropic_api_key'], $config['model'], $system, $messages, 1024);
        $reply = extract_text($response);
        $messages[] = ['role' => 'assistant', 'content' => $reply];

        $_SESSION['juno_system'] = $system;
        $_SESSION['juno_messages'] = $messages;
        $_SESSION['juno_message_count'] = 0;

        echo json_encode(['reply' => $reply, 'scenario' => $scenario ? $scenario['title'] : null]);
        exit;
    }

    if ($action === 'message') {
        if (empty($_SESSION['juno_system'])) {
            fail(400, 'No call in progress. Start a call first.');
        }
        if (($_SESSION['juno_message_count'] ?? 0) >= $config['max_messages_per_session']) {
            fail(429, 'Message limit reached for this call. End the call to see your report.');
        }
        $text = trim((string) ($body['text'] ?? ''));
        if ($text === '') {
            fail(400, 'Empty message.');
        }

        $_SESSION['juno_message_count']++;
        $messages = $_SESSION['juno_messages'];
        $messages[] = ['role' => 'user', 'content' => $text];

        $response = call_anthropic($config['anthropic_api_key'], $config['model'], $_SESSION['juno_system'], $messages, 1024);
        $reply = extract_text($response);
        $messages[] = ['role' => 'assistant', 'content' => $reply];
        $_SESSION['juno_messages'] = $messages;

        echo json_encode(['reply' => $reply]);
        exit;
    }

    if ($action === 'end') {
        if (empty($_SESSION['juno_system'])) {
            fail(400, 'No call in progress.');
        }
        $messages = $_SESSION['juno_messages'];
        $messages[] = [
            'role' => 'user',
            'content' => 'The call has ended. Generate the end-of-call report now by calling '
                . 'submit_session_report, reviewing the whole conversation above for every correctable '
                . 'moment, not just the ones you spoke aloud.',
        ];

        $response = call_anthropic(
            $config['anthropic_api_key'],
            $config['model'],
            'You are producing the Tutor Playbook end-of-call report from the transcript of a call you '
                . 'just finished tutoring. Be specific and honest.',
            $messages,
            4096,
            [REPORT_TOOL],
            ['type' => 'tool', 'name' => 'submit_session_report']
        );
        $report = extract_tool_input($response, 'submit_session_report');
        if ($report === null) {
            fail(502, 'Could not generate a report for this call.');
        }

        unset($_SESSION['juno_system'], $_SESSION['juno_messages'], $_SESSION['juno_message_count']);
        echo json_encode(['report' => $report]);
        exit;
    }

    fail(400, 'Unknown action.');
} catch (Exception $e) {
    fail(502, $e->getMessage());
}
