import type { Route } from "./+types/ai-email-responder._index";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { Button } from "~/components/ui/button";
import { Textarea } from "~/components/ui/textarea";
import { ArrowUpRight, Check, Copy, Mail, RotateCcw, Sparkles } from "lucide-react";

export function meta({}: Route.MetaArgs) {
  return [{ title: "Email drafting studio | Emilio Esposito" }, { name: "description", content: "Explore how grounded AI instructions turn rental inquiries into useful, reviewable replies." }];
}
interface Scenario { id: string; title: string; focus: string; subject: string; sender: string; body: string; facts: string }
interface Catalog { scenarios: Scenario[]; default_instructions: string }

export default function AIEmailResponderPage() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [selectedId, setSelectedId] = useState("tour");
  const [instructions, setInstructions] = useState("");
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [unavailable, setUnavailable] = useState<"budget" | "rate" | null>(null);
  const [copied, setCopied] = useState(false);
  const [retry, setRetry] = useState(0);
  const controller = useRef<AbortController | null>(null);
  const selected = catalog?.scenarios.find((item) => item.id === selectedId);

  useEffect(() => {
    const abort = new AbortController();
    setError("");
    fetch("/api/google/gmail/get_zillow_emails", { signal: abort.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("The scenarios could not load. Try again.");
        const data: Catalog = await response.json();
        setCatalog(data); setInstructions(data.default_instructions); setSelectedId(data.scenarios[0].id);
      }).catch((cause: Error) => { if (cause.name !== "AbortError") setError("The scenarios could not load. Try again."); });
    return () => { abort.abort(); controller.current?.abort(); };
  }, [retry]);

  useEffect(() => {
    if (unavailable !== "rate") return;
    const timer = window.setTimeout(() => setUnavailable(null), 60000);
    return () => window.clearTimeout(timer);
  }, [unavailable]);

  function clearDraft() { setDraft(""); setError(""); setCopied(false); }
  async function generate() {
    if (!selected || loading || unavailable) return;
    controller.current?.abort();
    const abort = new AbortController(); controller.current = abort;
    setLoading(true); setError(""); setCopied(false);
    const timeout = window.setTimeout(() => abort.abort(), 45000);
    try {
      const response = await fetch("/api/google/gmail/generate_email_response", {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: abort.signal,
        body: JSON.stringify({ scenario_id: selected.id, system_instruction: instructions.trim() }),
      });
      if (!response.ok) {
        if (response.status === 402) { setUnavailable("budget"); return; }
        if (response.status === 429) { setUnavailable("rate"); return; }
        throw new Error("Drafting is temporarily unavailable. Please try again shortly.");
      }
      const data = await response.json(); setDraft(data.response);
    } catch (cause) {
      setError(cause instanceof Error && cause.name !== "AbortError" ? cause.message : "Drafting timed out. Please try again.");
    } finally { window.clearTimeout(timeout); setLoading(false); }
  }
  async function copyDraft() {
    try { await navigator.clipboard.writeText(draft); setCopied(true); }
    catch { setError("Copy is unavailable in this browser. Select the draft text to copy it."); }
  }

  return <main className="mx-auto max-w-7xl px-4 py-8 sm:px-8 sm:py-12">
    <header className="mb-9 flex flex-wrap items-start justify-between gap-5">
      <div className="max-w-2xl"><h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Email drafting studio</h1>
        <p className="mt-3 text-base leading-relaxed text-muted-foreground">A good reply needs more than a good prompt. Give the assistant clear facts, set its boundaries, and see what it writes.</p></div>
      <Link to="/ai-email-responder/architecture" className="flex items-center gap-1 py-2 text-sm text-muted-foreground underline-offset-4 hover:underline">How it works <ArrowUpRight className="size-4" /></Link>
    </header>
    {!catalog ? <div role="status" className="rounded-xl border p-8">{error || "Loading fictional scenarios…"}{error && <Button onClick={() => setRetry(retry + 1)} variant="outline" className="ml-4">Try again</Button>}</div> : <>
      <div className="mb-6 flex items-center gap-2 text-sm text-muted-foreground"><Mail className="size-4" /><span>Fictional messages. Drafts only. Nothing is sent.</span></div>
      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-7">
          <section aria-labelledby="scenario-title">
            <h2 id="scenario-title" className="mb-3 text-lg font-semibold">1. Choose an inquiry</h2>
            <div className="mb-4 flex flex-wrap gap-2" aria-label="Fictional inquiries">{catalog.scenarios.map((item) => <button key={item.id} type="button" aria-pressed={selectedId === item.id} disabled={loading} onClick={() => { setSelectedId(item.id); clearDraft(); }} className={`rounded-full border px-4 py-2 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 ${selectedId === item.id ? "border-primary bg-primary text-primary-foreground" : "hover:bg-muted"}`}>{item.title}</button>)}</div>
            {selected && <div className="overflow-hidden rounded-xl border">
              <div className="border-b bg-muted/30 px-5 py-4"><h3 className="font-medium">{selected.subject}</h3><p className="mt-1 break-words text-sm text-muted-foreground">{selected.sender}</p></div>
              <p className="px-5 py-5 text-sm leading-7">{selected.body}</p>
              <details className="border-t bg-muted/20 px-5 py-3" open><summary className="cursor-pointer text-sm font-medium">Facts available to the assistant</summary><p className="mt-3 text-sm leading-6 text-muted-foreground">{selected.facts}</p></details>
            </div>}
          </section>
          <section aria-labelledby="instructions-title">
            <div className="mb-3 flex items-center justify-between gap-3"><h2 id="instructions-title" className="text-lg font-semibold">2. Shape the reply</h2><Button variant="ghost" size="sm" disabled={loading || instructions === catalog.default_instructions} onClick={() => { setInstructions(catalog.default_instructions); clearDraft(); }}><RotateCcw className="mr-1.5 size-3.5" />Reset</Button></div>
            <label htmlFor="draft-instructions" className="mb-2 block text-sm text-muted-foreground">Edit the instructions directly. Your next draft uses these writing preferences.</label>
            <Textarea id="draft-instructions" value={instructions} disabled={loading} maxLength={2000} onChange={(event) => { setInstructions(event.target.value); clearDraft(); }} className="min-h-[250px] text-sm leading-6" />
            <div className="mt-2 flex justify-between gap-4 text-xs text-muted-foreground"><span>Try a shorter reply or a more conversational tone.</span><span className="shrink-0">{instructions.length}/2,000</span></div>
          </section>
        </div>
        <section aria-labelledby="draft-title" className="min-w-0 rounded-2xl border bg-muted/20 p-5 sm:p-7 lg:sticky lg:top-6 lg:self-start">
          <div className="flex flex-wrap items-center justify-between gap-3"><h2 id="draft-title" className="text-lg font-semibold">3. Review the draft</h2><Button disabled={loading || !!unavailable || !instructions.trim()} onClick={generate}><Sparkles className={`mr-2 size-4 ${loading ? "motion-safe:animate-pulse" : ""}`} />{loading ? "Drafting…" : draft ? "Draft again" : "Generate draft"}</Button></div>
          <p className="mt-3 text-sm text-muted-foreground">{selected?.focus}. Review every reply before using it.</p>
          <div aria-live="polite" aria-busy={loading} className="mt-6 min-h-[280px] rounded-lg border bg-background p-5 sm:min-h-[350px]">
            {draft ? <p className="whitespace-pre-wrap break-words text-sm leading-7">{draft}</p> : <div className="flex min-h-[230px] flex-col justify-center gap-3 text-muted-foreground"><Mail className="size-7" aria-hidden="true" /><p className="text-lg text-foreground">{loading ? "Turning the inquiry into a useful reply…" : "Your next reply starts here."}</p><p className="max-w-sm text-sm leading-6">{loading ? "The assistant is working with the selected scenario and your instructions." : "Choose an inquiry, adjust the instructions, then generate a draft. Try the risky request to explore the assistant’s boundaries."}</p></div>}
          </div>
          {unavailable && <p role="status" className="mt-4 text-sm text-muted-foreground">{unavailable === "budget" ? "The demo budget is temporarily unavailable. Your draft and writing preferences are still here. Please come back later." : "The demo is temporarily at capacity. Your work is preserved. You can try again in a minute."}</p>}
          {error && <p role="alert" className="mt-4 text-sm text-destructive">{error}</p>}
          <div className="mt-4 flex items-center justify-between gap-3"><span className="text-xs text-muted-foreground">No inbox access or sending permissions.</span>{draft && <Button variant="outline" size="sm" onClick={copyDraft}>{copied ? <Check className="mr-2 size-4" /> : <Copy className="mr-2 size-4" />}{copied ? "Copied" : "Copy draft"}</Button>}</div>
        </section>
      </div>
    </>}
  </main>;
}
