import type { Route } from "./+types/sernia-settings";
import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router";
import { useAuth } from "@clerk/react-router";
import { AuthGuard } from "~/components/auth-guard";
import { Button } from "~/components/ui/button";
import { Switch } from "~/components/ui/switch";
import { Label } from "~/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "~/components/ui/card";
import { cn } from "~/lib/utils";
import {
  Building,
  Loader2,
  ArrowLeft,
  Save,
  RotateCcw,
  Menu,
} from "lucide-react";
import { SidebarProvider, SidebarInset, useSidebar } from "~/components/ui/sidebar";
import { ConversationSidebar } from "~/components/sernia/conversation-sidebar";

const API_BASE = "/api/sernia-ai";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Sernia Settings" },
    { name: "description", content: "Configure Sernia AI triggers and schedule." },
  ];
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DAYS = [
  { value: 0, label: "Mon" },
  { value: 1, label: "Tue" },
  { value: 2, label: "Wed" },
  { value: 3, label: "Thu" },
  { value: 4, label: "Fri" },
  { value: 5, label: "Sat" },
  { value: 6, label: "Sun" },
] as const;

function formatHour(h: number) {
  if (h === 0) return "12am";
  if (h < 12) return `${h}am`;
  if (h === 12) return "12pm";
  return `${h - 12}pm`;
}

// Business-hour range for the grid (5am–11pm covers realistic use)
const HOUR_OPTIONS = Array.from({ length: 19 }, (_, i) => i + 5); // 5..23

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface ScheduleConfig {
  days_of_week: number[];
  hours: number[];
}

interface Settings {
  triggers_enabled: boolean;
  schedule_config: ScheduleConfig;
}

function SettingsMobileSidebarToggle() {
  const { toggleSidebar } = useSidebar();
  return (
    <Button
      variant="ghost"
      size="icon"
      className="h-8 w-8 shrink-0 md:hidden"
      onClick={toggleSidebar}
    >
      <Menu className="w-4 h-4" />
    </Button>
  );
}

export default function SerniaSettingsPage() {
  const { getToken } = useAuth();
  const navigate = useNavigate();

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  // Settings state
  const [triggersEnabled, setTriggersEnabled] = useState(false);
  const [days, setDays] = useState<number[]>([0, 1, 2, 3, 4]);
  const [hours, setHours] = useState<number[]>([8, 11, 14, 17]);

  // Snapshot of last-saved values to detect dirty state
  const [saved, setSaved] = useState<Settings | null>(null);

  const isDirty =
    saved !== null &&
    (triggersEnabled !== saved.triggers_enabled ||
      JSON.stringify([...days].sort()) !== JSON.stringify([...saved.schedule_config.days_of_week].sort()) ||
      JSON.stringify([...hours].sort()) !== JSON.stringify([...saved.schedule_config.hours].sort()));

  // Fetch settings
  const fetchSettings = useCallback(async () => {
    setError(null);
    try {
      const token = await getToken();
      const res = await fetch(`${API_BASE}/admin/settings`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data: Settings = await res.json();
      setTriggersEnabled(data.triggers_enabled);
      setDays(data.schedule_config.days_of_week);
      setHours(data.schedule_config.hours);
      setSaved(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load settings");
    }
  }, [getToken]);

  useEffect(() => {
    fetchSettings();
  }, [fetchSettings]);

  // Save
  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setSuccess(false);
    try {
      const token = await getToken();
      const res = await fetch(`${API_BASE}/admin/settings`, {
        method: "PATCH",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          triggers_enabled: triggersEnabled,
          schedule_config: {
            days_of_week: [...days].sort(),
            hours: [...hours].sort(),
          },
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `${res.status} ${res.statusText}`);
      }
      const snapshot: Settings = {
        triggers_enabled: triggersEnabled,
        schedule_config: {
          days_of_week: [...days].sort(),
          hours: [...hours].sort(),
        },
      };
      setSaved(snapshot);
      setSuccess(true);
      setTimeout(() => setSuccess(false), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  };

  // Reset to last saved
  const handleReset = () => {
    if (!saved) return;
    setTriggersEnabled(saved.triggers_enabled);
    setDays(saved.schedule_config.days_of_week);
    setHours(saved.schedule_config.hours);
  };

  // Toggle helpers
  const toggleDay = (d: number) =>
    setDays((prev) =>
      prev.includes(d) ? prev.filter((x) => x !== d) : [...prev, d]
    );

  const toggleHour = (h: number) =>
    setHours((prev) =>
      prev.includes(h) ? prev.filter((x) => x !== h) : [...prev, h]
    );

  const handleSelectConversation = useCallback(
    (convId: string) => {
      navigate(`/sernia-chat?id=${convId}`);
    },
    [navigate]
  );

  const handleNewConversation = useCallback(() => {
    navigate("/sernia-chat");
  }, [navigate]);

  return (
    <AuthGuard
      requireDomain="serniacapital.com"
      message="Admin access required"
      icon={<Building className="w-16 h-16 text-muted-foreground" />}
    >
      <SidebarProvider>
        <ConversationSidebar
          onSelectConversation={handleSelectConversation}
          onNewConversation={handleNewConversation}
        />
        <SidebarInset className="min-w-0 overflow-x-hidden">
      <div className="flex flex-col h-dvh bg-background">
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-3 border-b">
          <SettingsMobileSidebarToggle />
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8 shrink-0"
            onClick={() => navigate("/sernia-chat")}
          >
            <ArrowLeft className="w-4 h-4" />
          </Button>
          <div className="flex-1 min-w-0">
            <h1 className="text-lg font-semibold">Settings</h1>
          </div>
          <div className="flex items-center gap-2">
            {isDirty && (
              <Button variant="outline" size="sm" className="gap-1.5" onClick={handleReset}>
                <RotateCcw className="w-3.5 h-3.5" />
                Reset
              </Button>
            )}
            <Button
              size="sm"
              className="gap-1.5"
              disabled={saving || !isDirty}
              onClick={handleSave}
            >
              {saving ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Save className="w-3.5 h-3.5" />
              )}
              Save
            </Button>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto">
          {saved === null && !error ? (
            <div className="flex justify-center py-16">
              <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
            </div>
          ) : (
            <div className="max-w-2xl mx-auto p-4 space-y-6">
              {/* Status messages */}
              {error && (
                <div className="rounded-lg border border-red-300 bg-red-50 dark:bg-red-950/20 p-3 text-sm text-red-700 dark:text-red-400">
                  {error}
                </div>
              )}
              {success && (
                <div className="rounded-lg border border-green-300 bg-green-50 dark:bg-green-950/20 p-3 text-sm text-green-700 dark:text-green-400">
                  Settings saved. Schedule updated.
                </div>
              )}

              {/* Triggers enabled */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Triggers</CardTitle>
                  <CardDescription>
                    Universal kill switch for all automated agent runs (scheduled checks, SMS, email triggers).
                    Web chat and approvals are not affected.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="flex items-center gap-3">
                    <Switch
                      id="triggers-enabled"
                      checked={triggersEnabled}
                      onCheckedChange={setTriggersEnabled}
                    />
                    <Label htmlFor="triggers-enabled" className="cursor-pointer">
                      {triggersEnabled ? "Enabled" : "Disabled"}
                    </Label>
                  </div>
                </CardContent>
              </Card>

              {/* Schedule config */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Scheduled Checks</CardTitle>
                  <CardDescription>
                    Controls when the AI agent wakes up to check the inbox and perform routine tasks.
                    All times are in Eastern Time (ET).
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                  {/* Days of week */}
                  <div className="space-y-2">
                    <Label className="text-sm font-medium">Days of Week</Label>
                    <div className="flex flex-wrap gap-2">
                      {DAYS.map(({ value, label }) => {
                        const active = days.includes(value);
                        return (
                          <button
                            key={value}
                            type="button"
                            onClick={() => toggleDay(value)}
                            className={cn(
                              "inline-flex items-center justify-center rounded-md px-3 py-1.5 text-sm font-medium transition-colors border",
                              active
                                ? "bg-primary text-primary-foreground border-primary"
                                : "bg-background text-muted-foreground border-input hover:bg-accent hover:text-accent-foreground"
                            )}
                          >
                            {label}
                          </button>
                        );
                      })}
                    </div>
                    {days.length === 0 && (
                      <p className="text-xs text-destructive">At least one day must be selected.</p>
                    )}
                  </div>

                  {/* Hours */}
                  <div className="space-y-2">
                    <Label className="text-sm font-medium">Run Times (ET)</Label>
                    <p className="text-xs text-muted-foreground">
                      Select which hours the scheduled check should run at.
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      {HOUR_OPTIONS.map((h) => {
                        const active = hours.includes(h);
                        return (
                          <button
                            key={h}
                            type="button"
                            onClick={() => toggleHour(h)}
                            className={cn(
                              "inline-flex items-center justify-center rounded-md px-2 py-1 text-xs font-medium transition-colors border min-w-[52px]",
                              active
                                ? "bg-primary text-primary-foreground border-primary"
                                : "bg-background text-muted-foreground border-input hover:bg-accent hover:text-accent-foreground"
                            )}
                          >
                            {formatHour(h)}
                          </button>
                        );
                      })}
                    </div>
                    {hours.length === 0 && (
                      <p className="text-xs text-destructive">At least one time must be selected.</p>
                    )}
                  </div>

                  {/* Summary */}
                  {days.length > 0 && hours.length > 0 && (
                    <div className="rounded-lg bg-muted/50 p-3 space-y-1">
                      <p className="text-xs font-medium text-muted-foreground">Schedule preview</p>
                      <p className="text-sm">
                        Runs{" "}
                        <span className="font-medium">
                          {[...days]
                            .sort()
                            .map((d) => DAYS.find((x) => x.value === d)!.label)
                            .join(", ")}
                        </span>{" "}
                        at{" "}
                        <span className="font-medium">
                          {[...hours]
                            .sort()
                            .map(formatHour)
                            .join(", ")}
                        </span>{" "}
                        ET
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {hours.length} check{hours.length !== 1 && "s"} per day,{" "}
                        {days.length} day{days.length !== 1 && "s"} per week ({hours.length * days.length} total/week)
                      </p>
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>
          )}
        </div>
      </div>
        </SidebarInset>
      </SidebarProvider>
    </AuthGuard>
  );
}
