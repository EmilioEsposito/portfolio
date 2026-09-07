import type { Route } from "./+types/ai-email-responder.architecture";
import { Link } from "react-router";
import Mermaid from "~/components/ui/mermaid";

export function meta({}: Route.MetaArgs) {
  return [{ title: "How the email studio works | Emilio Esposito" }];
}
export default function AIEmailResponderArchitecturePage() {
  return <main className="mx-auto max-w-4xl space-y-7 px-4 py-10 sm:px-8">
    <Link to="/ai-email-responder" className="text-sm underline underline-offset-4">Back to the drafting studio</Link>
    <h1 className="text-3xl font-semibold tracking-tight">Grounded drafting, with clear boundaries</h1>
    <p className="max-w-2xl leading-7 text-muted-foreground">The studio distills a real property-management workflow into a safe, fictional exercise. The assistant gets a short inquiry, known property facts, and editable writing preferences. It produces a draft for human review.</p>
    <Mermaid chart={`flowchart TD
      scenario[Fictional scenario] --> validate[Validate request and usage limits]
      preferences[Writing preferences] --> validate
      validate --> facts[Server-owned property facts and safety rules]
      facts --> model[OpenRouter model]
      model --> draft[Plain-text draft]
      draft --> review[Human review]
    `} />
    <div className="space-y-5 text-sm leading-7">
      <section><h2 className="text-lg font-semibold">Facts before fluency</h2><p className="text-muted-foreground">The server selects the scenario by ID. Visitors cannot substitute a private email or request arbitrary mailbox contents. Missing prices, tour availability, and approval decisions must remain unconfirmed.</p></section>
      <section><h2 className="text-lg font-semibold">Preferences within boundaries</h2><p className="text-muted-foreground">The editable instructions control tone and format. Separate system rules constrain the assistant to the fictional task. Email text is treated as untrusted content, including requests to ignore instructions.</p></section>
      <section><h2 className="text-lg font-semibold">Limited authority and cost</h2><p className="text-muted-foreground">The demo has no tools, inbox access, or ability to send a reply. Requests have input and output limits, a timeout, and shared usage controls. These limits reduce exposure even if a prompt produces an unexpected answer.</p></section>
      <section><h2 className="text-lg font-semibold">Inspired by production operations</h2><p className="text-muted-foreground">The writing defaults reflect the practical habits of SerniaAI: answer directly, use verified facts, avoid unsupported commitments, and make the next step clear. This public studio uses wholly fictional people and properties.</p></section>
    </div>
  </main>;
}
