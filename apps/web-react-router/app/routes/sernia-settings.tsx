import type { Route } from "./+types/sernia-settings";
import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router";
import { useAuth } from "@clerk/react-router";
import { AuthGuard } from "~/components/auth-guard";
import { Button } from "~/components/ui/button";
import { Switch } from "~/components/ui/switch";
import { Input } from "~/components/ui/input";
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

interface ModelChoice {
  key: string;
  label: string;
  provider: string;
  cost_note: string | null;
}

type ThinkingEffort = "low" | "medium" | "high";

interface ModelConfig {
  model_key: string;
  thinking_effort: ThinkingEffort;
}

const EFFORT_OPTIONS: { value: ThinkingEffort; label: string; hint: string }[] = [
  { value: "low", label: "Low", hint: "Fast, minimal thinking. Skips simple queries." },
  { value: "medium", label: "Medium", hint: "Balanced. Sensible default for chat." },
  { value: "high", label: "High", hint: "Deeper reasoning. Slower, more tokens." },
];

const DEFAULT_EFFORT: ThinkingEffort = "medium";

interface ZillowEmailConfig {
  debounce_seconds: number;
  require_approval: boolean;
}

interface Settings {
  triggers_enabled: boolean;
  schedule_config: ScheduleConfig;
  model_config: ModelConfig;
  zillow_email_config: ZillowEmailConfig;
  available_models: ModelChoice[];
}

const DEFAULT_MODEL_KEY = "gpt-5.4";
const FALLBACK_MODELS: ModelChoice[] = [
  { key: "gpt-5.4", label: "GPT-5.4", provider: "openai", cost_note: null },
  { key: "sonnet-4-6", label: "Claude Sonnet 4.6", provider: "anthropic", cost_note: null },
  { key: "opus-4-7", label: "Claude Opus 4.7", provider: "anthropic", cost_note: "~5x Sonnet pricing — use sparingly." },
];

// Zillow defaults — mirror api/src/sernia_ai/config.py constants. These are
// only used as the initial form values before the GET /admin/settings response
// arrives; the server response (which reflects the DB row or its own defaults)
// always wins.
const DEFAULT_ZILLOW_DEBOUNCE_SECONDS = 300;
const DEFAULT_ZILLOW_REQUIRE_APPROVAL = true;

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
  const [modelKey, setModelKey] = useState<string>(DEFAULT_MODEL_KEY);
  const [thinkingEffort, setThinkingEffort] = useState<ThinkingEffort>(DEFAULT_EFFORT);
  const [availableModels, setAvailableModels] = useState<ModelChoice[]>(FALLBACK_MODELS);
  const [zillowDebounceSeconds, setZillowDebounceSeconds] = useState<number>(
    DEFAULT_ZILLOW_DEBOUNCE_SECONDS,
  );
  const [zillowRequireApproval, setZillowRequireApproval] = useState<boolean>(
    DEFAULT_ZILLOW_REQUIRE_APPROVAL,
  );

  // Snapshot of last-saved values to detect dirty state
  const [saved, setSaved] = useState<Settings | null>(null);

  const isDirty =
    saved !== null &&
    (triggersEnabled !== saved.triggers_enabled ||
      JSON.stringify([...days].sort()) !== JSON.stringify([...saved.schedule_config.days_of_week].sort()) ||
      JSON.stringify([...hours].sort()) !== JSON.stringify([...saved.schedule_config.hours].sort()) ||
      modelKey !== saved.model_config.model_key ||
      thinkingEffort !== saved.model_config.thinking_effort ||
      zillowDebounceSeconds !== saved.zillow_email_config.debounce_seconds ||
      zillowRequireApproval !== saved.zillow_email_config.require_approval);

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
      setModelKey(data.model_config?.model_key ?? DEFAULT_MODEL_KEY);
      setThinkingEffort(data.model_config?.thinking_effort ?? DEFAULT_EFFORT);
      setZillowDebounceSeconds(
        data.zillow_email_config?.debounce_seconds ?? DEFAULT_ZILLOW_DEBOUNCE_SECONDS,
      );
      setZillowRequireApproval(
        data.zillow_email_config?.require_approval ?? DEFAULT_ZILLOW_REQUIRE_APPROVAL,
      );
      if (data.available_models?.length) setAvailableModels(data.available_models);
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
          model_config: { model_key: modelKey, thinking_effort: thinkingEffort },
          zillow_email_config: {
            debounce_seconds: zillowDebounceSeconds,
            require_approval: zillowRequireApproval,
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
        model_config: { model_key: modelKey, thinking_effort: thinkingEffort },
        zillow_email_config: {
          debounce_seconds: zillowDebounceSeconds,
          require_approval: zillowRequireApproval,
        },
        available_models: availableModels,
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
    setModelKey(saved.model_config.model_key);
    setThinkingEffort(saved.model_config.thinking_effort);
    setZillowDebounceSeconds(saved.zillow_email_config.debounce_seconds);
    setZillowRequireApproval(saved.zillow_email_config.require_approval);
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
      <div className="flex flex-col h-chat-viewport bg-background">
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

              {/* Model selector */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Model</CardTitle>
                  <CardDescription>
                    Which LLM the agent uses for every run (web chat, SMS, scheduled checks, approvals).
                    Anthropic models also enable the builtin web_fetch tool; OpenAI does not.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="flex flex-wrap gap-2">
                    {availableModels.map((m) => {
                      const active = modelKey === m.key;
                      return (
                        <button
                          key={m.key}
                          type="button"
                          onClick={() => setModelKey(m.key)}
                          className={cn(
                            "inline-flex items-center justify-center rounded-md px-3 py-1.5 text-sm font-medium transition-colors border",
                            active
                              ? "bg-primary text-primary-foreground border-primary"
                              : "bg-background text-muted-foreground border-input hover:bg-accent hover:text-accent-foreground"
                          )}
                        >
                          {m.label}
                        </button>
                      );
                    })}
                  </div>
                  {(() => {
                    const selected = availableModels.find((m) => m.key === modelKey);
                    if (!selected?.cost_note) return null;
                    return (
                      <p className="text-xs text-muted-foreground">
                        <span className="font-medium">Heads up:</span> {selected.cost_note}
                      </p>
                    );
                  })()}
                  <div className="space-y-2 pt-2">
                    <Label className="text-sm font-medium">Thinking Effort</Label>
                    <p className="text-xs text-muted-foreground">
                      Controls reasoning depth. Anthropic models use adaptive thinking — Claude
                      decides per request whether and how much to think. OpenAI maps this to
                      reasoning effort.
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {EFFORT_OPTIONS.map((opt) => {
                        const active = thinkingEffort === opt.value;
                        return (
                          <button
                            key={opt.value}
                            type="button"
                            onClick={() => setThinkingEffort(opt.value)}
                            className={cn(
                              "inline-flex items-center justify-center rounded-md px-3 py-1.5 text-sm font-medium transition-colors border",
                              active
                                ? "bg-primary text-primary-foreground border-primary"
                                : "bg-background text-muted-foreground border-input hover:bg-accent hover:text-accent-foreground"
                            )}
                          >
                            {opt.label}
                          </button>
                        );
                      })}
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {EFFORT_OPTIONS.find((o) => o.value === thinkingEffort)?.hint}
                    </p>
                  </div>
                </CardContent>
              </Card>

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
                  </div>

                  {/* Summary */}
                  <div className="rounded-lg bg-muted/50 p-3 space-y-1">
                    <p className="text-xs font-medium text-muted-foreground">Schedule preview</p>
                    {days.length === 0 || hours.length === 0 ? (
                      <p className="text-sm text-muted-foreground">
                        No scheduled checks — the agent will not wake up on a schedule.
                      </p>
                    ) : (
                      <>
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
                      </>
                    )}
                  </div>
                </CardContent>
              </Card>

              {/* Zillow email trigger config */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Zillow Email Auto-Reply</CardTitle>
                  <CardDescription>
                    Controls the Zillow lead trigger that drafts and sends replies to inbound
                    Zillow inquiry emails.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                  {/* Debounce */}
                  <div className="space-y-2">
                    <Label htmlFor="zillow-debounce" className="text-sm font-medium">
                      Debounce Window (seconds)
                    </Label>
                    <p className="text-xs text-muted-foreground">
                      First Zillow email starts a timer; additional Zillow emails arriving within
                      this window are batched into a single agent run. 60–3600 seconds.
                    </p>
                    <Input
                      id="zillow-debounce"
                      type="number"
                      min={60}
                      max={3600}
                      step={30}
                      value={zillowDebounceSeconds}
                      onChange={(e) =>
                        setZillowDebounceSeconds(Number(e.target.value) || 0)
                      }
                      className="max-w-40"
                    />
                    <p className="text-xs text-muted-foreground">
                      ≈ {(zillowDebounceSeconds / 60).toFixed(zillowDebounceSeconds % 60 === 0 ? 0 : 1)} minute
                      {zillowDebounceSeconds === 60 ? "" : "s"}
                    </p>
                  </div>

                  {/* Require approval */}
                  <div className="space-y-2">
                    <Label className="text-sm font-medium">Require HITL Approval</Label>
                    <p className="text-xs text-muted-foreground">
                      When on, every outbound Zillow reply pauses for human approval (the standard
                      external-email HITL card). Turn off only once the agent has earned trust to
                      auto-send Zillow replies — flip back on if it goes off the rails.
                    </p>
                    <div className="flex items-center gap-3 pt-1">
                      <Switch
                        id="zillow-require-approval"
                        checked={zillowRequireApproval}
                        onCheckedChange={setZillowRequireApproval}
                      />
                      <Label htmlFor="zillow-require-approval" className="cursor-pointer">
                        {zillowRequireApproval ? "Approval required" : "Auto-send (no approval)"}
                      </Label>
                    </div>
                    {!zillowRequireApproval && (
                      <p className="text-xs text-amber-600 dark:text-amber-400">
                        Heads up: with approval off, the agent will send Zillow replies without
                        the per-email approval card.
                      </p>
                    )}
                  </div>
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
