import { useState } from "react";
import { ArrowDown, ArrowRight, Check, Circle, Lock, RotateCcw } from "lucide-react";
import { cn } from "~/lib/utils";
import {
  currentStage,
  findItem,
  litSplits,
  phases,
  splits,
  stageIsRelated,
  stages,
  type MapItem,
  type Phase,
  type Split,
  type SplitId,
  type Stage,
  type Status,
} from "~/lib/evals-map";

const statusLabel: Record<Status, string> = {
  done: "Done",
  now: "We are here",
  next: "Not yet",
};

/** Filled cells have been run. Outlined cells have never been looked at. */
const cellClass: Record<SplitId, string> = {
  train: "bg-violet-500 dark:bg-violet-400",
  dev: "bg-teal-600 dark:bg-teal-500",
  test: "border border-zinc-400 dark:border-zinc-500",
};
const inReviewCell = "bg-amber-500 dark:bg-amber-400";

function StatusMark({ status }: { status: Status }) {
  if (status === "done") {
    return <Check aria-hidden="true" className="size-3.5 text-muted-foreground" />;
  }
  if (status === "now") {
    return (
      <span aria-hidden="true" className="relative flex size-2.5">
        <span className="absolute inline-flex size-full rounded-full bg-amber-500 opacity-60 motion-safe:animate-ping" />
        <span className="relative inline-flex size-2.5 rounded-full bg-amber-500" />
      </span>
    );
  }
  return <Circle aria-hidden="true" className="size-3 text-muted-foreground/60" />;
}

function Aka({ names, className }: { names: string[]; className?: string }) {
  if (names.length === 0) return null;
  return (
    <span className={cn("block text-xs leading-5 text-muted-foreground", className)}>
      aka {names.join(", ")}
    </span>
  );
}

interface Selection {
  activeId: string;
  pinnedId: string;
  preview: (id: string | null) => void;
  pin: (id: string) => void;
}

function itemProps(id: string, selection: Selection) {
  return {
    type: "button" as const,
    "aria-pressed": selection.pinnedId === id,
    onMouseEnter: () => selection.preview(id),
    onMouseLeave: () => selection.preview(null),
    onFocus: () => selection.preview(id),
    onBlur: () => selection.preview(null),
    onClick: () => selection.pin(id),
  };
}

function StageButton({
  stage,
  active,
  selection,
}: {
  stage: Stage;
  active: MapItem;
  selection: Selection;
}) {
  const isActive = selection.activeId === stage.id;
  const related = stageIsRelated(stage, active);
  return (
    <button
      {...itemProps(stage.id, selection)}
      className={cn(
        "flex h-full w-full flex-col items-start rounded-lg border bg-background px-3 py-2.5 text-left transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        isActive && "border-foreground bg-muted/60",
        !isActive && related && "border-foreground/50",
        !isActive && !related && "hover:border-foreground/40",
        stage.status === "next" && !isActive && "border-dashed",
      )}
    >
      <span className="flex w-full items-center gap-2">
        <StatusMark status={stage.status} />
        <span className="text-sm font-semibold leading-5">{stage.term}</span>
        <span className="sr-only">({statusLabel[stage.status]})</span>
      </span>
      <Aka names={stage.aka.slice(0, 2)} className="mt-0.5" />
    </button>
  );
}

function StageRow({
  row,
  active,
  selection,
}: {
  row: Stage[];
  active: MapItem;
  selection: Selection;
}) {
  return (
    <div className="@container">
      {/* The arrow leads each stage, so a wrapped row still reads as a continuation. */}
      <ol className="grid grid-cols-1 gap-2 @sm:grid-cols-2 @xl:grid-cols-3 @3xl:grid-cols-4">
        {row.map((stage, index) => (
          <li key={stage.id} className="flex flex-col gap-1.5 @sm:flex-row @sm:items-stretch">
            {index > 0 && (
              <>
                <ArrowDown
                  aria-hidden="true"
                  className="size-4 self-center text-muted-foreground @sm:hidden"
                />
                <ArrowRight
                  aria-hidden="true"
                  className="hidden size-4 shrink-0 self-center text-muted-foreground @sm:block"
                />
              </>
            )}
            <StageButton stage={stage} active={active} selection={selection} />
          </li>
        ))}
      </ol>
    </div>
  );
}

function SplitTray({
  split,
  active,
  selection,
}: {
  split: Split;
  active: MapItem;
  selection: Selection;
}) {
  const lit = litSplits(active);
  const isActive = selection.activeId === split.id;
  const isLit = lit.includes(split.id);
  const dimmed = lit.length > 0 && !isLit;
  return (
    <button
      {...itemProps(split.id, selection)}
      style={{ flexGrow: split.cases, flexBasis: "11rem" }}
      className={cn(
        "flex flex-col items-start rounded-lg border bg-background p-3 text-left transition-[opacity,border-color,background-color]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        isActive && "border-foreground bg-muted/60",
        !isActive && isLit && "border-foreground/60",
        !isActive && !isLit && "hover:border-foreground/40",
        dimmed && "opacity-45",
      )}
    >
      <span className="flex w-full items-center gap-2">
        {split.id === "test" && (
          <Lock aria-hidden="true" className="size-3.5 text-muted-foreground" />
        )}
        <span className="text-sm font-semibold leading-5">{split.term}</span>
        <span className="ml-auto text-xs tabular-nums text-muted-foreground">{split.cases}</span>
      </span>
      <Aka names={split.aka} className="mt-0.5" />
      <span aria-hidden="true" className="mt-2.5 flex flex-wrap gap-[2px]">
        {Array.from({ length: split.cases }, (_, index) => (
          <span
            key={index}
            className={cn(
              "size-1.5 rounded-[1px]",
              index < split.inReview ? inReviewCell : cellClass[split.id],
            )}
          />
        ))}
      </span>
    </button>
  );
}

function PhaseBand({
  phase,
  index,
  active,
  selection,
}: {
  phase: Phase;
  index: number;
  active: MapItem;
  selection: Selection;
}) {
  const own = stages.filter((stage) => stage.phase === phase.id);
  // Consecutive stages that repeat together are drawn inside one loop.
  const rows: Stage[][] = [];
  for (const stage of own) {
    const last = rows[rows.length - 1];
    if (last && last[0].loop === stage.loop) last.push(stage);
    else rows.push([stage]);
  }
  const headingId = `phase-${phase.id}`;
  return (
    <section aria-labelledby={headingId} className="rounded-2xl border p-4 sm:p-5">
      <header className="mb-4">
        <h3 id={headingId} className="flex items-baseline gap-2.5 text-lg font-semibold">
          <span className="text-sm font-normal tabular-nums text-muted-foreground">
            {index + 1}
          </span>
          {phase.term}
        </h3>
        <Aka names={phase.aka} />
        <p className="mt-1.5 text-sm leading-6 text-muted-foreground">{phase.question}</p>
      </header>

      {phase.id === "offline" && (
        <div className="mb-4">
          <div className="flex flex-wrap gap-2.5">
            {splits.map((split) => (
              <SplitTray key={split.id} split={split} active={active} selection={selection} />
            ))}
          </div>
          <p className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>One square is one test case.</span>
            <span className="inline-flex items-center gap-1.5">
              <span aria-hidden="true" className={cn("size-1.5 rounded-[1px]", inReviewCell)} />
              being annotated now
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span aria-hidden="true" className={cn("size-1.5 rounded-[1px]", cellClass.test)} />
              never run
            </span>
          </p>
        </div>
      )}

      <div className="space-y-3">
        {rows.map((row) =>
          row[0].loop ? (
            <div key={row[0].id} className="rounded-xl border border-dashed p-3">
              <StageRow row={row} active={active} selection={selection} />
              {phase.repeat && (
                <p className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
                  <RotateCcw aria-hidden="true" className="size-3.5 shrink-0" />
                  {phase.repeat}
                </p>
              )}
            </div>
          ) : (
            <StageRow key={row[0].id} row={row} active={active} selection={selection} />
          ),
        )}
      </div>
    </section>
  );
}

function DetailPanel({ item }: { item: MapItem }) {
  const phase = item.kind === "stage" ? phases.find((p) => p.id === item.phase) : undefined;
  const touched = item.kind === "stage" ? splits.filter((s) => item.touches.includes(s.id)) : [];
  return (
    <div className="p-4 sm:p-5">
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        {item.kind === "stage" ? (
          <>
            <span className="inline-flex items-center gap-1.5">
              <StatusMark status={item.status} />
              {statusLabel[item.status]}
            </span>
            <span>{phase?.term}</span>
          </>
        ) : (
          <span>
            Dataset split, {item.cases} test cases from {item.threads} threads
          </span>
        )}
      </p>
      <h3 className="mt-2 text-xl font-semibold tracking-tight">{item.term}</h3>
      <Aka names={item.aka} className="mt-0.5" />

      <div className="mt-3 space-y-2.5 text-sm leading-6">
        {item.what.map((paragraph) => (
          <p key={paragraph}>{paragraph}</p>
        ))}
      </div>

      {touched.length > 0 && (
        <p className="mt-3 text-sm leading-6 text-muted-foreground">
          Uses: {touched.map((split) => split.term.toLowerCase()).join(", ")}.
        </p>
      )}

      <h4 className="mt-4 text-sm font-semibold">In this project</h4>
      <p className="mt-1 text-sm leading-6 text-muted-foreground">{item.here}</p>

      <figure className="mt-4 rounded-lg border bg-muted/40 p-3">
        <figcaption className="text-xs text-muted-foreground">{item.example.caption}</figcaption>
        {item.example.blocks.map((block) => (
          <div key={block.label ?? block.text} className="mt-2">
            {block.label && <p className="text-xs font-semibold">{block.label}</p>}
            <p className="mt-0.5 whitespace-pre-wrap break-words text-[13px] leading-5">
              {block.text}
            </p>
          </div>
        ))}
      </figure>
    </div>
  );
}

export default function EvalsMap() {
  const [pinnedId, setPinnedId] = useState(currentStage.id);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const activeId = previewId ?? pinnedId;
  const active = findItem(activeId) ?? currentStage;
  const selection: Selection = {
    activeId: active.id,
    pinnedId,
    preview: setPreviewId,
    pin: setPinnedId,
  };

  return (
    <div className="@container">
      <div className="grid gap-5 @4xl:grid-cols-[minmax(0,1fr)_22rem] @4xl:items-start">
        <div className="space-y-4">
        {phases.map((phase, index) => (
          <PhaseBand
            key={phase.id}
            phase={phase}
            index={index}
            active={active}
            selection={selection}
          />
        ))}
      </div>
      {/* On a phone the panel rides the bottom edge, so a tap always shows its result. */}
      <aside
        aria-live="polite"
        aria-label="Details for the selected part of the map"
        className={cn(
          "sticky bottom-3 max-h-[40vh] overflow-y-auto rounded-2xl border bg-background shadow-lg",
          "@4xl:top-6 @4xl:bottom-auto @4xl:max-h-[calc(100vh-3rem)] @4xl:shadow-none",
        )}
      >
          <DetailPanel item={active} />
        </aside>
      </div>
    </div>
  );
}
