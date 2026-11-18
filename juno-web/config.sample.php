<?php
/**
 * Copy this file to config.php (same folder) and fill in your real values.
 * config.php must NEVER be committed to git or made public — it holds your
 * Anthropic API key. The .htaccess in this folder blocks direct web access
 * to it, but keep it out of version control regardless.
 */
return [
    // Get this from https://console.anthropic.com — Settings -> API Keys.
    // Billing is tied to your Anthropic account, not to this hosting.
    'anthropic_api_key' => 'sk-ant-REPLACE-ME',

    // Whoever tries Juno on the site needs to type this first. Change it
    // to something only you (and whoever you share it with) know.
    'access_passphrase' => 'change-me',

    // claude-opus-5 is the default recommendation. claude-sonnet-5 is
    // noticeably cheaper per call if you want to keep costs down while
    // testing on a public page — quality is still strong for this task.
    'model' => 'claude-opus-5',

    // Hard caps so one browser session can't run up a large bill.
    'max_messages_per_session' => 30,
    'max_calls_per_session' => 5,
];
