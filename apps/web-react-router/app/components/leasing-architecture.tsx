import { useId, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  CalendarCheck,
  CalendarDays,
  Database,
  Globe,
  Home,
  Mail,
  MailCheck,
  MessageSquare,
  MousePointerClick,
  Settings2,
  Smartphone,
  UserRoundCheck,
  Users,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { cn } from "~/lib/utils";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";

type NodeId =
  | "ad" | "general" | "listings" | "editor"
  | "inquiry" | "inbox" | "agent" | "team" | "reply"
  | "calendars" | "open" | "page" | "email" | "booked"
  | "leadEmail" | "leadDetails" | "host" | "residents";
type Tone = "listing" | "lead" | "booking" | "told";
interface Node {
  id: NodeId;
  title: string;
  subtitle: string;
  detail: string;
  icon: LucideIcon;
  tone: Tone;
  box: [number, number, number, number];
}

const nodes: Node[] = [
  {
    id: "ad", title: "Live Zillow listing", subtitle: "The public rental ad", icon: Globe, tone: "listing", box: [24, 44, 185, 82],
    detail: "The saved Zillow URL lets the general agent fetch the live ad on request, including rent, availability, amenities, and the description. If Zillow cannot be read, the team can supply the ad text.",
  },
  {
    id: "general", title: "General agent", subtitle: "Fetch the ad, update facts", icon: MessageSquare, tone: "listing", box: [264, 44, 200, 82],
    detail: "The general agent reads the live Zillow listing, compares it with the saved record, and updates the rental-listing table through validated tools. Changes to free text and links follow the existing review rules.",
  },
  {
    id: "listings", title: "Rental listing data", subtitle: "Rent, facts, showing rules", icon: Database, tone: "listing", box: [538, 44, 230, 82],
    detail: "One Postgres store holds asking rent, lease dates, unit facts, booking links, and showing rules. Each change records who made it and what changed. The lead agent and the booking page read this data.",
  },
  {
    id: "editor", title: "Team editor & MCP", subtitle: "Maintain the same records", icon: Settings2, tone: "listing", box: [860, 44, 230, 82],
    detail: "The rental-listings page and Sernia MCP tools use the same listing service as the general agent. Staff can maintain facts, set showing preferences, and inspect change history from either interface.",
  },
  {
    id: "inquiry", title: "Rental inquiry", subtitle: "A lead writes on Zillow", icon: Users, tone: "lead", box: [24, 249, 185, 96],
    detail: "A lead asks about an apartment or replies in an existing Zillow email thread. The conversation may contain unit questions, move-in preferences, or showing logistics.",
  },
  {
    id: "inbox", title: "Gmail & lead queue", subtitle: "Collect the conversation", icon: Mail, tone: "lead", box: [264, 249, 200, 96],
    detail: "Gmail notifications enqueue incoming messages. The queue groups messages by thread, handles retries, and loads the whole conversation from the shared mailbox before the lead agent sees it.",
  },
  {
    id: "agent", title: "Zillow Leads Agent", subtitle: "Thread + listing facts + open times", icon: MessageSquare, tone: "lead", box: [538, 239, 230, 116],
    detail: "The specialist answers from the conversation, the listing facts, and the open showing times it is given. It has no tools and sees no private company data, so its replies can be sent without a person reading each one. Each run ends one of three ways: a reply, no reply when none is needed, or a hand-off to the team.",
  },
  {
    id: "team", title: "Hand to the team", subtitle: "Anything it should not answer", icon: Users, tone: "lead", box: [860, 209, 230, 84],
    detail: "A question the listing data does not answer, an unclear request, or anything unusual becomes a task for the team, with what the agent would ask. Nothing goes to the lead until a person decides.",
  },
  {
    id: "reply", title: "Reply to the lead", subtitle: "Approved by the team, or automatic", icon: Mail, tone: "lead", box: [860, 313, 230, 84],
    detail: "A reply answers questions, offers open times with the unit's booking link, or confirms a time the lead agreed to. Code keeps it on the original Zillow thread, so nothing in an email can redirect it. The team chooses in settings whether replies wait for approval or go out automatically, with a separate choice for replies that book, reschedule, or cancel a showing.",
  },
  {
    id: "calendars", title: "Calendars", subtitle: "Each showing agent’s own", icon: CalendarDays, tone: "booking", box: [24, 474, 185, 96],
    detail: "Each showing agent connects a Google calendar. Anything busy on it blocks that agent, and a calendar that cannot be read counts as busy, never free.",
  },
  {
    id: "open", title: "Open times", subtitle: "Free agents + showing rules", icon: CalendarDays, tone: "booking", box: [264, 474, 200, 96],
    detail: "Start times every 30 minutes inside the listing’s showing window, a day or more ahead, when an agent is free and not at another showing. Travel time between buildings is allowed for. A showing that already has people booked and room left is offered first, so leads share a visit.",
  },
  {
    id: "page", title: "Books on the page", subtitle: "Name, mobile, email, consent", icon: MousePointerClick, tone: "booking", box: [538, 474, 230, 96],
    detail: "On the unit’s booking page the lead picks an open time, or joins a showing already booked, and gives a name, mobile number, and email. They agree to texts and confirm the listing’s requirements. If the mobile number cannot be texted, the booking is not made and the page says so.",
  },
  {
    id: "email", title: "Agrees by email", subtitle: "The reply confirms the time", icon: MailCheck, tone: "booking", box: [860, 474, 230, 96],
    detail: "When a lead accepts an offered time in the Zillow thread, the agent’s reply confirms it and the showing is booked as that reply is sent. If the time is no longer open, the reply is held for the team. The lead has not given a mobile number or agreed to texts yet, so the reply carries their own link to add them.",
  },
  {
    id: "booked", title: "Showing booked", subtitle: "One agent hosts it; other leads can join the same showing", icon: CalendarCheck, tone: "booking", box: [538, 644, 552, 84],
    detail: "Each showing has one host, chosen from the agents who are free, favouring one already at that building and one who prefers that time. Several leads can share a showing. The team can also book someone who phoned. A booked time leaves the open times, and the lead agent’s next reply knows about it.",
  },
  {
    id: "leadEmail", title: "Lead booked by email", subtitle: "Email only, until they add details", icon: Mail, tone: "told", box: [24, 838, 222, 112],
    detail: "This lead has the confirming email and nothing else. A phone number that appeared in a Zillow email is never texted, because the lead has not agreed to texts. The evening before, they get a reminder by email in the same thread, with a link to cancel. Once they add a mobile number and email through their own link, they are treated like any other lead.",
  },
  {
    id: "leadDetails", title: "Lead with details", subtitle: "Confirmation text + calendar invite", icon: Smartphone, tone: "told", box: [346, 838, 222, 112],
    detail: "Gets a confirmation text with a link to cancel, then a calendar invite by email. The evening before, a reminder text. Two hours after the showing starts, a text with the link to apply. If they cancel, by the link or by email to the agent, everyone else is updated.",
  },
  {
    id: "host", title: "The showing agent", subtitle: "Calendar event + texts on the day", icon: UserRoundCheck, tone: "told", box: [600, 838, 235, 112],
    detail: "Gets one event on their calendar for each showing, listing who is coming, how to reach them, and how each one booked. The event changes when someone joins, cancels, or moves. On the morning of a showing day they get one text listing the day’s showings, and a text for any change within two hours of a start.",
  },
  {
    id: "residents", title: "Current residents", subtitle: "One group text; none if vacant", icon: Home, tone: "told", box: [855, 838, 235, 112],
    detail: "The people on a current lease at the unit get one group text when a showing is first booked there, sent in the daytime, and a reminder the evening before listing the next day’s showings. If a showing is cancelled after they were told, they get a text saying so. A vacant unit gets nothing. If the residents cannot be looked up, the showing is flagged so the team tells them.",
  },
];

interface Edge {
  from: NodeId;
  to: NodeId;
  path: string;
  tone: Tone;
  label?: { text: string; x: number; y: number; anchor?: "start" | "middle" | "end" };
}
const edges: Edge[] = [
  { from: "ad", to: "general", path: "M209 85H264", tone: "listing" },
  { from: "general", to: "listings", path: "M464 85H538", tone: "listing" },
  { from: "editor", to: "listings", path: "M860 85H768", tone: "listing" },
  { from: "listings", to: "agent", path: "M653 126V239", tone: "listing", label: { text: "listing facts", x: 668, y: 186 } },
  { from: "listings", to: "open", path: "M570 126V165Q570 177 558 177H514Q502 177 502 189V387Q502 399 490 399H342Q330 399 330 411V474", tone: "listing", label: { text: "showing rules", x: 488, y: 206, anchor: "end" } },
  { from: "inquiry", to: "inbox", path: "M209 297H264", tone: "lead" },
  { from: "inbox", to: "agent", path: "M464 297H538", tone: "lead" },
  { from: "agent", to: "team", path: "M768 272H806Q814 272 814 264V259Q814 251 822 251H860", tone: "lead" },
  { from: "agent", to: "reply", path: "M768 322H806Q814 322 814 330V347Q814 355 822 355H860", tone: "lead" },
  { from: "reply", to: "page", path: "M885 397V422Q885 434 873 434H712Q700 434 700 446V474", tone: "lead", label: { text: "booking link", x: 792, y: 425, anchor: "middle" } },
  { from: "reply", to: "email", path: "M960 397V474", tone: "lead", label: { text: "confirms the time", x: 971, y: 441 } },
  { from: "calendars", to: "open", path: "M209 522H264", tone: "booking" },
  { from: "open", to: "agent", path: "M430 474V439Q430 427 442 427H588Q600 427 600 415V355", tone: "booking", label: { text: "open times", x: 515, y: 418, anchor: "middle" } },
  { from: "open", to: "page", path: "M464 522H538", tone: "booking" },
  { from: "page", to: "booked", path: "M653 570V644", tone: "booking" },
  { from: "email", to: "booked", path: "M975 570V644", tone: "booking" },
  { from: "booked", to: "open", path: "M538 686H376Q364 686 364 674V570", tone: "booking", label: { text: "booked showings", x: 451, y: 677, anchor: "middle" } },
  { from: "booked", to: "leadEmail", path: "M814 728V768Q814 780 802 780H147Q135 780 135 792V838", tone: "told", label: { text: "booked by email", x: 146, y: 812 } },
  { from: "booked", to: "leadDetails", path: "M814 728V768Q814 780 802 780H469Q457 780 457 792V838", tone: "told", label: { text: "booked on the page", x: 468, y: 812 } },
  { from: "booked", to: "host", path: "M814 728V768Q814 780 802 780H729Q717 780 717 792V838", tone: "told" },
  { from: "booked", to: "residents", path: "M814 728V768Q814 780 826 780H960Q972 780 972 792V838", tone: "told" },
  { from: "leadEmail", to: "leadDetails", path: "M246 894H346", tone: "told", label: { text: "adds details", x: 296, y: 884, anchor: "middle" } },
];

// The four parts of the flow, each named once on the diagram.
const bands: { tone: Tone; text: string; y: number }[] = [
  { tone: "listing", text: "Keep listings current", y: 28 },
  { tone: "lead", text: "Answer the inquiry", y: 196 },
  { tone: "booking", text: "Book a showing", y: 458 },
  { tone: "told", text: "Who is told", y: 764 },
];

const color: Record<Tone, string> = {
  listing: "text-teal-700 dark:text-teal-400",
  lead: "text-blue-700 dark:text-blue-400",
  booking: "text-amber-700 dark:text-amber-400",
  told: "text-violet-700 dark:text-violet-400",
};
const selectedColor: Record<Tone, string> = {
  listing: "border-teal-600 bg-teal-50 dark:bg-teal-950/60",
  lead: "border-blue-600 bg-blue-50 dark:bg-blue-950/60",
  booking: "border-amber-600 bg-amber-50 dark:bg-amber-950/60",
  told: "border-violet-600 bg-violet-50 dark:bg-violet-950/60",
};
const tones = ["listing", "lead", "booking", "told"] as const;
// The node each phone tab opens on.
const tabStart: Record<Tone, NodeId> = { listing: "general", lead: "agent", booking: "open", told: "leadDetails" };

export default function LeasingArchitecture() {
  const [selected, setSelected] = useState<NodeId>("agent");
  const markerId = useId().replaceAll(":", "");
  const active = nodes.find((node) => node.id === selected)!;
  const connected = new Set<NodeId>([selected]);
  for (const edge of edges) {
    if (edge.from === selected || edge.to === selected) {
      connected.add(edge.from);
      connected.add(edge.to);
    }
  }

  function nodeButton(node: Node, compact = false) {
    const Icon = node.icon;
    return (
      <button
        type="button"
        aria-pressed={selected === node.id}
        aria-controls={`${markerId}-detail`}
        onClick={() => setSelected(node.id)}
        className={cn(
          "flex h-full w-full flex-col justify-center rounded-xl border-2 bg-background px-4 text-left transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          compact ? "min-h-20 py-3" : "py-2",
          selected === node.id ? selectedColor[node.tone] : "border-border hover:border-foreground/40",
          !connected.has(node.id) && "text-muted-foreground",
        )}
      >
        <span className="flex items-center gap-2">
          <Icon className={cn("shrink-0", color[node.tone], compact ? "size-4" : "size-5")} aria-hidden="true" />
          <span className={cn("font-semibold leading-snug", compact ? "text-sm" : "text-[17px]")}>{node.title}</span>
        </span>
        <span className={cn("mt-1.5 leading-snug text-muted-foreground", compact ? "text-xs" : "text-[14px]")}>{node.subtitle}</span>
      </button>
    );
  }

  function edgePath(edge: Edge) {
    const lit = edge.from === selected || edge.to === selected;
    return (
      <g key={`${edge.from}-${edge.to}`} className={color[edge.tone]} opacity={lit ? 1 : 0.5}>
        <path d={edge.path} fill="none" className="stroke-background" strokeWidth="8" />
        <path d={edge.path} fill="none" stroke="currentColor" strokeWidth={lit ? 2.5 : 1.6} markerEnd={`url(#${markerId}-${edge.tone})`} />
        {edge.label && (
          <text x={edge.label.x} y={edge.label.y} textAnchor={edge.label.anchor ?? "start"} fill="currentColor" fontSize="14" fontWeight="500" paintOrder="stroke" className="stroke-background" strokeWidth="5" strokeLinejoin="round">
            {edge.label.text}
          </text>
        )}
      </g>
    );
  }

  const byId = (id: NodeId) => nodes.find((node) => node.id === id)!;

  function mobileRow(ids: NodeId[]) {
    return (
      <div className="grid gap-2">
        {ids.map((id, index) => {
          const node = byId(id);
          // The button fills its own wrapper, so the arrow above it is not counted in its height.
          return <div key={id}>{index > 0 && <ArrowDown className={cn("mx-auto my-2 size-4", color[node.tone])} aria-hidden="true" />}<div>{nodeButton(node, true)}</div></div>;
        })}
      </div>
    );
  }

  // A fork on a phone: what leads in, then the branches side by side.
  function mobileFork(label: string, ids: [NodeId, NodeId]) {
    const tone = byId(ids[0]).tone;
    return (
      <>
        <p className={cn("my-2 flex items-center justify-center gap-1.5 text-xs font-medium", color[tone])}><ArrowDown className="size-4" aria-hidden="true" />{label}</p>
        <div className="grid grid-cols-2 gap-2">{ids.map((id) => <div key={id}>{nodeButton(byId(id), true)}</div>)}</div>
      </>
    );
  }

  return (
    <figure id="booking" className="@container scroll-mt-6 overflow-clip rounded-2xl border bg-muted/10" aria-label="Leasing architecture">
      <p className="border-b px-5 py-3 text-xs text-muted-foreground">Select a step to read what happens there.</p>
      <svg viewBox="0 0 1120 980" className="hidden w-full @3xl:block" role="group" aria-label="Listing updates, lead replies, the two ways a showing is booked, and who is told about it">
        <defs>
          {tones.map((tone) => (
            <marker key={tone} id={`${markerId}-${tone}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse" className={color[tone]}>
              <path d="M1 1L9 5L1 9" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            </marker>
          ))}
        </defs>
        {bands.map((band) => (
          <text key={band.tone} x="24" y={band.y} fill="currentColor" fontSize="13" fontWeight="600" letterSpacing="0.06em" className={cn("uppercase", color[band.tone])}>
            {band.text}
          </text>
        ))}
        {edges.filter((edge) => edge.from !== selected && edge.to !== selected).map(edgePath)}
        {edges.filter((edge) => edge.from === selected || edge.to === selected).map(edgePath)}
        {nodes.map((node) => (
          <foreignObject key={node.id} x={node.box[0]} y={node.box[1]} width={node.box[2]} height={node.box[3]} className="overflow-visible">
            {nodeButton(node)}
          </foreignObject>
        ))}
      </svg>
      <Tabs
        defaultValue="lead"
        onValueChange={(value) => setSelected(tabStart[value as Tone])}
        className="p-4 @3xl:hidden"
      >
        <TabsList className="mb-3 grid w-full grid-cols-4">
          <TabsTrigger value="listing">Listings</TabsTrigger>
          <TabsTrigger value="lead">Inquiries</TabsTrigger>
          <TabsTrigger value="booking">Booking</TabsTrigger>
          <TabsTrigger value="told">Who’s told</TabsTrigger>
        </TabsList>
        <TabsContent value="listing" aria-label="Listing maintenance">
          <h3 className="mb-3 text-sm font-semibold text-teal-700 dark:text-teal-400">Keep listings current</h3>
          {mobileRow(["ad", "general", "listings"])}
          <p className="my-2 text-center text-xs text-muted-foreground">Also updated through</p>
          {nodeButton(byId("editor"), true)}
        </TabsContent>
        <TabsContent value="lead" aria-label="Lead conversation">
          <h3 className="mb-3 text-sm font-semibold text-blue-700 dark:text-blue-400">Answer the inquiry</h3>
          {mobileRow(["inquiry", "inbox", "agent"])}
          {mobileFork("One of", ["reply", "team"])}
          <p className="mt-3 text-xs leading-5 text-muted-foreground">The agent is given the listing facts and the open times. A reply carries the booking link, or confirms a time the lead agreed to.</p>
        </TabsContent>
        <TabsContent value="booking" aria-label="Open times and the two ways a showing is booked">
          <h3 className="mb-3 text-sm font-semibold text-amber-700 dark:text-amber-400">Book a showing</h3>
          {mobileRow(["calendars", "open"])}
          {mobileFork("Booked one of two ways", ["page", "email"])}
          <ArrowDown className="mx-auto my-2 size-4 text-amber-600" aria-hidden="true" />
          {nodeButton(byId("booked"), true)}
          <p className="mt-3 text-xs leading-5 text-muted-foreground">Showing rules shape the open times. A booked showing leaves them, and the next reply knows about it.</p>
        </TabsContent>
        <TabsContent value="told" aria-label="Who is told about a booked showing">
          <h3 className="mb-3 text-sm font-semibold text-violet-700 dark:text-violet-400">Who is told</h3>
          {nodeButton(byId("leadDetails"), true)}
          <p className="my-2 flex items-center justify-center gap-1.5 text-xs font-medium text-violet-700 dark:text-violet-400"><ArrowUp className="size-4" aria-hidden="true" />once they add their details</p>
          <div className="grid gap-2">
            {nodeButton(byId("leadEmail"), true)}
            {nodeButton(byId("host"), true)}
            {nodeButton(byId("residents"), true)}
          </div>
          <p className="mt-3 text-xs leading-5 text-muted-foreground">Everyone here is told when a showing is booked, and again when it changes.</p>
        </TabsContent>
      </Tabs>
      <figcaption id={`${markerId}-detail`} aria-live="polite" aria-atomic="true" className="grid gap-2 border-t bg-background px-5 py-5 sm:grid-cols-[12rem_1fr] sm:gap-5 @3xl:sticky @3xl:bottom-0">
        <p className={cn("font-semibold", color[active.tone])}>{active.title}</p>
        <p className="max-w-3xl text-sm leading-6 text-muted-foreground">{active.detail}</p>
      </figcaption>
    </figure>
  );
}
