import {
  type ReactNode,
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  ArrowLeft,
  ArrowRight,
  BarChart3,
  BookOpen,
  Check,
  CircleHelp,
  Mic,
  MicOff,
  Pause,
  Play,
  Sparkles,
  StopCircle,
  Volume2,
  X,
} from "lucide-react";
import {
  Link,
  Route,
  Switch,
  Router as WouterRouter,
  useLocation,
} from "wouter";
import { ErrorBoundary } from "./components/error-boundary";
import {
  api,
  loadState,
  message,
  type Mode,
  type State,
  type Message,
  type OpenCall,
} from "./juno";
const partner = "Juno";
const Data = createContext<{
  data: State;
  refresh: () => Promise<void>;
} | null>(null);
function useJuno() {
  return useContext(Data)!;
}
function readStorage<T>(key: string, fallback: T): T {
  try {
    return JSON.parse(localStorage.getItem(key) ?? "") as T;
  } catch {
    return fallback;
  }
}
function say(text: string, onUnsupported?: () => void) {
  if (!("speechSynthesis" in window)) {
    onUnsupported?.();
    return false;
  }
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "en-GB";
  utterance.rate = 0.88;
  window.speechSynthesis.speak(utterance);
  return true;
}

function Shell({ children }: { children: ReactNode }) {
  const [location] = useLocation();
  const { data } = useJuno();
  const name = data.progress.name;
  const [editingName, setEditingName] = useState(false);
  const nav = [
    { href: "/", label: "Today", icon: Sparkles },
    { href: "/practice", label: "Practice", icon: Mic },
    { href: "/progress", label: "Progress", icon: BarChart3 },
  ];
  return (
    <div className="fp-noise min-h-[100dvh] bg-background text-foreground">
      <div className="mx-auto flex min-h-[100dvh] max-w-[1380px]">
        <aside className="hidden w-[224px] shrink-0 flex-col border-r border-border bg-sidebar/60 px-5 py-7 lg:flex">
          <Link
            href="/"
            className="mb-14 flex items-center gap-3 no-underline"
            data-testid="link-brand"
          >
            <span className="flex h-9 w-9 items-center justify-center rounded-[13px] bg-primary text-primary-foreground">
              <Volume2 size={18} strokeWidth={2.4} />
            </span>
            <span className="font-serif text-[25px] tracking-tight">
              Juno<span className="text-primary">.</span>
              <small className="block font-sans text-[9px] tracking-[.12em] text-muted-foreground">
                BY S&amp;R SPAIN
              </small>
            </span>
          </Link>
          <p className="mb-4 px-3 text-[10px] font-semibold uppercase tracking-[.18em] text-muted-foreground">
            Your space
          </p>
          <nav className="flex flex-col gap-1" aria-label="Primary navigation">
            {nav.map(({ href, label, icon: Icon }) => (
              <Link
                key={href}
                href={href}
                data-testid={`link-nav-${label.toLowerCase()}`}
                className={`flex items-center gap-3 rounded-xl px-3 py-3 text-sm font-medium transition-colors ${location === href ? "bg-primary text-primary-foreground shadow-sm" : "text-muted-foreground hover:bg-secondary hover:text-foreground"}`}
              >
                <Icon size={17} />
                <span>{label}</span>
              </Link>
            ))}
            <Link
              href="/notes"
              data-testid="link-nav-notes"
              className={`flex items-center gap-3 rounded-xl px-3 py-3 text-sm font-medium transition-colors ${location === "/notes" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-secondary hover:text-foreground"}`}
            >
              <BookOpen size={17} />
              <span>Session notes</span>
            </Link>
          </nav>
          <div className="mt-auto rounded-2xl border border-border bg-card p-4">
            <p className="text-[10px] font-semibold uppercase tracking-[.16em] text-muted-foreground">
              This week
            </p>
            <p className="mt-2 font-serif text-3xl">
              {data.progress.minutes_week}
              <span className="text-base text-muted-foreground"> min</span>
            </p>
            <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-secondary">
              <div
                className="h-full rounded-full bg-accent"
                style={{
                  width: `${Math.min(100, (data.progress.minutes_week / 30) * 100)}%`,
                }}
              />
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              A gentle 30 min goal
            </p>
          </div>
        </aside>
        <main className="min-w-0 flex-1 pb-24 lg:pb-0">{children}</main>
      </div>
      <nav
        className="fixed inset-x-0 bottom-0 z-30 flex h-[72px] items-center justify-around border-t border-border bg-background/95 px-4 backdrop-blur lg:hidden"
        aria-label="Mobile navigation"
      >
        {nav.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            data-testid={`link-mobile-${label.toLowerCase()}`}
            className={`flex min-w-[76px] flex-col items-center gap-1 text-[10px] font-semibold uppercase tracking-[.08em] ${location === href ? "text-primary" : "text-muted-foreground"}`}
          >
            <Icon size={19} />
            <span>{label}</span>
          </Link>
        ))}
        <Link
          href="/notes"
          data-testid="link-mobile-notes"
          className={`flex min-w-[76px] flex-col items-center gap-1 text-[10px] font-semibold uppercase tracking-[.08em] ${location === "/notes" ? "text-primary" : "text-muted-foreground"}`}
        >
          <BookOpen size={19} />
          <span>Notes</span>
        </Link>
      </nav>
      {editingName && (
        <div
          className="fixed inset-0 z-40 flex items-center justify-center bg-foreground/30 p-5"
          role="dialog"
          aria-modal="true"
        >
          <div className="w-full max-w-sm rounded-3xl bg-card p-6 shadow-2xl">
            <div className="flex justify-between">
              <h2 className="font-serif text-2xl">Your Juno account</h2>
              <button onClick={() => setEditingName(false)} aria-label="Close">
                <X size={20} />
              </button>
            </div>
            <p className="mt-5">{name}</p>
            <p className="mt-2 text-sm text-muted-foreground">
              Fictional preview student. Your production account is never loaded
              here.
            </p>
            <button
              onClick={() => setEditingName(false)}
              className="mt-6 rounded-xl bg-primary px-5 py-3 text-primary-foreground"
            >
              Back to practice
            </button>
          </div>
        </div>
      )}
      <button
        onClick={() => setEditingName(true)}
        className="fixed right-5 top-5 z-20 hidden rounded-full border border-border bg-card px-3 py-2 text-xs text-muted-foreground shadow-sm hover:text-foreground lg:block"
        data-testid="button-edit-name"
      >
        Hi, {name}
      </button>
    </div>
  );
}

function PageHeader({
  eyebrow,
  title,
  children,
}: {
  eyebrow: string;
  title: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header className="mb-8 flex items-end justify-between gap-4">
      <div>
        <p className="mb-2 text-[10px] font-semibold uppercase tracking-[.18em] text-muted-foreground">
          {eyebrow}
        </p>
        <h1 className="font-serif text-[40px] leading-[.98] tracking-tight sm:text-[48px]">
          {title}
        </h1>
      </div>
      {children}
    </header>
  );
}

function Home() {
  const [, setLocation] = useLocation();
  const { data } = useJuno();
  const name = data.progress.name;
  const latest = data.notes[0];
  const start = (mode: Mode) => {
    localStorage.setItem("juno-visual-selected-mode", mode);
    setLocation(`/practice?mode=${mode}`);
  };
  const cards = [
    {
      mode: "free" as Mode,
      title: "Free talk",
      body: "A relaxed topic, with S&R’s gentle correction rules.",
      icon: Mic,
      dark: true,
    },
    {
      mode: "business" as Mode,
      title: "Business scenario",
      body: "Choose from Juno’s existing lessons.",
      icon: BookOpen,
      dark: false,
    },
    {
      mode: "structured" as Mode,
      title: "Structured class",
      body: "Follow the six-part S&R lesson.",
      icon: Sparkles,
      dark: false,
    },
  ];
  return (
    <div className="fp-page mx-auto max-w-5xl px-5 py-10 sm:px-10 lg:px-16 lg:py-16">
      <PageHeader
        eyebrow="Your practice space"
        title={
          <>
            Good evening,
            <br />
            <i>{name}</i>
          </>
        }
      >
        <div className="rounded-2xl border border-border bg-card px-4 py-3 text-center">
          <div className="font-serif text-2xl text-primary">
            {data.progress.level.ready
              ? data.progress.level.overall?.band
              : "—"}
          </div>
          <div className="text-[9px] font-bold uppercase tracking-[.16em] text-muted-foreground">
            level
          </div>
        </div>
      </PageHeader>
      <p className="mb-3 text-[10px] font-semibold uppercase tracking-[.18em] text-muted-foreground">
        Talk to {partner}
      </p>
      <section
        className="grid gap-3 md:grid-cols-3"
        aria-label="Conversation modes"
      >
        {cards.map(({ mode, title, body, icon: Icon, dark }) => (
          <button
            key={mode}
            onClick={() => start(mode)}
            data-testid={`button-start-${mode}`}
            className={`group flex min-h-[174px] flex-col justify-between rounded-[25px] p-5 text-left transition-transform hover:-translate-y-1 ${dark ? "bg-foreground text-background" : "border border-border bg-card hover:bg-secondary"}`}
          >
            <div
              className={`flex h-11 w-11 items-center justify-center rounded-full ${dark ? "bg-primary text-primary-foreground" : "bg-secondary text-foreground"}`}
            >
              <Icon size={19} />
            </div>
            <div>
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-[17px] font-semibold">{title}</h2>
                <ArrowRight
                  size={17}
                  className="opacity-50 transition-transform group-hover:translate-x-1"
                />
              </div>
              <p
                className={`mt-1 text-[13px] leading-5 ${dark ? "text-background/65" : "text-muted-foreground"}`}
              >
                {body}
              </p>
            </div>
          </button>
        ))}
      </section>
      <section className="mt-8 grid gap-3 md:grid-cols-[1.15fr_.85fr]">
        <button
          onClick={() => setLocation("/drill")}
          className="rounded-[25px] border border-border bg-card p-5 text-left transition-colors hover:bg-secondary"
          data-testid="button-open-drill"
        >
          <div className="mb-4 flex items-center justify-between">
            <span className="text-[10px] font-semibold uppercase tracking-[.18em] text-muted-foreground">
              Vocabulary practice
            </span>
            <span className="text-xs text-muted-foreground">
              From your reports
            </span>
          </div>
          <div aria-hidden="true" className="mb-5 h-px bg-border" />
          <div className="flex items-center gap-4">
            <span className="flex h-12 w-12 items-center justify-center rounded-full bg-secondary">
              <Play size={17} fill="currentColor" />
            </span>
            <span>
              <strong className="block text-[15px]">
                Words to take into your next class
              </strong>
              <small className="text-xs text-muted-foreground">
                Listen and review your saved vocabulary
              </small>
            </span>
            <ArrowRight size={16} className="ml-auto text-muted-foreground" />
          </div>
        </button>
        <button
          onClick={() => setLocation("/notes")}
          className="flex items-center justify-between rounded-[25px] bg-secondary p-5 text-left transition-colors hover:bg-muted"
          data-testid="button-open-notes"
        >
          <span>
            <strong className="block text-[15px]">
              Your latest session notes
            </strong>
            <small className="mt-1 block text-xs text-muted-foreground">
              {latest
                ? `${latest.report.corrections.length} corrections · ${latest.report.vocabulary_learned.length} words`
                : "Finish a class to see your notes"}
            </small>
          </span>
          <ArrowRight size={16} className="text-muted-foreground" />
        </button>
      </section>
      <div className="mt-8 flex items-center gap-3 border-t border-border pt-5 text-xs text-muted-foreground">
        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-accent/15 text-accent">
          <Check size={14} />
        </span>
        <span>Small practice counts. You are building a speaking habit.</span>
      </div>
    </div>
  );
}

function Practice() {
  const [, setLocation] = useLocation();
  const query = new URLSearchParams(window.location.search);
  const initial =
    (query.get("mode") as Mode) ||
    readStorage<Mode>("juno-visual-selected-mode", "free");
  const [mode, setMode] = useState<Mode>(
    ["free", "business", "structured"].includes(initial) ? initial : "free",
  );
  const { data } = useJuno();
  const [scenario, setScenario] = useState(data.scenarios[0]?.id || "");
  const [level, setLevel] = useState("B1");
  const modes: { id: Mode; title: string; body: string; icon: typeof Mic }[] = [
    {
      id: "free",
      title: "Free talk",
      body: "A relaxed conversation with no fixed topic.",
      icon: Mic,
    },
    {
      id: "business",
      title: "Business scenario",
      body: "Practise one of Juno’s existing business lessons.",
      icon: BookOpen,
    },
    {
      id: "structured",
      title: "Structured class",
      body: "Warm up, review, learn, practise, then reflect with S&R.",
      icon: Sparkles,
    },
  ];
  return (
    <div className="fp-page mx-auto max-w-3xl px-5 py-10 sm:px-10 lg:py-16">
      <Link
        href="/"
        className="mb-10 inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
        data-testid="link-practice-back"
      >
        <ArrowLeft size={15} /> Today
      </Link>
      <PageHeader
        eyebrow="Before you begin"
        title={
          <>
            Choose how
            <br />
            <i>you want to talk.</i>
          </>
        }
      />
      <div className="space-y-3">
        {modes.map(({ id, title, body, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setMode(id)}
            data-testid={`button-select-${id}`}
            className={`flex w-full items-center gap-4 rounded-2xl border p-4 text-left transition-all ${mode === id ? "border-primary bg-primary/8 ring-2 ring-primary/20" : "border-border bg-card hover:bg-secondary"}`}
          >
            <span
              className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-full ${mode === id ? "bg-primary text-primary-foreground" : "bg-secondary"}`}
            >
              <Icon size={18} />
            </span>
            <span className="min-w-0 flex-1">
              <strong className="block text-[16px]">{title}</strong>
              <small className="text-sm text-muted-foreground">{body}</small>
            </span>
            <span
              className={`flex h-5 w-5 items-center justify-center rounded-full border ${mode === id ? "border-primary bg-primary text-primary-foreground" : "border-border"}`}
            >
              {mode === id && <Check size={13} />}
            </span>
          </button>
        ))}
      </div>
      <div className="mt-6 grid gap-3 sm:grid-cols-2">
        <label className="text-xs font-medium">
          Practice level
          <select
            aria-label="Practice level"
            value={level}
            onChange={(e) => setLevel(e.target.value)}
            className="mt-2 w-full rounded-xl border border-border bg-card p-3"
          >
            {["A2", "B1", "B2", "C1"].map((l) => (
              <option key={l}>{l}</option>
            ))}
          </select>
        </label>
        {mode === "business" && (
          <label className="text-xs font-medium">
            Juno lesson
            <select
              aria-label="Juno lesson"
              value={scenario}
              onChange={(e) => setScenario(e.target.value)}
              className="mt-2 w-full rounded-xl border border-border bg-card p-3"
            >
              {data.scenarios.map((s) => (
                <option value={s.id} key={s.id}>
                  {s.title} · {s.cefr}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <div className="mt-7 rounded-2xl bg-secondary/70 p-5">
        <p className="text-[10px] font-semibold uppercase tracking-[.18em] text-muted-foreground">
          A useful nudge
        </p>
        <p className="mt-2 font-serif text-2xl leading-tight">
          You do not need perfect words. You need a little room to find them.
        </p>
      </div>
      <button
        onClick={() => {
          localStorage.setItem("juno-visual-selected-mode", mode);
          setLocation(
            `/session?mode=${mode}&level=${level}&scenario=${scenario}`,
          );
        }}
        className="mt-6 flex h-14 w-full items-center justify-center gap-2 rounded-2xl bg-primary font-semibold text-primary-foreground shadow-sm transition-transform hover:-translate-y-0.5"
        data-testid="button-begin-conversation"
      >
        {data.open ? "Resume saved class" : "Begin conversation"}{" "}
        <ArrowRight size={17} />
      </button>
    </div>
  );
}

function Session() {
  const { refresh } = useJuno();
  const [, navigate] = useLocation();
  const [call, setCall] = useState<OpenCall | null>(null);
  const [transcript, setTranscript] = useState<Message[]>([]);
  const [caption, setCaption] = useState("Connecting to your practice space…");
  const [busy, setBusy] = useState(true);
  const [paused, setPaused] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [help, setHelp] = useState("");
  const [mic, setMic] = useState(false);
  const [voice, setVoice] = useState(true);
  const starting = useRef(false);
  const pending = useRef<{ text: string; key: string } | null>(null);
  const recognition = useRef<any>(null);
  const query = new URLSearchParams(window.location.search);
  const requested = query.get("mode") || "free";
  const isCoach = call?.mode === "structured";
  useEffect(() => {
    if (starting.current) return;
    starting.current = true;
    let alive = true;
    (async () => {
      try {
        const resume = await api<{ open_call: OpenCall | null }>("/api/resume");
        let current = resume.open_call;
        if (!current) {
          const started = await api<{ call_id: string; reply: string }>(
            "/api/start",
            {
              mode: requested,
              level: query.get("level") || "B1",
              scenario_id: query.get("scenario") || undefined,
            },
          );
          current = {
            call_id: started.call_id,
            mode: requested as Mode,
            level: query.get("level") || "B1",
            turns: 0,
            transcript: [{ role: "assistant", content: started.reply }],
          };
        }
        if (alive) {
          setCall(current);
          setTranscript(current.transcript);
          setCaption(
            current.transcript.filter((m) => m.role === "assistant").at(-1)
              ?.content || "Your class is ready.",
          );
          await refresh();
        }
      } catch (e) {
        if (alive) setError((e as Error).message);
      } finally {
        if (alive) setBusy(false);
      }
    })();
    return () => {
      alive = false;
      recognition.current?.abort();
      window.speechSynthesis?.cancel();
    };
  }, []);
  useEffect(() => {
    if (paused || !call) return;
    const timer = window.setInterval(() => setElapsed((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, [paused, call]);
  const send = async (value = input) => {
    const text = value.trim();
    if (!text || busy || paused || !call) return;
    if (!pending.current || pending.current.text !== text)
      pending.current = { text, key: crypto.randomUUID() };
    setBusy(true);
    setError("");
    setHelp("");
    window.speechSynthesis?.cancel();
    let shown = "";
    try {
      const result = await message(text, pending.current.key, (piece) => {
        shown = shown ? `${shown} ${piece}` : piece;
        setCaption(shown);
      });
      setTranscript((previous) => [
        ...previous,
        { role: "user", content: text },
        { role: "assistant", content: result.reply },
      ]);
      pending.current = null;
      setInput("");
      if (voice) say(result.reply);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
      setInput(text);
    } finally {
      setBusy(false);
    }
  };
  const finish = async () => {
    if (busy || !call) return;
    setBusy(true);
    setError("");
    recognition.current?.abort();
    window.speechSynthesis?.cancel();
    try {
      await api("/api/end", { call_id: call.call_id });
      await refresh();
      navigate("/notes");
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };
  const askHelp = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await api<{
        explanation: string;
        answers: { en: string; es: string }[];
      }>("/api/help");
      setHelp(
        [
          result.explanation,
          ...result.answers.map((a) => `${a.en} — ${a.es}`),
        ].join("\n"),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const lookup = async (word: string) => {
    try {
      const result = await api<{ meaning: string; example: string }>(
        "/api/define",
        { word: word.replace(/[^a-zA-Z’'-]/g, ""), sentence: caption },
      );
      setHelp(`${word}: ${result.meaning}\n${result.example}`);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const listen = () => {
    if (mic) {
      recognition.current?.stop();
      return;
    }
    const Speech =
      (window as any).SpeechRecognition ||
      (window as any).webkitSpeechRecognition;
    if (!Speech) {
      setError(
        "Voice input is unavailable here. You can type your answer below.",
      );
      return;
    }
    window.speechSynthesis?.cancel();
    const rec = new Speech();
    rec.lang = "en-US";
    rec.interimResults = true;
    rec.onresult = (event: any) => {
      setInput(
        Array.from(event.results)
          .map((r: any) => r[0].transcript)
          .join(" "),
      );
    };
    rec.onstart = () => setMic(true);
    rec.onend = () => setMic(false);
    rec.onerror = () => {
      setMic(false);
      setError(
        "Microphone unavailable. Allow microphone access or type instead.",
      );
    };
    recognition.current = rec;
    rec.start();
  };
  const time = `${String(Math.floor(elapsed / 60)).padStart(2, "0")}:${String(elapsed % 60).padStart(2, "0")}`;
  return (
    <div
      className={`fp-page min-h-[100dvh] ${isCoach ? "bg-[#111821]" : "bg-[#171423]"} text-[#f6f2ea]`}
    >
      <div className="mx-auto flex min-h-[100dvh] max-w-3xl flex-col px-5 py-8 sm:px-10 lg:py-12">
        <div className="flex justify-between">
          <span className="text-[10px] font-semibold uppercase tracking-[.17em] text-[#a9a3b8]">
            Juno ·{" "}
            {call?.mode === "business"
              ? "business scenario"
              : isCoach
                ? "structured class"
                : "free talk"}
          </span>
          <span
            className="font-mono text-xs text-[#a9a3b8]"
            data-testid="text-session-clock"
          >
            {time}
          </span>
        </div>
        <div className="flex flex-1 flex-col items-center justify-center py-8">
          <div
            className="mb-7 max-w-xl text-center font-serif text-[27px] leading-tight sm:text-3xl"
            aria-live="polite"
            data-testid="text-session-caption"
          >
            {caption.split(/(\s+)/).map((word, i) =>
              word.trim() ? (
                <button
                  key={i}
                  onClick={() => lookup(word)}
                  disabled={busy}
                  className="rounded hover:text-[#b99fff]"
                  title={`Explain ${word}`}
                >
                  {word}
                </button>
              ) : (
                word
              ),
            )}
          </div>
          <div
            className="relative flex h-56 w-56 items-center justify-center"
            aria-label="Reserved space for Juno’s future animated teacher"
            data-testid="teacher-avatar-slot"
          >
            <div
              className={`fp-orb-ring absolute inset-0 rounded-full border ${isCoach ? "border-accent/45" : "border-primary/45"}`}
            />
            <div
              className={`fp-orb-ring absolute inset-6 rounded-full border ${isCoach ? "border-accent/25" : "border-primary/25"}`}
              style={{ animationDelay: ".9s" }}
            />
            <div
              className={`fp-orb-pulse flex h-40 w-40 items-center justify-center rounded-full ${isCoach ? "bg-[radial-gradient(circle_at_32%_25%,#a7f0d0,#3eb98f_48%,#11634c)] shadow-[0_0_85px_rgba(63,191,149,.42)]" : "bg-[radial-gradient(circle_at_32%_25%,#b99fff,#7948e8_48%,#421cae)] shadow-[0_0_85px_rgba(122,75,238,.48)]"}`}
            >
              <span className="font-serif text-5xl">J</span>
            </div>
          </div>
          <p
            className="mt-5 text-xs uppercase tracking-[.16em] text-[#a9a3b8]"
            role="status"
            data-testid="status-session"
          >
            {error
              ? "Connection needs attention"
              : busy
                ? "Juno is preparing a reply"
                : paused
                  ? "Paused"
                  : mic
                    ? "Listening — tap the mic when finished"
                    : "Your turn to speak or type"}
          </p>
          <p className="mt-2 text-[10px] text-[#a9a3b8]">
            Avatar space reserved · no avatar service connected
          </p>
        </div>
        <div
          className="mb-5 max-h-44 space-y-2 overflow-y-auto border-t border-white/10 pt-5"
          aria-label="Conversation transcript"
        >
          {transcript.map((m, i) => (
            <div key={i} className="flex gap-3 text-sm">
              <span className="w-10 shrink-0 text-[10px] uppercase text-[#a9a3b8]">
                {m.role === "assistant" ? "Juno" : "You"}
              </span>
              <span>{m.content}</span>
            </div>
          ))}
        </div>
        {help && (
          <div
            className="mb-4 whitespace-pre-line rounded-2xl border border-accent/30 bg-accent/10 p-4 text-sm text-[#d9f4e8]"
            data-testid="status-help"
          >
            {help}
          </div>
        )}
        {error && (
          <p role="alert" className="mb-4 rounded-xl bg-white/10 p-3 text-sm">
            {error}
          </p>
        )}
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void send();
          }}
          className="mb-4 flex gap-2"
        >
          <label htmlFor="answer" className="sr-only">
            Your answer
          </label>
          <input
            id="answer"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={busy || paused}
            placeholder="Type your answer…"
            className="min-w-0 flex-1 rounded-xl border border-white/20 bg-white/5 p-3"
          />
          <button
            disabled={busy || paused || !input.trim()}
            className="rounded-xl bg-primary px-4 font-semibold disabled:opacity-40"
          >
            Send
          </button>
        </form>
        <div className="grid grid-cols-3 gap-2">
          <button
            onClick={() => {
              setPaused(!paused);
              recognition.current?.abort();
              window.speechSynthesis?.cancel();
            }}
            disabled={busy}
            className="flex h-12 items-center justify-center gap-2 rounded-xl border border-white/15 bg-white/5 text-sm disabled:opacity-40"
            data-testid="button-toggle-pause"
          >
            {paused ? <Play size={15} /> : <Pause size={15} />}{" "}
            {paused ? "Resume" : "Pause"}
          </button>
          <button
            onClick={askHelp}
            disabled={busy || paused || !call}
            className="flex h-12 items-center justify-center gap-2 rounded-xl border border-white/15 bg-white/5 text-sm disabled:opacity-40"
            data-testid="button-help"
          >
            <CircleHelp size={15} />
            Help me
          </button>
          <button
            onClick={finish}
            disabled={busy || !call}
            className="flex h-12 items-center justify-center gap-2 rounded-xl bg-[#f6f2ea] text-sm font-semibold text-[#171423] disabled:opacity-40"
            data-testid="button-end-session"
          >
            <StopCircle size={15} />
            End
          </button>
        </div>
        <div className="mt-4 flex flex-wrap justify-center gap-5 text-xs text-[#a9a3b8]">
          <button
            onClick={listen}
            disabled={busy || paused}
            data-testid="button-toggle-mute"
          >
            {mic ? (
              <MicOff size={14} className="inline mr-2" />
            ) : (
              <Mic size={14} className="inline mr-2" />
            )}
            {mic ? "Stop microphone" : "Use microphone"}
          </button>
          <label>
            <input
              type="checkbox"
              checked={voice}
              onChange={(e) => {
                setVoice(e.target.checked);
                if (!e.target.checked) window.speechSynthesis?.cancel();
              }}
            />{" "}
            Juno speaks
          </label>
          <Link href="/">Leave class saved</Link>
        </div>
      </div>
    </div>
  );
}
function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <p className="mb-3 mt-8 text-[10px] font-semibold uppercase tracking-[.18em] text-muted-foreground">
      {children}
    </p>
  );
}
function Notes() {
  const { data } = useJuno();
  const [selected, setSelected] = useState(data.notes[0]?.call_id);
  const note = data.notes.find((n) => n.call_id === selected) || data.notes[0];
  if (!note)
    return (
      <div className="mx-auto max-w-3xl px-5 py-16">
        <PageHeader
          eyebrow="Session notes"
          title={
            <>
              Your next page
              <br />
              <i>starts with a conversation.</i>
            </>
          }
        />
        <p className="text-muted-foreground">
          Finish a Juno class to see its summary, corrections, vocabulary and
          homework here.
        </p>
        <Link
          href="/practice"
          className="mt-8 inline-block rounded-xl bg-primary px-5 py-3 text-primary-foreground"
        >
          Start a class
        </Link>
      </div>
    );
  const r = note.report;
  return (
    <div className="fp-page mx-auto max-w-3xl px-5 py-10 sm:px-10 lg:py-16">
      <PageHeader
        eyebrow="Session notes"
        title={
          <>
            Your class,
            <br />
            <i>in your own words.</i>
          </>
        }
      />
      <label className="text-xs text-muted-foreground">
        Saved class
        <select
          aria-label="Saved class"
          value={note.call_id}
          onChange={(e) => setSelected(e.target.value)}
          className="mb-6 mt-2 block w-full rounded-xl border border-border bg-card p-3"
        >
          {data.notes.map((n) => (
            <option value={n.call_id} key={n.call_id}>
              {new Date(n.started_at).toLocaleString()} · {n.mode}
            </option>
          ))}
        </select>
      </label>
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-2xl border border-border bg-card p-4">
          <p className="font-serif text-3xl">{note.minutes} min</p>
          <p className="mt-1 text-xs text-muted-foreground">
            class duration · not measured speaking time
          </p>
        </div>
        <div className="rounded-2xl border border-border bg-card p-4">
          <p className="font-serif text-3xl">{r.corrections.length}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            corrections in this report
          </p>
        </div>
      </div>
      <SectionLabel>What we did</SectionLabel>
      <p className="text-sm leading-7 text-muted-foreground">{r.what_we_did}</p>
      <SectionLabel>Your corrections</SectionLabel>
      {r.corrections.length ? (
        r.corrections.map((c, i) => (
          <article
            key={i}
            className="mb-3 rounded-[21px] border border-border bg-card p-5"
            data-testid={`card-correction-${i}`}
          >
            <p className="text-[15px] leading-6 text-muted-foreground line-through">
              {c.said}
            </p>
            <p className="mt-1 text-[15px] font-semibold">{c.better}</p>
            <p className="mt-3 text-xs leading-5 text-muted-foreground">
              {c.note}
            </p>
          </article>
        ))
      ) : (
        <p className="rounded-2xl bg-secondary p-5 text-sm">
          No corrections recorded. Juno does not invent mistakes to fill this
          space.
        </p>
      )}
      <SectionLabel>Vocabulary to keep</SectionLabel>
      <div className="rounded-[21px] bg-secondary px-5">
        {r.vocabulary_learned.length ? (
          r.vocabulary_learned.map((v) => (
            <div
              key={v.term}
              className="border-b border-border py-4 last:border-0"
            >
              <p className="font-semibold">
                {v.term}
                <button
                  onClick={() => say(v.term)}
                  aria-label={`Listen to ${v.term}`}
                  className="ml-3 rounded-full p-2 hover:bg-muted"
                >
                  <Volume2 size={14} />
                </button>
              </p>
              <p className="mt-1 text-xs text-muted-foreground">{v.meaning}</p>
            </div>
          ))
        ) : (
          <p className="py-5 text-sm">No vocabulary recorded in this class.</p>
        )}
      </div>
      <SectionLabel>Pronunciation practice · no scores</SectionLabel>
      {r.pronunciation.length ? (
        r.pronunciation.map((p) => (
          <div
            key={p.word}
            className="mb-3 rounded-[21px] border border-border bg-card p-5"
          >
            <button
              onClick={() => say(p.word)}
              className="float-right rounded-full bg-secondary p-3"
              aria-label={`Listen to ${p.word}`}
            >
              <Play size={16} />
            </button>
            <p className="font-semibold">
              {p.word}{" "}
              <span className="font-normal text-muted-foreground">{p.ipa}</span>
            </p>
            <p className="mt-2 text-xs text-muted-foreground">
              Practice cue: {p.watch_for}
            </p>
          </div>
        ))
      ) : (
        <p className="text-sm text-muted-foreground">
          No pronunciation practice cues in this report. Juno does not measure
          pronunciation from text.
        </p>
      )}
      <SectionLabel>What went well</SectionLabel>
      {r.what_went_well.map((x) => (
        <p key={x} className="mb-2 text-sm">
          ✓ {x}
        </p>
      ))}
      {r.word_traps.length > 0 && (
        <>
          <SectionLabel>Word traps</SectionLabel>
          {r.word_traps.map((w, i) => (
            <p key={i} className="mb-2 text-sm">
              {w.you_said} → {w.we_say} · {w.problem}
            </p>
          ))}
        </>
      )}
      {r.grammar_point_taught && (
        <>
          <SectionLabel>Grammar covered</SectionLabel>
          <p>{r.grammar_point_taught}</p>
        </>
      )}
      <SectionLabel>Homework</SectionLabel>
      {r.homework.length ? (
        r.homework.map((x) => (
          <p className="mb-2 text-sm" key={x}>
            {x}
          </p>
        ))
      ) : (
        <p className="text-sm text-muted-foreground">No homework set.</p>
      )}
      <section className="mt-7 rounded-[22px] bg-foreground p-5 text-background">
        <p className="text-[10px] uppercase tracking-[.17em] text-background/55">
          Your next conversation
        </p>
        <h2 className="mt-3 font-serif text-2xl">
          {r.next_recommendation || "Keep the conversation going."}
        </h2>
        <Link
          href="/practice"
          className="mt-5 flex h-12 items-center justify-center gap-2 rounded-xl bg-primary font-semibold text-primary-foreground"
        >
          Choose your next class <ArrowRight size={16} />
        </Link>
      </section>
    </div>
  );
}
function Progress() {
  const { data } = useJuno();
  const p = data.progress;
  return (
    <div className="fp-page mx-auto max-w-4xl px-5 py-10 sm:px-10 lg:py-16">
      <PageHeader
        eyebrow="Your progress"
        title={
          <>
            Keep the thread.
            <br />
            <i>One class at a time.</i>
          </>
        }
      />
      <section className="rounded-[27px] bg-foreground p-6 text-background sm:p-8">
        <p className="text-[10px] uppercase tracking-[.18em] text-background/55">
          Current level estimate
        </p>
        <h2 className="mt-2 font-serif text-4xl">
          {p.level.ready
            ? p.level.overall?.band + (p.level.overall?.plus ? "+" : "")
            : "Building the picture"}
        </h2>
        <p className="mt-4 text-sm text-background/65">
          {p.level.ready
            ? "Based on saved, sufficiently evidenced class reports."
            : `${p.level.rated_classes} sufficiently evidenced classes · ${p.level.needed || 2} needed before showing an estimate.`}
        </p>
      </section>
      <div className="mt-4 grid gap-4 md:grid-cols-[1.05fr_.95fr]">
        <section className="rounded-[24px] border border-border bg-card p-6">
          <p className="text-[10px] uppercase tracking-[.18em] text-muted-foreground">
            Class minutes this week
          </p>
          <p className="mt-2 font-serif text-4xl">
            {p.minutes_week}
            <span className="text-lg text-muted-foreground"> min</span>
          </p>
          <div className="mt-7 flex h-28 items-end gap-2">
            {p.week.map((d) => (
              <div
                key={d.date}
                className="flex flex-1 flex-col items-center gap-2"
              >
                <span className="text-[10px] text-muted-foreground">
                  {d.minutes}
                </span>
                <div
                  className="w-full rounded-t-lg bg-primary/70"
                  style={{
                    height: Math.max(
                      3,
                      (d.minutes /
                        Math.max(1, ...p.week.map((x) => x.minutes))) *
                        65,
                    ),
                  }}
                />
                <span className="text-[10px] text-muted-foreground">
                  {d.weekday}
                </span>
              </div>
            ))}
          </div>
        </section>
        <section className="rounded-[24px] border border-border bg-card p-6">
          <p className="text-[10px] uppercase tracking-[.18em] text-muted-foreground">
            A steady rhythm
          </p>
          <p className="mt-3 font-serif text-3xl">
            {p.classes_total} finished classes
          </p>
          <p className="mt-3 text-sm text-muted-foreground">
            {p.exchanges_total} exchanges · {p.minutes_total} total class
            minutes
          </p>
          <p className="mt-3 text-sm text-muted-foreground">
            {p.streak_days} day practice streak · {p.words_learned} vocabulary
            items
          </p>
        </section>
      </div>
      <SectionLabel>Skill estimates</SectionLabel>
      <section className="rounded-[24px] border border-border bg-card p-6">
        {p.level.skills?.map((s) => (
          <div className="mb-5 flex justify-between last:mb-0" key={s.key}>
            <span>{s.label}</span>
            <span className="text-primary">
              {s.band}
              {s.plus ? "+" : ""}
            </span>
          </div>
        )) || (
          <p className="text-sm text-muted-foreground">
            Fluency, grammar, vocabulary and understanding will appear when
            enough evidence is available. No pronunciation score is inferred.
          </p>
        )}
      </section>
      <SectionLabel>Next focus</SectionLabel>
      <p className="text-sm text-muted-foreground">
        {p.next_recommendation ||
          "Your next focus will come from a completed class report."}
      </p>
    </div>
  );
}
function Drill() {
  const { data } = useJuno();
  const words = data.notes.flatMap((n) => n.report.vocabulary_learned);
  return (
    <div className="fp-page mx-auto max-w-3xl px-5 py-10 sm:px-10 lg:py-16">
      <Link
        href="/"
        className="mb-10 inline-flex items-center gap-2 text-sm text-muted-foreground"
      >
        <ArrowLeft size={15} />
        Today
      </Link>
      <PageHeader
        eyebrow="Vocabulary practice"
        title={
          <>
            Hear it again.
            <br />
            <i>Make it yours.</i>
          </>
        }
      />
      <p className="mb-7 text-sm text-muted-foreground">
        Review vocabulary from your saved Juno reports. This is practice, not a
        listening assessment.
      </p>
      {words.length ? (
        words.map((w, i) => (
          <div
            key={i}
            className="mb-3 flex items-center gap-4 rounded-[22px] border border-border bg-card p-5"
          >
            <button
              onClick={() => say(w.term)}
              className="rounded-full bg-secondary p-4"
              aria-label={`Listen to ${w.term}`}
            >
              <Play size={18} />
            </button>
            <div>
              <h2 className="font-semibold">{w.term}</h2>
              <p className="mt-1 text-sm text-muted-foreground">{w.meaning}</p>
            </div>
          </div>
        ))
      ) : (
        <p className="rounded-2xl bg-secondary p-5 text-sm">
          Complete a class to collect words for practice.
        </p>
      )}
      <Link
        href="/practice"
        className="mt-7 flex h-12 items-center justify-center rounded-xl bg-primary font-semibold text-primary-foreground"
      >
        On to the conversation
      </Link>
    </div>
  );
}
function NotFound() {
  return (
    <div className="p-12 text-center">
      <h1 className="font-serif text-4xl">A quiet dead end.</h1>
      <Link href="/" className="mt-6 inline-block text-primary">
        Back to today
      </Link>
    </div>
  );
}
function Router() {
  const [location] = useLocation();
  return (
    <Shell>
      <ErrorBoundary resetKey={location}>
        <Switch>
          <Route path="/" component={Home} />
          <Route path="/practice" component={Practice} />
          <Route path="/session" component={Session} />
          <Route path="/notes" component={Notes} />
          <Route path="/progress" component={Progress} />
          <Route path="/drill" component={Drill} />
          <Route component={NotFound} />
        </Switch>
      </ErrorBoundary>
    </Shell>
  );
}
function App() {
  const [data, setData] = useState<State | null>(null);
  const [error, setError] = useState("");
  const refresh = async () => {
    setData(await loadState());
  };
  useEffect(() => {
    refresh().catch((e) => setError(e.message));
  }, []);
  return (
    <>
      <div className="juno-preview" role="note">
        Fictional preview · Alex Test · AI responses are mocked · no paid
        services
      </div>
      {error ? (
        <main className="mx-auto max-w-lg p-10">
          <h1 className="font-serif text-3xl">
            Your practice space is resting.
          </h1>
          <p role="alert" className="mt-4">
            {error}
          </p>
          <button
            onClick={() => {
              setError("");
              refresh().catch((e) => setError(e.message));
            }}
            className="mt-6 rounded-xl bg-primary px-5 py-3 text-primary-foreground"
          >
            Try again
          </button>
        </main>
      ) : data ? (
        <Data.Provider value={{ data, refresh }}>
          <WouterRouter>
            <Router />
          </WouterRouter>
        </Data.Provider>
      ) : (
        <p role="status" className="p-12 text-center font-serif text-2xl">
          Opening your Juno notebook…
        </p>
      )}
    </>
  );
}
export default App;
