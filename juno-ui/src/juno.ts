export type Mode = "free" | "business" | "structured";
export type Report = {
  what_we_did: string;
  corrections: { tier: string; said: string; better: string; note: string }[];
  word_traps: { you_said: string; problem: string; we_say: string }[];
  pronunciation: { word: string; ipa: string; watch_for: string }[];
  vocabulary_learned: { term: string; meaning: string }[];
  what_went_well: string[];
  homework: string[];
  next_recommendation: string;
  updated_recurring_error_patterns: string[];
  grammar_point_taught: string;
  skill_estimates: {
    enough_evidence: boolean;
    fluency: string;
    grammar: string;
    vocabulary: string;
    understanding: string;
  };
};
export type Note = {
  call_id: string;
  mode: Mode;
  started_at: string;
  minutes: number;
  report: Report;
};
export type Scenario = {
  id: string;
  title: string;
  expression: string;
  cefr: string;
  pack: string;
};
export type Message = { role: "user" | "assistant"; content: string };
export type OpenCall = {
  call_id: string;
  mode: Mode;
  level: string;
  turns: number;
  transcript: Message[];
};
export type Progress = {
  name: string;
  classes_total: number;
  minutes_total: number;
  minutes_week: number;
  exchanges_total: number;
  streak_days: number;
  week: { date: string; weekday: string; minutes: number }[];
  level: {
    ready: boolean;
    rated_classes: number;
    needed?: number;
    overall?: { band: string; plus: boolean; next_band: string };
    skills?: { key: string; label: string; band: string; plus: boolean }[];
  };
  words_learned: number;
  grammar_covered: string[];
  working_on: string[];
  next_recommendation: string;
};
export type State = {
  progress: Progress;
  notes: Note[];
  scenarios: Scenario[];
  open: OpenCall | null;
};
export async function api<T>(path: string, body: unknown = {}): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(data.error || "Could not load this part of Juno.");
  return data;
}
export async function loadState(): Promise<State> {
  const [progress, notes, resume, scenarios] = await Promise.all([
    api<Progress>("/api/progress", {
      tz_offset: new Date().getTimezoneOffset(),
    }),
    api<{ notes: Note[] }>("/api/notes"),
    api<{ open_call: OpenCall | null }>("/api/resume"),
    fetch("/scenarios.json").then((r) => {
      if (!r.ok) throw Error("Lessons could not load");
      return r.json();
    }),
  ]);
  return {
    progress,
    notes: notes.notes,
    open: resume.open_call,
    scenarios: scenarios.business_packs.flatMap(
      (pack: { label: string; scenarios: Scenario[] }) =>
        pack.scenarios.map((s) => ({ ...s, pack: pack.label })),
    ),
  };
}
export async function message(
  text: string,
  key: string,
  onDelta: (piece: string) => void,
): Promise<{ reply: string; wrapped: boolean }> {
  const response = await fetch("/api/message", {
    method: "POST",
    credentials: "same-origin",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text, idempotency_key: key }),
  });
  if (!(response.headers.get("content-type") || "").includes("ndjson")) {
    const data = await response.json();
    if (!response.ok)
      throw Error(data.error || "Your reply could not be saved.");
    onDelta(data.reply);
    return data;
  }
  if (!response.body) throw Error("The reply could not be read.");
  const reader = response.body.getReader(),
    decoder = new TextDecoder();
  let buffer = "",
    final: { reply: string; wrapped: boolean } | null = null;
  const consume = (line: string) => {
    if (!line.trim()) return;
    const event = JSON.parse(line);
    if (event.type === "error") throw Error(event.error);
    if (event.type === "delta") onDelta(event.text);
    if (event.type === "final") final = event;
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let end;
      while ((end = buffer.indexOf("\n")) >= 0) {
        consume(buffer.slice(0, end));
        buffer = buffer.slice(end + 1);
      }
    }
    buffer += decoder.decode();
    if (buffer.trim()) consume(buffer);
    if (!final)
      throw Error("The reply stopped early. Resume your saved class.");
    return final;
  } finally {
    reader.releaseLock();
  }
}
