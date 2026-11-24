"use client";

import { useState, useEffect, useCallback, useRef, useSyncExternalStore } from "react";
import { useAuth } from "@clerk/react-router";
import { cn } from "~/lib/utils";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Badge } from "~/components/ui/badge";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarHeader,
  SidebarFooter,
  SidebarMenu,
  SidebarMenuItem,
  SidebarMenuButton,
  SidebarSeparator,
  useSidebar,
} from "~/components/ui/sidebar";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "~/components/ui/collapsible";
import { useTeamCounts } from "~/sites/team/components/team-counts";
import { TeamAccount, TeamNavItems } from "~/sites/team/components/team-nav";
import { TeamWordmark } from "~/sites/team/components/team-wordmark";
import {
  Plus,
  Search,
  Phone,
  Mail,
  MessageSquare,
  Clock,
  Trash2,
  Loader2,
  X,
  Menu,
  ChevronDown,
  ChevronRight,
} from "lucide-react";

const API_BASE = "/api/sernia-ai";

export type { ConversationSummary } from "~/sites/team/lib/sernia-conversations";
import {
  type ConversationSummary,
  CONVERSATIONS_CHANGED,
  mergeConversationSummaries,
  acceptConversationSummaries,
} from "~/sites/team/lib/sernia-conversations";

type SourceFilter = "all" | "web_chat" | "sms";

const SOURCE_FILTERS: {
  value: SourceFilter;
  label: string;
  icon: React.ReactNode;
}[] = [
  { value: "all", label: "All", icon: null },
  {
    value: "web_chat",
    label: "Chat",
    icon: <MessageSquare className="w-3 h-3" />,
  },
  { value: "sms", label: "SMS", icon: <Phone className="w-3 h-3" /> },
];

function modalityIcon(conv: ConversationSummary) {
  if (conv.trigger_source === "sms" || conv.modality === "sms") {
    return <Phone className="w-3.5 h-3.5 text-muted-foreground" />;
  }
  if (
    conv.trigger_source === "email" ||
    conv.trigger_source === "zillow_email" ||
    conv.modality === "email"
  ) {
    return (
      <Mail
        className={cn(
          "w-3.5 h-3.5 text-muted-foreground",
          conv.trigger_source === "zillow_email" && "text-blue-500",
        )}
      />
    );
  }
  return <MessageSquare className="w-3.5 h-3.5 text-muted-foreground" />;
}

function formatTimeOfDay(dateString: string | null): string {
  if (!dateString) return "";
  return new Date(dateString).toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
  });
}

function localDateKey(d: Date): string {
  // YYYY-MM-DD in local time, used as a stable group key.
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function formatGroupLabel(date: Date): string {
  const midnight = (d: Date) => {
    const c = new Date(d);
    c.setHours(0, 0, 0, 0);
    return c;
  };
  const diffDays = Math.round(
    (midnight(new Date()).getTime() - midnight(date).getTime()) / 86_400_000,
  );
  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}

interface ConversationGroup {
  key: string;
  label: string;
  items: ConversationSummary[];
}

function groupConversationsByDate(
  convos: ConversationSummary[],
): ConversationGroup[] {
  const map = new Map<string, ConversationGroup>();
  for (const c of convos) {
    const ts = c.updated_at || c.created_at;
    if (!ts) continue;
    const d = new Date(ts);
    const key = localDateKey(d);
    let group = map.get(key);
    if (!group) {
      group = { key, label: formatGroupLabel(d), items: [] };
      map.set(key, group);
    }
    group.items.push(c);
  }
  return Array.from(map.values()).sort((a, b) => (a.key < b.key ? 1 : -1));
}

// ---- Cache for page 1 ----
let cachedConversations: ConversationSummary[] | null = null;
let cacheTimestamp = 0;
const CACHE_TTL = 30_000; // 30 seconds

function getCachedConversations(): ConversationSummary[] | null {
  if (cachedConversations && Date.now() - cacheTimestamp < CACHE_TTL) {
    return cachedConversations;
  }
  return null;
}

function setCachedConversations(convos: ConversationSummary[]) {
  cachedConversations = convos;
  cacheTimestamp = Date.now();
}

// ---- Whether the team's pages are open ----

// Open at first, on a phone too; the person's choice is kept in this browser.
// Read through useSyncExternalStore so a later page opens it as left, and the
// server (which has no storage) renders it open.
const TEAM_PAGES_OPEN_KEY = "sernia-team-pages-open";
let teamPagesChoice: boolean | null = null;
const teamPagesListeners = new Set<() => void>();

function teamPagesOpen(): boolean {
  // This page's own choice first, for a browser whose storage is blocked.
  if (teamPagesChoice !== null) return teamPagesChoice;
  try {
    return localStorage.getItem(TEAM_PAGES_OPEN_KEY) !== "false";
  } catch {
    return true;
  }
}

function setTeamPagesOpen(open: boolean) {
  teamPagesChoice = open;
  try {
    localStorage.setItem(TEAM_PAGES_OPEN_KEY, String(open));
  } catch {
    // Storage is blocked: the choice lasts until the page is reloaded.
  }
  teamPagesListeners.forEach((listener) => listener());
}

function subscribeToTeamPagesOpen(listener: () => void) {
  teamPagesListeners.add(listener);
  return () => {
    teamPagesListeners.delete(listener);
  };
}

// ---- Main component ----

interface ConversationSidebarProps {
  activeConversationId?: string;
  onSelectConversation: (convId: string, modality?: string) => void;
  onNewConversation: () => void;
  onDeleteConversation?: (convId: string) => void;
}

export function ConversationSidebar({
  activeConversationId,
  onSelectConversation,
  onNewConversation,
  onDeleteConversation,
}: ConversationSidebarProps) {
  const { isSignedIn, getToken } = useAuth();
  const { state: sidebarState, toggleSidebar, isMobile } = useSidebar();
  // The team's pages, above the conversations as on every other team page.
  const pagesOpen = useSyncExternalStore(
    subscribeToTeamPagesOpen,
    teamPagesOpen,
    () => true,
  );

  const [conversations, setConversations] = useState<ConversationSummary[]>(
    () => getCachedConversations() || [],
  );
  const [isLoading, setIsLoading] = useState(!getCachedConversations());
  const [searchQuery, setSearchQuery] = useState("");
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");
  const [collapsedGroups, setCollapsedGroups] = useState<
    Record<string, boolean>
  >({});
  const fetchRef = useRef(0);
  // The history list does not say which conversations wait for an approval;
  // the pending list behind the navigation's badge does.
  const { approvals } = useTeamCounts();
  const awaitingApproval = new Set(
    approvals.state === "loaded" ? approvals.value.ids : [],
  );

  const fetchConversations = useCallback(
    async (opts?: { silent?: boolean }) => {
      if (!isSignedIn) return;
      const fetchId = ++fetchRef.current;
      if (!opts?.silent) setIsLoading(true);

      try {
        const token = await getToken();
        const res = await fetch(`${API_BASE}/conversations/history?limit=50`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok && fetchId === fetchRef.current) {
          const data = await res.json();
          if (fetchId !== fetchRef.current) return;
          const convos = acceptConversationSummaries(data.conversations || []);
          setConversations(convos);
          setCachedConversations(convos);
        }
      } catch {
        // keep existing data on error
      } finally {
        if (fetchId === fetchRef.current) setIsLoading(false);
      }
    },
    [isSignedIn, getToken],
  );

  // Initial load: use cache or wait for prefetch, then fetch fresh
  useEffect(() => {
    if (!isSignedIn) return;

    const cached = getCachedConversations();
    if (cached) {
      setConversations(cached);
      setIsLoading(false);
      // Still refresh in background
      fetchConversations({ silent: true });
    } else {
      fetchConversations();
    }
  }, [isSignedIn, fetchConversations]);

  // Local submissions appear synchronously; receipt/completion refresh the DB copy.
  useEffect(() => {
    const refresh = () => {
      setConversations((previous) => mergeConversationSummaries(previous));
      void fetchConversations({ silent: true });
    };
    const push = (event: MessageEvent) => {
      if (["response-ready", "notification-click"].includes(event.data?.type))
        refresh();
    };
    window.addEventListener(CONVERSATIONS_CHANGED, refresh);
    navigator.serviceWorker?.addEventListener("message", push);
    return () => {
      fetchRef.current += 1;
      window.removeEventListener(CONVERSATIONS_CHANGED, refresh);
      navigator.serviceWorker?.removeEventListener("message", push);
    };
  }, [fetchConversations]);

  // Keep detached runs current while the user is reading another conversation.
  useEffect(() => {
    if (
      !conversations.some(
        (conversation) => conversation.run?.status === "running",
      )
    )
      return;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible")
        void fetchConversations({ silent: true });
    }, 3000);
    return () => clearInterval(timer);
  }, [conversations, fetchConversations]);

  // Refresh when page becomes visible
  useEffect(() => {
    const handleVisibility = () => {
      if (document.visibilityState === "visible" && isSignedIn) {
        fetchConversations({ silent: true });
      }
    };
    document.addEventListener("visibilitychange", handleVisibility);
    return () =>
      document.removeEventListener("visibilitychange", handleVisibility);
  }, [isSignedIn, fetchConversations]);

  // Filter conversations
  const filtered = conversations.filter((conv) => {
    if (sourceFilter !== "all") {
      const matchesModality = conv.modality === sourceFilter;
      const matchesTrigger =
        sourceFilter === "sms" ? conv.trigger_source === "sms" : false;
      if (!matchesModality && !matchesTrigger) return false;
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      const matchesPreview = conv.preview?.toLowerCase().includes(q);
      const matchesContact = conv.trigger_contact_name
        ?.toLowerCase()
        .includes(q);
      const matchesParticipant = conv.participant?.toLowerCase().includes(q);
      if (!matchesPreview && !matchesContact && !matchesParticipant)
        return false;
    }
    return true;
  });

  const handleDelete = async (convId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm("Delete this conversation?")) return;

    try {
      const token = await getToken();
      const res = await fetch(`${API_BASE}/conversation/${convId}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        setConversations((prev) => {
          const next = prev.filter((c) => c.conversation_id !== convId);
          setCachedConversations(next);
          return next;
        });
        onDeleteConversation?.(convId);
      } else {
        alert(
          "This conversation could not be deleted. Cancel any running response first.",
        );
      }
    } catch {
      console.error("Failed to delete conversation");
    }
  };

  const handleSelect = (conv: ConversationSummary) => {
    onSelectConversation(conv.conversation_id, conv.modality);
    if (isMobile) toggleSidebar();
  };

  const groupedConversations = groupConversationsByDate(filtered);
  const todayKey = localDateKey(new Date());
  const yesterdayDate = new Date();
  yesterdayDate.setDate(yesterdayDate.getDate() - 1);
  const yesterdayKey = localDateKey(yesterdayDate);

  const isGroupCollapsed = (key: string): boolean => {
    // Explicit user toggle takes precedence.
    if (collapsedGroups[key] !== undefined) return collapsedGroups[key];
    // Default expansion: today, yesterday, and any group containing the
    // active conversation (so deep-linking to an older convo shows it).
    if (key === todayKey || key === yesterdayKey) return false;
    const containsActive = groupedConversations
      .find((g) => g.key === key)
      ?.items.some((c) => c.conversation_id === activeConversationId);
    return !containsActive;
  };

  const toggleGroup = (key: string) => {
    setCollapsedGroups((prev) => ({ ...prev, [key]: !isGroupCollapsed(key) }));
  };

  const handleNewConversation = () => {
    onNewConversation();
    if (isMobile) toggleSidebar();
  };

  // Icons only on a computer; a phone's sidebar always opens in full.
  const isCollapsed = sidebarState === "collapsed" && !isMobile;

  return (
    <Sidebar collapsible="icon" className="border-r">
      <SidebarHeader className="border-b border-sidebar-border">
        <div className="flex items-center justify-between gap-1 px-1">
          {!isCollapsed && (
            <TeamWordmark linked className="text-sm [&_svg]:size-6" />
          )}
          <Button
            variant="ghost"
            size="icon"
            onClick={toggleSidebar}
            className="h-8 w-8 shrink-0"
          >
            <Menu className="h-4 w-4" />
            <span className="sr-only">Toggle sidebar</span>
          </Button>
        </div>
      </SidebarHeader>

      <SidebarContent>
        {/* The team's pages, where every other team page has them */}
        <SidebarGroup className="pb-0">
          {isCollapsed ? (
            <TeamNavItems />
          ) : (
            <Collapsible open={pagesOpen} onOpenChange={setTeamPagesOpen}>
              <CollapsibleTrigger className="flex h-8 w-full items-center gap-1 rounded-md px-2 text-xs font-medium text-sidebar-foreground/70 outline-none ring-sidebar-ring hover:text-sidebar-foreground focus-visible:ring-2">
                {pagesOpen ? (
                  <ChevronDown className="w-3 h-3 shrink-0" />
                ) : (
                  <ChevronRight className="w-3 h-3 shrink-0" />
                )}
                Team pages
              </CollapsibleTrigger>
              <CollapsibleContent>
                <TeamNavItems />
              </CollapsibleContent>
            </Collapsible>
          )}
        </SidebarGroup>

        <SidebarSeparator />

        {/* The conversations: a new one, then the list */}
        <div className={cn("px-2", isCollapsed && "px-1")}>
          {!isCollapsed && (
            <p className="flex h-8 items-center px-2 text-xs font-medium text-sidebar-foreground/70">
              Conversations
            </p>
          )}
          <Button
            variant="outline"
            className={cn(
              "w-full gap-2 justify-start",
              isCollapsed && "justify-center px-0",
            )}
            onClick={handleNewConversation}
          >
            <Plus className="w-4 h-4 shrink-0" />
            {!isCollapsed && <span>New chat</span>}
          </Button>
        </div>

        {/* Search and filters — hidden when collapsed */}
        {!isCollapsed && (
          <div className="px-2 pt-2 space-y-2">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
              <Input
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search conversations..."
                className="h-8 pl-8 pr-8 text-sm"
              />
              {searchQuery && (
                <button
                  onClick={() => setSearchQuery("")}
                  className="absolute right-2 top-1/2 -translate-y-1/2"
                >
                  <X className="w-3.5 h-3.5 text-muted-foreground hover:text-foreground" />
                </button>
              )}
            </div>

            <div className="flex gap-1">
              {SOURCE_FILTERS.map((f) => (
                <Button
                  key={f.value}
                  variant={sourceFilter === f.value ? "default" : "ghost"}
                  size="sm"
                  className={cn(
                    "h-7 text-xs px-2 gap-1",
                    sourceFilter === f.value ? "" : "text-muted-foreground",
                  )}
                  onClick={() => setSourceFilter(f.value)}
                >
                  {f.icon}
                  {f.label}
                </Button>
              ))}
            </div>
          </div>
        )}

        {/* Conversation list. It keeps some height when the team's pages
            are open on a short screen; the sidebar then scrolls as a whole. */}
        <div
          className={cn(
            "flex-1 overflow-y-auto overflow-x-hidden px-2 pt-1",
            isCollapsed ? "min-h-0" : "min-h-48",
          )}
        >
          {isLoading && conversations.length === 0 ? (
            <div className="flex justify-center py-8">
              <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
            </div>
          ) : filtered.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-8">
              {searchQuery || sourceFilter !== "all"
                ? "No matching conversations"
                : "No conversations yet"}
            </p>
          ) : isCollapsed ? (
            <div className="space-y-0.5 pb-2">
              {filtered.map((conv) => {
                const isActive = conv.conversation_id === activeConversationId;
                return (
                  <SidebarMenu key={conv.conversation_id}>
                    <SidebarMenuItem>
                      <SidebarMenuButton
                        tooltip={conv.preview || "Empty conversation"}
                        isActive={isActive}
                        onClick={() => handleSelect(conv)}
                        className="h-8 w-8 p-0 justify-center"
                      >
                        {conv.run?.status === "running" ? (
                          <Loader2
                            aria-label="Running"
                            className="w-3.5 h-3.5 animate-spin"
                          />
                        ) : (
                          modalityIcon(conv)
                        )}
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  </SidebarMenu>
                );
              })}
            </div>
          ) : (
            <div className="space-y-2 pb-2">
              {groupedConversations.map((group) => {
                const collapsedGroup = isGroupCollapsed(group.key);
                return (
                  <div key={group.key}>
                    <button
                      type="button"
                      onClick={() => toggleGroup(group.key)}
                      className="flex items-center gap-1 w-full px-2 py-1 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
                    >
                      {collapsedGroup ? (
                        <ChevronRight className="w-3 h-3 shrink-0" />
                      ) : (
                        <ChevronDown className="w-3 h-3 shrink-0" />
                      )}
                      <span className="flex-1 text-left">{group.label}</span>
                      <span className="text-[10px] tabular-nums opacity-70">
                        {group.items.length}
                      </span>
                    </button>
                    {!collapsedGroup && (
                      <div className="space-y-0.5">
                        {group.items.map((conv) => {
                          const isActive =
                            conv.conversation_id === activeConversationId;
                          return (
                            <div
                              key={conv.conversation_id}
                              className={cn(
                                "group flex items-start gap-2 px-2 py-2 rounded-lg cursor-pointer transition-colors",
                                isActive
                                  ? "bg-accent text-accent-foreground"
                                  : "hover:bg-muted",
                              )}
                              onClick={() => handleSelect(conv)}
                            >
                              <div className="shrink-0 mt-0.5">
                                {conv.run?.status === "running" ? (
                                  <Loader2
                                    aria-label="Running"
                                    className="w-3.5 h-3.5 animate-spin"
                                  />
                                ) : (
                                  modalityIcon(conv)
                                )}
                              </div>
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-1">
                                  <span className="text-sm truncate flex-1">
                                    {conv.preview || "Empty conversation"}
                                  </span>
                                  {(conv.has_pending ||
                                    awaitingApproval.has(
                                      conv.conversation_id,
                                    )) && (
                                    <Badge
                                      variant="outline"
                                      className="text-[10px] px-1 py-0 h-4 text-amber-600 border-amber-300 shrink-0"
                                    >
                                      To approve
                                    </Badge>
                                  )}
                                </div>
                                <div className="flex items-center gap-1 text-xs text-muted-foreground mt-0.5">
                                  <span className="shrink-0 tabular-nums">
                                    {formatTimeOfDay(conv.updated_at)}
                                  </span>
                                  {conv.trigger_contact_name && (
                                    <>
                                      <span>&middot;</span>
                                      <span className="truncate">
                                        {conv.trigger_contact_name}
                                      </span>
                                    </>
                                  )}
                                  {conv.participant &&
                                    !conv.trigger_contact_name && (
                                      <>
                                        <span>&middot;</span>
                                        <span className="truncate">
                                          {conv.participant}
                                        </span>
                                      </>
                                    )}
                                </div>
                              </div>
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-6 w-6 opacity-0 group-hover:opacity-100 transition-opacity shrink-0 text-muted-foreground hover:text-destructive"
                                disabled={conv.run?.status === "running"}
                                aria-label="Delete conversation"
                                onClick={(e) =>
                                  handleDelete(conv.conversation_id, e)
                                }
                              >
                                <Trash2 className="w-3 h-3" />
                              </Button>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </SidebarContent>

      {/* Notifications, the public site and the account */}
      <SidebarFooter className="border-t border-sidebar-border">
        <TeamAccount />
      </SidebarFooter>
    </Sidebar>
  );
}
