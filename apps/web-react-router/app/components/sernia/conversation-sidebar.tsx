"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { useAuth, useUser } from "@clerk/react-router";
import { cn } from "~/lib/utils";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Badge } from "~/components/ui/badge";
import { ScrollArea } from "~/components/ui/scroll-area";
import {
  Sidebar,
  SidebarContent,
  SidebarHeader,
  SidebarFooter,
  SidebarMenu,
  SidebarMenuItem,
  SidebarMenuButton,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarGroupContent,
  useSidebar,
} from "~/components/ui/sidebar";
import {
  Plus,
  Search,
  Phone,
  Mail,
  MessageSquare,
  Clock,
  Trash2,
  Loader2,
  Home,
  Settings,
  LayoutList,
  Building,
  X,
  Menu,
} from "lucide-react";

const API_BASE = "/api/sernia-ai";

export interface ConversationSummary {
  conversation_id: string;
  modality: string;
  preview: string;
  has_pending: boolean;
  trigger_source: string | null;
  trigger_contact_name: string | null;
  participant?: string;
  created_at: string | null;
  updated_at: string | null;
}

type SourceFilter = "all" | "web_chat" | "sms" | "email";

const SOURCE_FILTERS: { value: SourceFilter; label: string; icon: React.ReactNode }[] = [
  { value: "all", label: "All", icon: null },
  { value: "web_chat", label: "Chat", icon: <MessageSquare className="w-3 h-3" /> },
  { value: "sms", label: "SMS", icon: <Phone className="w-3 h-3" /> },
  { value: "email", label: "Email", icon: <Mail className="w-3 h-3" /> },
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
          conv.trigger_source === "zillow_email" && "text-blue-500"
        )}
      />
    );
  }
  return <MessageSquare className="w-3.5 h-3.5 text-muted-foreground" />;
}

function formatRelativeTime(dateString: string | null) {
  if (!dateString) return "";
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60_000);
  const diffHours = Math.floor(diffMs / 3_600_000);
  const diffDays = Math.floor(diffMs / 86_400_000);

  if (diffMins < 1) return "now";
  if (diffMins < 60) return `${diffMins}m`;
  if (diffHours < 24) return `${diffHours}h`;
  if (diffDays < 7) return `${diffDays}d`;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
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

// ---- Prefetch function (call early, outside component) ----
let prefetchPromise: Promise<ConversationSummary[]> | null = null;

export function prefetchConversations(getToken: () => Promise<string | null>) {
  if (getCachedConversations()) return;
  if (prefetchPromise) return;

  prefetchPromise = (async () => {
    try {
      const token = await getToken();
      const res = await fetch(`${API_BASE}/conversations/history?limit=30`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        const convos = data.conversations || [];
        setCachedConversations(convos);
        return convos;
      }
    } catch {
      // silent fail for prefetch
    } finally {
      prefetchPromise = null;
    }
    return [];
  })();
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
  const { user } = useUser();
  const navigate = useNavigate();
  const { state: sidebarState, toggleSidebar, isMobile } = useSidebar();
  const isAdmin =
    user?.primaryEmailAddress?.emailAddress === "emilio@serniacapital.com";

  const [conversations, setConversations] = useState<ConversationSummary[]>(
    () => getCachedConversations() || []
  );
  const [isLoading, setIsLoading] = useState(!getCachedConversations());
  const [searchQuery, setSearchQuery] = useState("");
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");
  const fetchRef = useRef(0);

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
          const convos = data.conversations || [];
          setConversations(convos);
          setCachedConversations(convos);
        }
      } catch {
        // keep existing data on error
      } finally {
        if (fetchId === fetchRef.current) setIsLoading(false);
      }
    },
    [isSignedIn, getToken]
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
    } else if (prefetchPromise) {
      // Wait for in-flight prefetch
      prefetchPromise.then((convos) => {
        if (convos && convos.length > 0) {
          setConversations(convos);
          setIsLoading(false);
        } else {
          fetchConversations();
        }
      });
    } else {
      fetchConversations();
    }
  }, [isSignedIn, fetchConversations]);

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
        sourceFilter === "sms"
          ? conv.trigger_source === "sms"
          : sourceFilter === "email"
            ? conv.trigger_source === "email" ||
              conv.trigger_source === "zillow_email"
            : false;
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
        setConversations((prev) =>
          prev.filter((c) => c.conversation_id !== convId)
        );
        onDeleteConversation?.(convId);
      }
    } catch {
      console.error("Failed to delete conversation");
    }
  };

  const handleSelect = (conv: ConversationSummary) => {
    onSelectConversation(conv.conversation_id, conv.modality);
    if (isMobile) toggleSidebar();
  };

  const handleNewConversation = () => {
    onNewConversation();
    if (isMobile) toggleSidebar();
  };

  const isCollapsed = sidebarState === "collapsed";

  return (
    <Sidebar collapsible="icon" className="border-r">
      <SidebarHeader className="border-b border-sidebar-border">
        <div className="flex items-center justify-between gap-1 px-1">
          {!isCollapsed && (
            <Link
              to="/"
              className="flex items-center gap-2 text-sm font-semibold hover:opacity-80 transition-opacity"
            >
              <Building className="w-4 h-4 text-primary" />
              <span>Sernia AI</span>
            </Link>
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
        {/* New conversation button */}
        <div className={cn("px-2 pt-2", isCollapsed && "px-1")}>
          <Button
            variant="outline"
            className={cn(
              "w-full gap-2 justify-start",
              isCollapsed && "justify-center px-0"
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
                autoComplete="off"
                autoCorrect="off"
                data-form-type="other"
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
                    sourceFilter === f.value
                      ? ""
                      : "text-muted-foreground"
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

        {/* Conversation list */}
        <ScrollArea className="flex-1 px-2 pt-1">
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
          ) : (
            <div className="space-y-0.5 pb-2">
              {filtered.map((conv) => {
                const isActive =
                  conv.conversation_id === activeConversationId;

                if (isCollapsed) {
                  return (
                    <SidebarMenu key={conv.conversation_id}>
                      <SidebarMenuItem>
                        <SidebarMenuButton
                          tooltip={conv.preview || "Empty conversation"}
                          isActive={isActive}
                          onClick={() => handleSelect(conv)}
                          className="h-8 w-8 p-0 justify-center"
                        >
                          {modalityIcon(conv)}
                        </SidebarMenuButton>
                      </SidebarMenuItem>
                    </SidebarMenu>
                  );
                }

                return (
                  <div
                    key={conv.conversation_id}
                    className={cn(
                      "group flex items-start gap-2 px-2 py-2 rounded-lg cursor-pointer transition-colors",
                      isActive
                        ? "bg-accent text-accent-foreground"
                        : "hover:bg-muted"
                    )}
                    onClick={() => handleSelect(conv)}
                  >
                    <div className="shrink-0 mt-0.5">
                      {modalityIcon(conv)}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1">
                        <span className="text-sm truncate flex-1">
                          {conv.preview || "Empty conversation"}
                        </span>
                        {conv.has_pending && (
                          <Badge
                            variant="outline"
                            className="text-[10px] px-1 py-0 h-4 text-amber-600 border-amber-300 shrink-0"
                          >
                            Pending
                          </Badge>
                        )}
                      </div>
                      <div className="flex items-center gap-1 text-xs text-muted-foreground mt-0.5">
                        {conv.trigger_contact_name && (
                          <>
                            <span className="truncate">
                              {conv.trigger_contact_name}
                            </span>
                            <span>&middot;</span>
                          </>
                        )}
                        {conv.participant && !conv.trigger_contact_name && (
                          <>
                            <span className="truncate">{conv.participant}</span>
                            <span>&middot;</span>
                          </>
                        )}
                        <span className="shrink-0">
                          {formatRelativeTime(conv.updated_at)}
                        </span>
                      </div>
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6 opacity-0 group-hover:opacity-100 transition-opacity shrink-0 text-muted-foreground hover:text-destructive"
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
        </ScrollArea>
      </SidebarContent>

      {/* Footer with nav links */}
      <SidebarFooter className="border-t border-sidebar-border">
        <SidebarMenu>
          {isAdmin && (
            <>
              <SidebarMenuItem>
                <SidebarMenuButton
                  tooltip="All Conversations"
                  onClick={() => {
                    navigate("/sernia-admin");
                    if (isMobile) toggleSidebar();
                  }}
                >
                  <LayoutList className="w-4 h-4" />
                  {!isCollapsed && <span>All Conversations</span>}
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton
                  tooltip="Settings"
                  onClick={() => {
                    navigate("/sernia-settings");
                    if (isMobile) toggleSidebar();
                  }}
                >
                  <Settings className="w-4 h-4" />
                  {!isCollapsed && <span>Settings</span>}
                </SidebarMenuButton>
              </SidebarMenuItem>
            </>
          )}
          <SidebarMenuItem>
            <SidebarMenuButton asChild tooltip="Home">
              <Link to="/">
                <Home className="w-4 h-4" />
                {!isCollapsed && <span>Portfolio Home</span>}
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
    </Sidebar>
  );
}
