import { Plus, X } from "lucide-react";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Label } from "~/components/ui/label";
import type { Window } from "~/sites/team/lib/showing-bookings";
import { cn } from "~/lib/utils";

const DAYS: { key: Window["days"][number]; label: string }[] = [
  { key: "mon", label: "Mon" },
  { key: "tue", label: "Tue" },
  { key: "wed", label: "Wed" },
  { key: "thu", label: "Thu" },
  { key: "fri", label: "Fri" },
  { key: "sat", label: "Sat" },
  { key: "sun", label: "Sun" },
];

/** Weekly windows of start times: days, the first start and the last start. */
export function WindowsEditor({
  id,
  noun,
  empty,
  windows,
  onChange,
}: {
  id: string;
  // "hours" or "preferred time", for labels: "Remove hours 1".
  noun: string;
  // Shown when there are no windows.
  empty: string;
  windows: Window[];
  onChange: (windows: Window[]) => void;
}) {
  const setWindow = (index: number, patch: Partial<Window>) =>
    onChange(windows.map((window, at) => (at === index ? { ...window, ...patch } : window)));
  return (
    <div className="space-y-3">
      {windows.length === 0 && <p className="text-sm text-muted-foreground">{empty}</p>}
      {windows.map((window, index) => (
        <div key={index} className="space-y-2 rounded-md border p-3">
          <div className="flex items-start justify-between gap-2">
            <div role="group" aria-label={`Days for ${noun} ${index + 1}`} className="flex flex-wrap gap-1">
              {DAYS.map(({ key, label }) => {
                const on = window.days.includes(key);
                return (
                  <button
                    key={key}
                    type="button"
                    aria-pressed={on}
                    onClick={() =>
                      setWindow(index, {
                        days: DAYS.map((day) => day.key).filter((day) => (day === key ? !on : window.days.includes(day))),
                      })
                    }
                    className={cn(
                      "min-w-[44px] rounded-md border px-2 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      on
                        ? "border-primary bg-primary text-primary-foreground"
                        : "border-input bg-background text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                    )}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-8 w-8 shrink-0"
              aria-label={`Remove ${noun} ${index + 1}`}
              onClick={() => onChange(windows.filter((_, at) => at !== index))}
            >
              <X className="h-4 w-4" />
            </Button>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Label htmlFor={`${id}-${index}-start`} className="sr-only">
              {`${noun} ${index + 1}: first start`}
            </Label>
            <Input
              id={`${id}-${index}-start`}
              type="time"
              step={1800}
              className="w-32"
              value={window.start.slice(0, 5)}
              onChange={(event) => setWindow(index, { start: event.target.value })}
            />
            <span className="text-muted-foreground">to last start</span>
            <Label htmlFor={`${id}-${index}-end`} className="sr-only">
              {`${noun} ${index + 1}: last start`}
            </Label>
            <Input
              id={`${id}-${index}-end`}
              type="time"
              step={1800}
              className="w-32"
              value={window.end.slice(0, 5)}
              onChange={(event) => setWindow(index, { end: event.target.value })}
            />
          </div>
        </div>
      ))}
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="gap-1.5"
        onClick={() => onChange([...windows, { days: ["mon", "tue", "wed", "thu", "fri"], start: "16:00", end: "18:00" }])}
      >
        <Plus className="h-3.5 w-3.5" aria-hidden="true" />
        Add {noun}
      </Button>
    </div>
  );
}
