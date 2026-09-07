import { useEffect, useRef, useState } from "react";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import { ArrowUp, Check, CloudSun, GitBranch, Square, UserRound } from "lucide-react";
import { Button } from "~/components/ui/button";
import { Textarea } from "~/components/ui/textarea";
import { Markdown } from "~/components/markdown";
import { cn } from "~/lib/utils";
import { publicChatHistory } from "~/lib/public-chat-history";

export function meta() {
  return [{ title: "Agent showcase | Emilio Esposito" }, { name: "description", content: "See an AI workflow make a routing decision, use a specialist, and stream its answer in real time." }];
}

type NodeName = "router" | "emilio" | "weather";
type NodeStatus = "active" | "complete";
interface Activity { node: NodeName; status: NodeStatus }
const nodes = [
  { id: "router" as const, title: "Router", detail: "Chooses the right specialist", icon: GitBranch },
  { id: "emilio" as const, title: "Portfolio agent", detail: "Experience, projects & approach", icon: UserRound },
  { id: "weather" as const, title: "Weather agent", detail: "Live data from Open-Meteo", icon: CloudSun },
];
const prompts = ["How does Emilio turn an AI prototype into a reliable product?", "What’s the weather in Pittsburgh right now?"];

function toolLabel(type: string, complete: boolean): string {
  const labels: Record<string, [string, string]> = {
    "tool-get_current_weather": ["Looking up live weather data…", "Weather data received from Open-Meteo"],
    "tool-fetch_resume": ["Reading Emilio’s resume…", "Resume context retrieved"],
    "tool-fetch_portfolio_website": ["Reading portfolio context…", "Portfolio context retrieved"],
    "tool-fetch_interview_ai_launch": ["Reading AI launch experience…", "AI launch context retrieved"],
    "tool-get_emilio_links": ["Finding relevant links…", "Portfolio links retrieved"],
  };
  return (labels[type] ?? ["Running a tool…", "Tool result received"])[complete ? 1 : 0];
}

function publicErrorText(message: string): string {
  if (message === "Public demo budget is exhausted. Please try again later.") return message;
  if (message.includes("usage limit")) return "The public demo has reached its usage limit. Please try again later.";
  if (message.includes("temporarily unavailable")) return "Public AI is temporarily unavailable. Please try again later.";
  return "The run could not finish. Please try again shortly.";
}

export default function MultiAgentChatPage() {
  const [input, setInput] = useState("");
  const [activity, setActivity] = useState<Activity[]>([]);
  const [cancelled, setCancelled] = useState(false);
  const submitting = useRef(false);
  const lastSubmitted = useRef("");
  const transcript = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const { messages, sendMessage, status, stop, error, setMessages, clearError } = useChat({
    transport: new DefaultChatTransport({
      api: "/api/ai-demos/multi-agent-chat",
      prepareSendMessagesRequest: ({ messages, id, trigger, body }) => ({
        body: { ...body, id, trigger, messages: publicChatHistory(messages) },
      }),
    }),
    onData: (part) => {
      if (part.type !== "data-agent-activity") return;
      const data = part.data as Activity;
      if (nodes.some((node) => node.id === data?.node) && ["active", "complete"].includes(data.status)) {
        setActivity((previous) => [...previous, data]);
      }
    },
    onFinish: () => { submitting.current = false; },
    onError: () => { submitting.current = false; setInput(lastSubmitted.current); },
  });
  useEffect(() => {
    if (following.current && transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight;
  }, [messages]);
  const busy = status === "submitted" || status === "streaming";
  const selected = activity.find((event) => event.node !== "router")?.node;
  const finished = activity.some((event) => event.node !== "router" && event.status === "complete");
  const nodeState = (id: NodeName) => activity.filter((event) => event.node === id).at(-1)?.status;
  const submit = (text: string) => {
    if (!text.trim() || busy || submitting.current) return;
    lastSubmitted.current = text.trim();
    submitting.current = true;
    following.current = true;
    clearError();
    setActivity([]);
    setCancelled(false);
    setInput("");
    void sendMessage({ text: text.trim() });
  };
  const runStatus = error ? "Run interrupted" : cancelled ? "Run stopped" : finished ? "Run complete" : busy ? activity.length ? selected ? `${selected === "emilio" ? "Portfolio" : "Weather"} agent is working` : "Router is choosing a specialist" : "Connecting to the workflow" : "Ready to run";

  return (
    <main className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-8 lg:py-12">
      <header className="mb-8 max-w-3xl">
        <h1 className="text-3xl font-semibold tracking-tight sm:text-5xl">Watch the handoff.</h1>
        <p className="mt-4 max-w-2xl text-base leading-relaxed text-muted-foreground">One conversation, two specialists. Ask about my work or the weather, and watch the router choose an agent before its answer streams in.</p>
      </header>
      <div className="grid items-start gap-6 lg:grid-cols-[minmax(280px,0.8fr)_minmax(0,1.2fr)] lg:gap-10">
        <section aria-label="Live agent workflow" className="rounded-2xl border bg-muted/20 p-5 sm:p-6 lg:sticky lg:top-6">
          <div className="mb-7 flex items-center justify-between gap-3">
            <h2 className="text-lg font-medium">The workflow</h2>
            <span className="flex items-center gap-2 text-xs text-muted-foreground"><span className={cn("size-2 rounded-full", busy ? "bg-blue-500 motion-safe:animate-pulse" : "bg-muted-foreground/40")} />{busy ? "Live" : "Execution view"}</span>
          </div>
          <div className="mx-auto max-w-sm">
            {nodes.map((node, index) => {
              const state = nodeState(node.id);
              const active = state === "active" && busy;
              return (
                <div key={node.id} className={cn(index > 0 && "ml-6 border-l-2 pl-6", index > 0 && selected === node.id ? "border-blue-500" : "border-border")}>
                  {index > 0 && <div aria-hidden="true" className="h-5" />}
                  <div className={cn("relative flex items-center gap-3 rounded-xl border p-4 motion-safe:transition-colors motion-safe:duration-300", active ? "border-blue-500 bg-blue-50 ring-2 ring-blue-500/20 dark:bg-blue-950/40" : state === "complete" ? "border-blue-300 bg-background dark:border-blue-800" : "border-border bg-background/70")}>
                    {index > 0 && <span aria-hidden="true" className={cn("absolute -left-6 top-1/2 h-0.5 w-6", selected === node.id ? "bg-blue-500" : "bg-border")} />}
                    <node.icon aria-hidden="true" className={cn("size-5 shrink-0", active || state === "complete" ? "text-blue-600 dark:text-blue-400" : "text-muted-foreground")} />
                    <div className="min-w-0 flex-1"><h3 className="text-sm font-medium">{node.title}</h3><p className="mt-1 text-xs text-muted-foreground">{node.detail}</p></div>
                    {state === "complete" && <Check aria-label="Complete" className="size-4 text-blue-600 dark:text-blue-400" />}
                    {active && <span className="size-2 rounded-full bg-blue-500 motion-safe:animate-pulse"><span className="sr-only">Active</span></span>}
                  </div>
                </div>
              );
            })}
          </div>
          <p role="status" aria-live="polite" className="mt-7 text-sm font-medium">{runStatus}</p>
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">Highlights follow events from the running graph. Only the selected specialist runs; weather requests can call a live forecast tool.</p>
          <details className="mt-6 border-t pt-4 text-sm">
            <summary className="cursor-pointer text-muted-foreground focus-visible:outline focus-visible:outline-2">How it works</summary>
            <p className="mt-3 text-xs leading-relaxed text-muted-foreground">A PydanticAI graph routes each message, then streams the specialist’s response. Portfolio answers use curated career context. Weather answers use Open-Meteo. This public demo has bounded inputs, tool calls and inference time. AI answers can be mistaken.</p>
          </details>
        </section>
        <section aria-label="Conversation" className="min-w-0">
          <div className="mb-4 flex items-center justify-between gap-4"><h2 className="text-lg font-medium">Try a handoff</h2>{messages.length > 0 && <Button variant="ghost" size="sm" disabled={busy} onClick={() => { setMessages([]); setActivity([]); clearError(); setCancelled(false); }}>New conversation</Button>}</div>
          {messages.length === 0 && <div className="mb-6 space-y-3"><p className="text-sm text-muted-foreground">Start with a question, then try the other specialist.</p>{prompts.map((prompt) => <button key={prompt} type="button" disabled={busy} onClick={() => submit(prompt)} className="block w-full rounded-xl border px-4 py-4 text-left text-sm leading-relaxed hover:border-blue-400 hover:bg-muted/50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-500 disabled:opacity-50">{prompt}</button>)}</div>}
          <div ref={transcript} onScroll={() => { const el = transcript.current; if (el) following.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }} aria-label="Messages" className="max-h-[55dvh] space-y-6 overflow-y-auto [overflow-wrap:anywhere]">
            {messages.map((message) => <article key={message.id} className={cn("rounded-xl px-4 py-4", message.role === "user" ? "ml-6 bg-muted" : "border bg-background")}><p className="mb-2 text-xs font-medium text-muted-foreground">{message.role === "user" ? "You" : "Agent"}</p>{message.parts.map((part, i) => part.type === "text" ? <div key={i} className="prose prose-sm max-w-none dark:prose-invert"><Markdown>{part.text}</Markdown></div> : part.type.startsWith("tool-") ? <p key={i} className="mb-2 rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">{toolLabel(part.type, "state" in part && part.state === "output-available")}</p> : null)}</article>)}
          </div>
          {error && <p role="alert" className="mt-4 rounded-lg border border-destructive/40 p-3 text-sm text-destructive">{publicErrorText(error.message)}</p>}
          <form className="mt-6 rounded-xl border bg-background p-3 focus-within:ring-2 focus-within:ring-blue-500/40" onSubmit={(event) => { event.preventDefault(); submit(input); }}>
            <label htmlFor="showcase-message" className="sr-only">Your question</label>
            <Textarea id="showcase-message" value={input} maxLength={2000} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); submit(input); } }} placeholder="Ask about Emilio’s work or the weather…" rows={3} className="resize-none border-0 bg-transparent shadow-none focus-visible:ring-0" disabled={busy} />
            <div className="mt-2 flex items-center justify-between gap-3"><span className="text-xs text-muted-foreground">{input.length}/2,000</span>{busy ? <Button type="button" variant="outline" onClick={() => { stop(); submitting.current = false; setCancelled(true); }}><Square className="mr-2 size-3" />Stop</Button> : <Button type="submit" disabled={!input.trim()}><ArrowUp className="mr-2 size-4" />Send</Button>}</div>
          </form>
          <p className="mt-3 text-xs text-muted-foreground">Public demo. Recent text provides conversation context. Leave out private or sensitive information.</p>
        </section>
      </div>
    </main>
  );
}
