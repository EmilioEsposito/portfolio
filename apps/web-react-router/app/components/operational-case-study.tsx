import { Link } from "react-router";
import { ArrowDown, ArrowRight, ExternalLink } from "lucide-react";
import { buildUrl, generateCanonicalLink, generatePageMeta } from "~/lib/seo";

interface WorkflowStep { label: string; title: string; description: string }
interface CaseStudy {
  path: string;
  title: string;
  description: string;
  approach: string;
  problem: string;
  steps: WorkflowStep[];
  judgment: string;
  controls: string;
  outcome: string;
  boundary: string;
  source: string;
}
export function caseStudyMeta(study: CaseStudy) {
  return generatePageMeta({ title: study.title, description: study.description, path: study.path });
}
export function caseStudyLinks(study: CaseStudy) { return [generateCanonicalLink(buildUrl(study.path))]; }

export default function OperationalCaseStudy({ study }: { study: CaseStudy }) {
  return <main className="mx-auto max-w-5xl px-4 py-9 sm:px-8 sm:py-14">
    <Link to="/#sernia-systems" className="text-sm text-muted-foreground underline underline-offset-4">Back to Sernia systems</Link>
    <header className="mt-8 max-w-3xl">
      <p className="mb-3 text-sm text-muted-foreground">Production AI agent · {study.approach}</p>
      <h1 className="text-3xl font-semibold tracking-tight sm:text-5xl">{study.title}</h1>
      <p className="mt-5 text-lg leading-8 text-muted-foreground">{study.description}</p>
    </header>
    <section className="my-9 max-w-3xl" aria-labelledby="problem"><h2 id="problem" className="text-lg font-semibold">The operational problem</h2><p className="mt-3 leading-7 text-muted-foreground">{study.problem}</p></section>
    <section aria-labelledby="workflow" className="rounded-2xl border bg-muted/20 p-5 sm:p-7">
      <h2 id="workflow" className="text-xl font-semibold">How a run works</h2>
      <ol className="mt-6 grid gap-5 md:grid-cols-4">{study.steps.map((step, index) => <li key={step.label} className="relative min-w-0">
        <div className="mb-3 flex items-center gap-2 text-sm text-muted-foreground"><span className="flex size-7 items-center justify-center rounded-full border bg-background">{index + 1}</span><span>{step.label}</span>{index < study.steps.length - 1 && <ArrowRight aria-hidden="true" className="ml-auto hidden size-4 md:block" />}</div>
        <h3 className="font-semibold">{step.title}</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">{step.description}</p>{index < study.steps.length - 1 && <ArrowDown aria-hidden="true" className="mt-4 size-4 text-muted-foreground md:hidden" />}
      </li>)}</ol>
    </section>
    <div className="my-9 grid gap-8 md:grid-cols-2"><section><h2 className="text-lg font-semibold">Where AI decides</h2><p className="mt-3 leading-7 text-muted-foreground">{study.judgment}</p></section><section><h2 className="text-lg font-semibold">What the code controls</h2><p className="mt-3 leading-7 text-muted-foreground">{study.controls}</p></section></div>
    <section className="max-w-3xl border-t pt-7"><h2 className="text-lg font-semibold">The practical outcome</h2><p className="mt-3 leading-7 text-muted-foreground">{study.outcome}</p><p className="mt-4 text-sm leading-6 text-muted-foreground">{study.boundary}</p></section>
    <footer className="mt-8 flex flex-wrap items-center gap-6"><a href={study.source} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-2 text-sm underline underline-offset-4">Read the implementation <ExternalLink className="size-4" /></a><Link to="/multi-agent-chat" className="text-sm underline underline-offset-4">Explore the public agent demo</Link></footer>
  </main>;
}

export const operationsStudy: CaseStudy = {
  path: "/systems/sernia-ai", title: "SerniaAI operations agent", approach: "Flexible tool use",
  description: "How SerniaAI connects scheduled runs, SMS, and web chat to shared company memory, sandboxed computation, business tools, and human approval for external actions.",
  problem: "Property operations arrive as fragments: a text from a tenant, an email thread, a pending task, or a reminder. The team needs a way to connect those details and act without reconstructing the history every time.",
  steps: [
    { label: "Trigger", title: "Chat, event, or schedule", description: "A team member asks a question, a supported event arrives, or a scheduled check starts a run." },
    { label: "AI judgment", title: "Gather context and plan", description: "The agent reads relevant history and business knowledge, then selects tools for the task." },
    { label: "Controlled actions", title: "Execute within permissions", description: "Application tools validate operations. Actions that require approval pause for human review." },
    { label: "Outcome", title: "Reply, act, or wait", description: "The team gets a response or follow-up. The conversation records context for the next run." },
  ],
  judgment: "The agent determines which information matters and which tools to call. It can search communications, consult its workspace knowledge, manage tasks, and coordinate follow-ups rather than following one fixed sequence for every request.",
  controls: "Authentication and internal-contact checks govern entry points. Tool implementations enforce permissions and approval rules. Conversation persistence and scheduled jobs live in application code, outside the model’s reasoning.",
  outcome: "The same operational context can carry from a web conversation to an internal SMS thread or a later follow-up. The assistant can also stop without taking action when a trigger does not need a response.",
  boundary: "This is the private operational system. The public agent demo uses a separate, restricted workflow and does not expose tenant records or business tools.",
  source: "https://github.com/EmilioEsposito/portfolio/tree/main/api/src/sernia_ai",
};

export const emergencyStudy: CaseStudy = {
  path: "/systems/emergency-routing", title: "Emergency SMS routing agent", approach: "Event-driven specialist",
  description: "A specialized AI agent evaluates incoming messages for urgent property issues. A deterministic workflow routes qualifying incidents to the team through Twilio.",
  problem: "An urgent tenant message can arrive alongside routine questions and text reactions. Routing every message as an emergency creates alert fatigue; relying on keywords alone misses the meaning and timing of the request.",
  steps: [
    { label: "Trigger", title: "Incoming team SMS", description: "The webhook checks the event type and destination, and filters reaction messages and notification loops." },
    { label: "AI judgment", title: "Assess urgency", description: "The model evaluates the message and timestamp against escalation instructions, returning a decision and reason." },
    { label: "Controlled actions", title: "Route qualifying alerts", description: "Code checks the decision, resolves configured recipients, and starts Twilio Studio executions." },
    { label: "Outcome", title: "Bring in the team", description: "The configured flow places escalation calls. An incident ID connects the notifications." },
  ],
  judgment: "The model performs a narrow classification task: should this message escalate, and why? It returns structured fields rather than choosing arbitrary tools or contact numbers.",
  controls: "Webhook conditions decide when assessment runs. A boolean decision gates the Twilio workflow, and recipient configuration stays in application code. A failed call to one recipient does not prevent attempts to reach the others.",
  outcome: "Qualifying messages reach the escalation channel automatically, including the configured phone flow for off-hours attention. The human team handles the incident; the agent does not diagnose or resolve the underlying emergency.",
  boundary: "If AI assessment fails after its bounded retry, the current implementation does not escalate automatically and logs the failure. This routing aid is not an emergency service or a guarantee that every urgent message will be detected.",
  source: "https://github.com/EmilioEsposito/portfolio/blob/main/api/src/open_phone/escalate.py",
};

export const leasingStudy: CaseStudy = {
  path: "/systems/leasing-leads", title: "Leasing lead management agent", approach: "Event-driven specialist",
  description: "A specialized AI agent reviews leasing email threads on a schedule, identifies missing replies and confirmed tours, and feeds a deterministic contact and calendar workflow.",
  problem: "A leasing thread can mix proposed times, confirmed appointments, contact details, and unanswered questions. Turning that conversation into reliable follow-up takes more than copying the latest email into a calendar.",
  steps: [
    { label: "Trigger", title: "Scheduled inbox review", description: "Registered scheduler jobs select leasing threads and check for inquiries needing attention." },
    { label: "AI judgment", title: "Interpret the thread", description: "The model assesses whether a reply is needed and a tour is confirmed, then extracts structured details." },
    { label: "Controlled actions", title: "Update operational tools", description: "Code alerts the team, upserts the lead contact, and creates a calendar event when required details are present." },
    { label: "Outcome", title: "A usable follow-up", description: "The team gets missing-reply alerts and tour details, or a notification about information that could not be processed." },
  ],
  judgment: "The model distinguishes a proposed tour from a confirmed appointment and extracts the lead, property, and appointment information from the thread. Its structured output supplies data to the workflow; it does not freely select actions.",
  controls: "Scheduler jobs and database queries determine which threads are reviewed. Branches in code gate alerts, contact updates, and calendar creation. Missing phone numbers or appointment details produce operational notes instead of fabricated records.",
  outcome: "Confirmed tour information can become a contact in the phone platform and an event in the team calendar. Unanswered inquiries and processing issues surface to the team for follow-up.",
  boundary: "This workflow assists the leasing team; it does not approve applicants or autonomously negotiate lease terms. The public email studio is a separate drafting exercise with fictional scenarios.",
  source: "https://github.com/EmilioEsposito/portfolio/tree/main/api/src/zillow_email",
};
