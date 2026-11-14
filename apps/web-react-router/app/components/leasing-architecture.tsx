import { useId, useState } from "react";
import { ArrowDown, CalendarDays, Database, Globe, Mail, MessageSquare, Settings2, Users } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { cn } from "~/lib/utils";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";

type NodeId = "ad" | "general" | "listings" | "editor" | "inquiry" | "inbox" | "agent" | "reply" | "calendarA" | "calendarB" | "availability" | "booking" | "visit";
type Tone = "listing" | "lead" | "booking";
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
    id: "ad", title: "Live Zillow listing", subtitle: "The public rental ad", icon: Globe, tone: "listing", box: [24, 40, 185, 82],
    detail: "The saved Zillow URL lets the general agent fetch the live ad on request, including rent, availability, amenities, and the description. If Zillow cannot be read, the team can supply the ad text.",
  },
  {
    id: "general", title: "General agent", subtitle: "Fetch the ad, update facts", icon: MessageSquare, tone: "listing", box: [264, 40, 200, 82],
    detail: "The general agent reads the live Zillow listing, compares it with the saved record, and updates the rental-listing table through validated tools. Changes to free text and links follow the existing review rules.",
  },
  {
    id: "listings", title: "Rental listing data", subtitle: "Rent, facts, showing rules", icon: Database, tone: "listing", box: [538, 40, 230, 82],
    detail: "One Postgres store holds asking rent, lease dates, unit facts, booking links, and showing rules. Each change records who made it and what changed. The lead workflow reads this data for every inquiry.",
  },
  {
    id: "editor", title: "Team editor & MCP", subtitle: "Maintain the same records", icon: Settings2, tone: "listing", box: [860, 40, 230, 82],
    detail: "The rental-listings page and Sernia MCP tools use the same listing service as the general agent. Staff can maintain facts, set showing preferences, and inspect change history from either interface.",
  },
  {
    id: "inquiry", title: "Rental inquiry", subtitle: "A prospect writes on Zillow", icon: Users, tone: "lead", box: [24, 245, 185, 96],
    detail: "A prospect asks about an apartment or replies to an existing Zillow email thread. The conversation may contain unit questions, move-in preferences, or showing logistics.",
  },
  {
    id: "inbox", title: "Gmail & lead queue", subtitle: "Collect the conversation", icon: Mail, tone: "lead", box: [264, 245, 200, 96],
    detail: "Gmail notifications enqueue incoming messages. The queue groups messages by thread, handles retries, and loads the shared mailbox’s conversation before preparing the lead agent’s input.",
  },
  {
    id: "agent", title: "Zillow Leads Agent", subtitle: "Thread + listing + tour times", icon: MessageSquare, tone: "lead", box: [538, 235, 230, 116],
    detail: "The specialist answers from the supplied conversation, listing facts, and available showing times. It has no tools to browse private company data. That limited access is the basis for automatic routine replies, with exceptions handed to the team.",
  },
  {
    id: "reply", title: "Reply to the prospect", subtitle: "Answers, times, booking link", icon: Mail, tone: "lead", box: [860, 245, 230, 96],
    detail: "The agent returns a reply, no reply, or an exception for the team. Application code keeps a reply on the original Zillow thread. The intended flow sends routine replies automatically and directs prospects to booking.",
  },
  {
    id: "calendarA", title: "Showing agent A", subtitle: "Shared calendar", icon: CalendarDays, tone: "booking", box: [50, 456, 185, 78],
    detail: "Each showing agent shares a calendar for free/busy checks. Busy time blocks that agent’s availability; an unreadable calendar cannot be treated as free.",
  },
  {
    id: "calendarB", title: "Showing agent B", subtitle: "Shared calendar", icon: CalendarDays, tone: "booking", box: [50, 566, 185, 78],
    detail: "Additional showing agents contribute their own calendars. Availability can offer times when a host is free, while retaining the host assignment internally.",
  },
  {
    id: "availability", title: "Available tour times", subtitle: "Free/busy + showing rules", icon: CalendarDays, tone: "booking", box: [310, 502, 215, 96],
    detail: "Combine the showing agents’ free/busy calendars with the listing’s showing window, blackout dates, and at least 24 hours’ notice. Supply open times and existing showings to the lead’s case; preferred times help guide the offer.",
  },
  {
    id: "booking", title: "Book a showing", subtitle: "Choose a time or join a tour", icon: Users, tone: "booking", box: [604, 502, 215, 96],
    detail: "The prospect follows a booking link to choose a time or join an existing showing. Booked tours feed back into availability and the lead agent’s context so several prospects can share a visit.",
  },
  {
    id: "visit", title: "Coordinate the visit", subtitle: "Invites + resident notice", icon: CalendarDays, tone: "booking", box: [894, 502, 196, 96],
    detail: "Block the showing agent’s calendar, send calendar invitations to the prospect and host, and notify the current resident through the existing messaging gates. A vacant unit needs no resident notice.",
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
  { from: "ad", to: "general", path: "M209 81H264", tone: "listing" },
  { from: "general", to: "listings", path: "M464 81H538", tone: "listing" },
  { from: "editor", to: "listings", path: "M860 81H768", tone: "listing" },
  { from: "listings", to: "agent", path: "M653 122V235", tone: "listing", label: { text: "listing facts", x: 668, y: 180 } },
  { from: "inquiry", to: "inbox", path: "M209 293H264", tone: "lead" },
  { from: "inbox", to: "agent", path: "M464 293H538", tone: "lead" },
  { from: "agent", to: "reply", path: "M768 293H860", tone: "lead" },
  { from: "calendarA", to: "availability", path: "M235 495H265Q277 495 277 507V538Q277 550 289 550H310", tone: "booking" },
  { from: "calendarB", to: "availability", path: "M235 605H265Q277 605 277 593V562Q277 550 289 550H310", tone: "booking" },
  { from: "availability", to: "agent", path: "M417 502V399Q417 387 429 387H641Q653 387 653 375V351", tone: "booking", label: { text: "available times", x: 520, y: 378, anchor: "middle" } },
  { from: "reply", to: "booking", path: "M975 341V375Q975 387 963 387H724Q712 387 712 399V502", tone: "lead", label: { text: "booking link", x: 838, y: 378, anchor: "middle" } },
  { from: "availability", to: "booking", path: "M525 550H604", tone: "booking" },
  { from: "booking", to: "visit", path: "M819 550H894", tone: "booking" },
  { from: "booking", to: "availability", path: "M712 598V638Q712 650 700 650H429Q417 650 417 638V598", tone: "booking", label: { text: "booked showings", x: 564, y: 641, anchor: "middle" } },
  { from: "listings", to: "availability", path: "M570 122V161Q570 173 558 173H514Q502 173 502 185V426Q502 438 490 438H362Q350 438 350 450V502", tone: "listing", label: { text: "showing rules", x: 485, y: 202, anchor: "end" } },
];

const color: Record<Tone, string> = {
  listing: "text-teal-700 dark:text-teal-400",
  lead: "text-blue-700 dark:text-blue-400",
  booking: "text-amber-700 dark:text-amber-400",
};
const selectedColor: Record<Tone, string> = {
  listing: "border-teal-600 bg-teal-50 dark:bg-teal-950/60",
  lead: "border-blue-600 bg-blue-50 dark:bg-blue-950/60",
  booking: "border-amber-600 bg-amber-50 dark:bg-amber-950/60",
};

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

  function mobileRow(ids: NodeId[]) {
    return (
      <div className="grid gap-2">
        {ids.map((id, index) => {
          const node = nodes.find((item) => item.id === id)!;
          return <div key={id}>{index > 0 && <ArrowDown className={cn("mx-auto my-2 size-4", color[node.tone])} aria-hidden="true" />}{nodeButton(node, true)}</div>;
        })}
      </div>
    );
  }

  return (
    <figure id="booking" className="@container scroll-mt-6 overflow-hidden rounded-2xl border bg-muted/10" aria-label="Leasing architecture">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b px-5 py-3 text-xs text-muted-foreground">
        <p>Select a component to explore the flow.</p>
        <a href="https://github.com/EmilioEsposito/portfolio/issues/368" className="inline-flex items-center gap-2 underline-offset-4 hover:underline">
          <span className="h-3 w-5 rounded border border-dashed border-amber-600" aria-hidden="true" />Booking integration in progress
        </a>
      </div>
      <svg viewBox="0 0 1120 700" className="hidden w-full @3xl:block" role="group" aria-label="Listing updates, lead replies, and showing bookings">
        <defs>
          {(["listing", "lead", "booking"] as const).map((tone) => (
            <marker key={tone} id={`${markerId}-${tone}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse" className={color[tone]}>
              <path d="M1 1L9 5L1 9" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            </marker>
          ))}
        </defs>
        <rect x="24" y="420" width="1080" height="260" rx="18" fill="none" className="stroke-amber-600/60 dark:stroke-amber-500/60" strokeDasharray="5 6" />
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
        onValueChange={(value) => setSelected(value === "listing" ? "general" : value === "booking" ? "availability" : "agent")}
        className="p-4 @3xl:hidden"
      >
        <TabsList className="mb-3 grid w-full grid-cols-3">
          <TabsTrigger value="listing">Listings</TabsTrigger>
          <TabsTrigger value="lead">Inquiries</TabsTrigger>
          <TabsTrigger value="booking">Showings</TabsTrigger>
        </TabsList>
        <TabsContent value="listing" aria-label="Listing maintenance">
          <h3 className="mb-3 text-sm font-semibold text-teal-700 dark:text-teal-400">Keep listings current</h3>
          {mobileRow(["ad", "general", "listings"])}
          <p className="my-2 text-center text-xs text-muted-foreground">Also updated through</p>
          {nodeButton(nodes.find((node) => node.id === "editor")!, true)}
        </TabsContent>
        <TabsContent value="lead" aria-label="Lead conversation">
          <h3 className="mb-3 text-sm font-semibold text-blue-700 dark:text-blue-400">Answer the inquiry</h3>
          {mobileRow(["inquiry", "inbox", "agent", "reply"])}
          <p className="mt-3 text-xs leading-5 text-muted-foreground">The agent receives listing facts and the available times below.</p>
        </TabsContent>
        <TabsContent value="booking" aria-label="Showing availability and booking" className="rounded-xl border border-dashed border-amber-600/70 p-3">
          <h3 className="mb-3 text-sm font-semibold text-amber-700 dark:text-amber-400">Find a time and book</h3>
          <div className="grid grid-cols-2 gap-2">
            {nodeButton(nodes.find((node) => node.id === "calendarA")!, true)}
            {nodeButton(nodes.find((node) => node.id === "calendarB")!, true)}
          </div>
          <ArrowDown className="mx-auto my-3 size-4 text-amber-600" aria-hidden="true" />
          {mobileRow(["availability", "booking", "visit"])}
          <p className="mt-3 text-xs leading-5 text-muted-foreground">Showing rules shape availability. Booked showings feed back into the next reply.</p>
        </TabsContent>
      </Tabs>
      <figcaption id={`${markerId}-detail`} aria-live="polite" aria-atomic="true" className="grid gap-2 border-t bg-background px-5 py-5 sm:grid-cols-[12rem_1fr] sm:gap-5">
        <p className={cn("font-semibold", color[active.tone])}>{active.title}</p>
        <p className="max-w-3xl text-sm leading-6 text-muted-foreground">{active.detail}</p>
      </figcaption>
    </figure>
  );
}
