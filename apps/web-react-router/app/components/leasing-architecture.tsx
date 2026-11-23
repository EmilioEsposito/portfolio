import { useId, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties, TouchList as ReactTouchList } from "react";
import {
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
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { cn } from "~/lib/utils";

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
    detail: "Each showing agent connects a Google calendar. Anything busy on it blocks that agent, and a calendar that cannot be read counts as busy.",
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
    detail: "This lead has only the confirming email. A phone number that appeared in a Zillow email is never texted, because the lead has not agreed to texts. The evening before, they get a reminder by email in the same thread, with a link to cancel. Once they add a mobile number and email through their own link, they are treated like any other lead.",
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

// On a screen too narrow for the whole diagram, it is drawn this wide inside a
// frame that scrolls both ways, so the text stays readable and every branch is
// still on the one picture. The reader zooms between the frame's own width (the
// whole diagram at once) and the widest.
const START_WIDTH = 880;
const WIDEST = 1120;
const ZOOM_STEP = 1.3;

function spread(touches: ReactTouchList) {
  return Math.hypot(touches[0].clientX - touches[1].clientX, touches[0].clientY - touches[1].clientY);
}

export default function LeasingArchitecture() {
  const [selected, setSelected] = useState<NodeId>("agent");
  // The drawn width in a narrow frame; null fits the frame. Ignored when the
  // whole diagram fits, where it is always the frame's width.
  const [width, setWidth] = useState<number | null>(START_WIDTH);
  const frame = useRef<HTMLDivElement>(null);
  const drawing = useRef<SVGSVGElement>(null);
  // The point of the diagram that stays put while the width changes.
  const anchor = useRef<{ rx: number; ry: number; x: number; y: number } | null>(null);
  const pinch = useRef<{ spread: number; width: number } | null>(null);
  const markerId = useId().replaceAll(":", "");

  // Change the drawn width, keeping the point at (x, y) in the frame where it is.
  function zoomTo(wanted: number, at?: { x: number; y: number }) {
    const el = frame.current;
    if (!el) return;
    const x = at?.x ?? el.clientWidth / 2;
    const y = at?.y ?? el.clientHeight / 2;
    anchor.current = { rx: (el.scrollLeft + x) / el.scrollWidth, ry: (el.scrollTop + y) / el.scrollHeight, x, y };
    const next = Math.min(wanted, WIDEST);
    setWidth(next <= el.clientWidth + 1 ? null : next);
  }
  function zoomBy(factor: number) {
    const drawn = drawing.current?.getBoundingClientRect().width;
    if (drawn) zoomTo(drawn * factor);
  }
  // In a narrow frame, start on the selected step, with the steps around it in view.
  useLayoutEffect(() => {
    const el = frame.current;
    const start = nodes.find((node) => node.id === selected);
    if (!el || !start || el.scrollWidth <= el.clientWidth) return;
    const scale = el.scrollWidth / 1120;
    el.scrollLeft = (start.box[0] + start.box[2] / 2) * scale - el.clientWidth / 2;
    el.scrollTop = (start.box[1] + start.box[3] / 2) * scale - el.clientHeight / 2;
    // Only once, when the diagram first appears.
  }, []);
  useLayoutEffect(() => {
    const el = frame.current;
    const kept = anchor.current;
    if (!el || !kept) return;
    el.scrollLeft = kept.rx * el.scrollWidth - kept.x;
    el.scrollTop = kept.ry * el.scrollHeight - kept.y;
    anchor.current = null;
  }, [width]);
  const active = nodes.find((node) => node.id === selected)!;
  const connected = new Set<NodeId>([selected]);
  for (const edge of edges) {
    if (edge.from === selected || edge.to === selected) {
      connected.add(edge.from);
      connected.add(edge.to);
    }
  }

  function nodeButton(node: Node) {
    const Icon = node.icon;
    return (
      <button
        type="button"
        aria-pressed={selected === node.id}
        aria-controls={`${markerId}-detail`}
        onClick={() => setSelected(node.id)}
        className={cn(
          "flex h-full w-full flex-col justify-center rounded-xl border-2 bg-background px-4 py-2 text-left transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          selected === node.id ? selectedColor[node.tone] : "border-border hover:border-foreground/40",
          !connected.has(node.id) && "text-muted-foreground",
        )}
      >
        <span className="flex items-center gap-2">
          <Icon className={cn("size-5 shrink-0", color[node.tone])} aria-hidden="true" />
          <span className="text-[17px] font-semibold leading-snug">{node.title}</span>
        </span>
        <span className="mt-1.5 text-[14px] leading-snug text-muted-foreground">{node.subtitle}</span>
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

  const zoomButton = "inline-flex size-8 items-center justify-center rounded-md border bg-background text-foreground disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

  return (
    <figure id="booking" className="@container scroll-mt-6 overflow-clip rounded-2xl border bg-muted/10" aria-label="Leasing architecture">
      <div className="flex items-center justify-between gap-3 border-b px-5 py-2.5 text-xs text-muted-foreground">
        <p>Select a step to read what happens there.<span className="@3xl:hidden"> Scroll to move around, or zoom out to see it all.</span></p>
        <div className="flex shrink-0 gap-1.5 @3xl:hidden">
          <button type="button" className={zoomButton} onClick={() => zoomBy(1 / ZOOM_STEP)} disabled={width === null} aria-label="Zoom out"><ZoomOut className="size-4" aria-hidden="true" /></button>
          <button type="button" className={zoomButton} onClick={() => zoomBy(ZOOM_STEP)} disabled={width !== null && width >= WIDEST} aria-label="Zoom in"><ZoomIn className="size-4" aria-hidden="true" /></button>
        </div>
      </div>
      <div
        ref={frame}
        // Two fingers zoom the diagram, not the page; one finger scrolls it.
        className="max-h-[62vh] overflow-auto [touch-action:pan-x_pan-y] @3xl:max-h-none @3xl:overflow-visible @3xl:[touch-action:auto]"
        onTouchStart={(event) => {
          const drawn = drawing.current?.getBoundingClientRect().width;
          if (event.touches.length === 2 && drawn) pinch.current = { spread: spread(event.touches), width: drawn };
        }}
        onTouchMove={(event) => {
          const el = frame.current;
          const began = pinch.current;
          if (!el || !began || event.touches.length !== 2) return;
          const box = el.getBoundingClientRect();
          zoomTo((began.width * spread(event.touches)) / began.spread, {
            x: (event.touches[0].clientX + event.touches[1].clientX) / 2 - box.left,
            y: (event.touches[0].clientY + event.touches[1].clientY) / 2 - box.top,
          });
        }}
        onTouchEnd={() => { pinch.current = null; }}
        onTouchCancel={() => { pinch.current = null; }}
      >
        <svg
          ref={drawing}
          viewBox="0 0 1120 980"
          style={{ "--drawn": width === null ? "100%" : `${width}px` } as CSSProperties}
          className="block w-[var(--drawn)] max-w-none @3xl:w-full"
          role="group"
          aria-label="Listing updates, lead replies, the two ways a showing is booked, and who is told about it"
        >
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
      </div>
      <figcaption id={`${markerId}-detail`} aria-live="polite" aria-atomic="true" className="grid gap-2 border-t bg-background px-5 py-5 sm:grid-cols-[12rem_1fr] sm:gap-5 @3xl:sticky @3xl:bottom-0">
        <p className={cn("font-semibold", color[active.tone])}>{active.title}</p>
        <p className="max-w-3xl text-sm leading-6 text-muted-foreground">{active.detail}</p>
      </figcaption>
    </figure>
  );
}
