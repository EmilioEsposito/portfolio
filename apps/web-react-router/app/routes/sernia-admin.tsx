import type { Route } from "./+types/sernia-admin";
import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router";
import { useAuth } from "@clerk/react-router";
import { AuthGuard } from "~/components/auth-guard";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Badge } from "~/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "~/components/ui/table";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "~/components/ui/sheet";
import { cn } from "~/lib/utils";
import { Markdown } from "~/components/markdown";
import { ToolResultCard } from "~/components/chat/tool-cards";
import { processMessage } from "~/components/chat/process-message";
import { FileMessageDisplay } from "~/components/chat/file-message-display";
import {
  Building,
  Loader2,
  RefreshCw,
  Trash2,
  Phone,
  Mail,
  MessageSquare,
  ExternalLink,
  ChevronLeft,
  ChevronRight,
  ArrowLeft,
} from "lucide-react";

const API_BASE = "/api/sernia-ai";
const PAGE_SIZE = 30;

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Sernia Admin — Conversations" },
    {
      name: "description",
      content: "Browse and inspect all Sernia AI conversations.",
    },
  ];
}

interface ConversationSummary {
  conversation_id: string;
  agent_name: string;
  clerk_user_id: string | null;
  user_email: string | null;
  modality: string;
  preview: string;
  estimated_tokens: number;
  contact_identifier: string | null;
  has_pending: boolean;
  trigger_source: string | null;
  trigger_contact_name: string | null;
  created_at: string | null;
  updated_at: string | null;
}

const MODALITY_OPTIONS: {
  value: string | null;
  label: string;
  icon?: typeof MessageSquare;
}[] = [
  { value: null, label: "All" },
  { value: "web_chat", label: "Web Chat", icon: MessageSquare },
  { value: "sms", label: "SMS", icon: Phone },
  { value: "email", label: "Email", icon: Mail },
];

function modalityIcon(modality: string) {
  switch (modality) {
    case "sms":
      return <Phone className="w-3.5 h-3.5" />;
    case "email":
      return <Mail className="w-3.5 h-3.5" />;
    default:
      return <MessageSquare className="w-3.5 h-3.5" />;
  }
}

function formatDateTime(dateString: string | null) {
  if (!dateString) return "";
  const date = new Date(dateString);
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatTokens(tokens: number) {
  if (tokens === 0) return "—";
  if (tokens >= 1000) return `${(tokens / 1000).toFixed(1)}k`;
  return String(tokens);
}

// ---------------------------------------------------------------------------
// Detail Drawer — shows full message thread for a conversation
// ---------------------------------------------------------------------------

function ConversationDetail({
  conversationId,
  getToken,
  onDelete,
}: {
  conversationId: string;
  getToken: () => Promise<string | null>;
  onDelete: () => void;
}) {
  const navigate = useNavigate();
  const [messages, setMessages] = useState<any[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setMessages(null);
      setError(null);
      try {
        const token = await getToken();
        const res = await fetch(
          `${API_BASE}/conversation/${conversationId}/messages`,
          { headers: { Authorization: `Bearer ${token}` } }
        );
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        const data = await res.json();
        if (!cancelled) setMessages(data.messages || []);
      } catch (err) {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "Failed to load");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [conversationId, getToken]);

  const handleDelete = async () => {
    if (!confirm("Delete this conversation?")) return;
    try {
      const token = await getToken();
      const res = await fetch(`${API_BASE}/conversation/${conversationId}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) onDelete();
    } catch (err) {
      console.error("Failed to delete:", err);
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Actions */}
      <div className="flex items-center gap-2 px-4 py-3 border-b">
        <Button
          variant="outline"
          size="sm"
          className="gap-1.5"
          onClick={() => navigate(`/sernia-chat?id=${conversationId}`)}
        >
          <ExternalLink className="w-3.5 h-3.5" />
          Open in Chat
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="gap-1.5 text-destructive hover:text-destructive"
          onClick={handleDelete}
        >
          <Trash2 className="w-3.5 h-3.5" />
          Delete
        </Button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {error && (
          <div className="rounded-lg border border-red-300 bg-red-50 dark:bg-red-950/20 p-4 text-sm text-red-700 dark:text-red-400">
            {error}
          </div>
        )}

        {messages === null && !error && (
          <div className="flex justify-center py-12">
            <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
          </div>
        )}

        {messages?.length === 0 && (
          <p className="text-sm text-muted-foreground text-center py-8">
            No messages
          </p>
        )}

        {messages?.map((message, index) => {
          const { segments } = processMessage(message);

          return (
            <div
              key={message.id || index}
              className={cn(
                "flex gap-3",
                message.role === "user" ? "justify-end" : "justify-start"
              )}
            >
              {message.role === "assistant" && (
                <div className="shrink-0">
                  <div className="w-7 h-7 rounded-full bg-primary flex items-center justify-center">
                    <Building className="w-3.5 h-3.5 text-primary-foreground" />
                  </div>
                </div>
              )}

              <div
                className={cn(
                  "flex flex-col gap-2 max-w-[90%] min-w-0",
                  message.role === "user" && "items-end"
                )}
              >
                {message.role === "user" ? (
                  <>
                    <FileMessageDisplay
                      files={
                        segments.filter((s) => s.type === "file") as any
                      }
                    />
                    {segments.some((s) => s.type === "text") && (
                      <div className="bg-primary text-primary-foreground rounded-2xl px-3 py-2 shadow-sm">
                        <p className="text-sm whitespace-pre-wrap">
                          {segments.find((s) => s.type === "text")?.type ===
                          "text"
                            ? (
                                segments.find((s) => s.type === "text") as any
                              ).content
                            : ""}
                        </p>
                      </div>
                    )}
                  </>
                ) : (
                  segments.map((seg, i) =>
                    seg.type === "text" ? (
                      <div
                        key={i}
                        className="bg-muted/50 rounded-2xl px-3 py-2 shadow-sm overflow-hidden"
                      >
                        <div className="text-sm prose prose-sm dark:prose-invert max-w-none break-words">
                          <Markdown>{seg.content}</Markdown>
                        </div>
                      </div>
                    ) : seg.type === "tool" ? (
                      <ToolResultCard
                        key={seg.toolCallId}
                        toolName={seg.toolName}
                        args={seg.args}
                        result={
                          typeof seg.result === "string"
                            ? seg.result
                            : JSON.stringify(seg.result)
                        }
                      />
                    ) : null
                  )
                )}
              </div>

              {message.role === "user" && (
                <div className="shrink-0">
                  <div className="w-7 h-7 rounded-full bg-muted flex items-center justify-center text-xs font-medium">
                    U
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Admin Page
// ---------------------------------------------------------------------------

export default function SerniaAdminPage() {
  const { getToken } = useAuth();
  const navigate = useNavigate();

  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [modalityFilter, setModalityFilter] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);

  // Detail drawer
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selectedConv = conversations.find(
    (c) => c.conversation_id === selectedId
  );

  const fetchConversations = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const token = await getToken();
      const params = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: String(page * PAGE_SIZE),
      });
      if (modalityFilter) params.set("modality", modalityFilter);

      const res = await fetch(
        `${API_BASE}/conversations/history?${params}`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data = await res.json();
      setConversations(data.conversations || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [getToken, page, modalityFilter]);

  useEffect(() => {
    fetchConversations();
  }, [fetchConversations]);

  // Reset page when filter changes
  useEffect(() => {
    setPage(0);
  }, [modalityFilter]);

  const handleDelete = () => {
    setSelectedId(null);
    fetchConversations();
  };

  // Client-side search filter
  const filtered = search.trim()
    ? conversations.filter(
        (c) =>
          c.preview.toLowerCase().includes(search.toLowerCase()) ||
          c.contact_identifier?.toLowerCase().includes(search.toLowerCase()) ||
          c.user_email?.toLowerCase().includes(search.toLowerCase()) ||
          c.trigger_contact_name?.toLowerCase().includes(search.toLowerCase())
      )
    : conversations;

  return (
    <AuthGuard
      requireDomain="serniacapital.com"
      message="Admin access required"
      icon={<Building className="w-16 h-16 text-muted-foreground" />}
    >
      <div className="flex flex-col h-[calc(100dvh-52px)] bg-background">
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-3 border-b">
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8 shrink-0"
            onClick={() => navigate("/sernia-chat")}
          >
            <ArrowLeft className="w-4 h-4" />
          </Button>
          <div className="flex-1 min-w-0">
            <h1 className="text-lg font-semibold">Conversations</h1>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={fetchConversations}
            disabled={loading}
            className="gap-1.5"
          >
            <RefreshCw
              className={cn("w-3.5 h-3.5", loading && "animate-spin")}
            />
            Refresh
          </Button>
        </div>

        {/* Filter bar */}
        <div className="flex flex-wrap items-center gap-2 px-4 py-2 border-b">
          <div className="flex gap-1">
            {MODALITY_OPTIONS.map((opt) => (
              <Button
                key={opt.label}
                variant={modalityFilter === opt.value ? "default" : "outline"}
                size="sm"
                className="text-xs h-8 gap-1.5"
                onClick={() => setModalityFilter(opt.value)}
              >
                {opt.icon && <opt.icon className="w-3.5 h-3.5" />}
                {opt.label}
              </Button>
            ))}
          </div>
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search preview, contact, user..."
            className="h-8 text-xs max-w-[260px]"
          />
        </div>

        {/* Error */}
        {error && (
          <div className="mx-4 mt-2 rounded-lg border border-red-300 bg-red-50 dark:bg-red-950/20 p-3 text-sm text-red-700 dark:text-red-400">
            {error}
          </div>
        )}

        {/* Table */}
        <div className="flex-1 overflow-auto">
          {loading && conversations.length === 0 ? (
            <div className="flex justify-center py-16">
              <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
            </div>
          ) : filtered.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-16">
              No conversations found
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[40%]">Preview</TableHead>
                  <TableHead className="hidden sm:table-cell">Source</TableHead>
                  <TableHead className="hidden md:table-cell">
                    Contact
                  </TableHead>
                  <TableHead className="hidden lg:table-cell">User</TableHead>
                  <TableHead className="hidden lg:table-cell text-right">
                    Tokens
                  </TableHead>
                  <TableHead className="hidden sm:table-cell">Status</TableHead>
                  <TableHead>Updated</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((conv) => (
                  <TableRow
                    key={conv.conversation_id}
                    className={cn(
                      "cursor-pointer",
                      conv.conversation_id === selectedId && "bg-muted"
                    )}
                    onClick={() => setSelectedId(conv.conversation_id)}
                  >
                    <TableCell className="max-w-0">
                      <p className="truncate text-sm">
                        {conv.preview || "Empty conversation"}
                      </p>
                    </TableCell>
                    <TableCell className="hidden sm:table-cell">
                      <div className="flex items-center gap-1.5 text-muted-foreground">
                        {modalityIcon(conv.modality)}
                        <span className="text-xs capitalize">
                          {conv.modality.replace("_", " ")}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell className="hidden md:table-cell">
                      <span className="text-xs text-muted-foreground truncate block max-w-[160px]">
                        {conv.trigger_contact_name ||
                          conv.contact_identifier ||
                          "—"}
                      </span>
                    </TableCell>
                    <TableCell className="hidden lg:table-cell">
                      <span className="text-xs text-muted-foreground truncate block max-w-[160px]">
                        {conv.user_email
                          ? conv.user_email.split("@")[0]
                          : "—"}
                      </span>
                    </TableCell>
                    <TableCell className="hidden lg:table-cell text-right">
                      <span className="text-xs text-muted-foreground font-mono">
                        {formatTokens(conv.estimated_tokens)}
                      </span>
                    </TableCell>
                    <TableCell className="hidden sm:table-cell">
                      {conv.has_pending && (
                        <Badge
                          variant="outline"
                          className="text-xs text-amber-600 border-amber-300"
                        >
                          Pending
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      <span className="text-xs text-muted-foreground whitespace-nowrap">
                        {formatDateTime(conv.updated_at)}
                      </span>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>

        {/* Pagination */}
        <div className="flex items-center justify-between px-4 py-2 border-t text-sm text-muted-foreground">
          <span>
            Page {page + 1}
            {conversations.length === PAGE_SIZE && "+"}
          </span>
          <div className="flex gap-1">
            <Button
              variant="outline"
              size="sm"
              disabled={page === 0}
              onClick={() => setPage((p) => p - 1)}
              className="h-8 w-8 p-0"
            >
              <ChevronLeft className="w-4 h-4" />
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={conversations.length < PAGE_SIZE}
              onClick={() => setPage((p) => p + 1)}
              className="h-8 w-8 p-0"
            >
              <ChevronRight className="w-4 h-4" />
            </Button>
          </div>
        </div>
      </div>

      {/* Detail drawer */}
      <Sheet
        open={!!selectedId}
        onOpenChange={(open) => !open && setSelectedId(null)}
      >
        <SheetContent side="right" className="w-full sm:max-w-xl p-0">
          <SheetHeader className="px-4 py-3 border-b">
            <SheetTitle className="text-sm font-medium flex items-center gap-2">
              {selectedConv && (
                <>
                  {modalityIcon(selectedConv.modality)}
                  <span className="truncate flex-1">
                    {selectedConv.preview || "Conversation"}
                  </span>
                  {selectedConv.has_pending && (
                    <Badge
                      variant="outline"
                      className="text-xs text-amber-600 border-amber-300"
                    >
                      Pending
                    </Badge>
                  )}
                </>
              )}
            </SheetTitle>
            {selectedConv && (
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                {selectedConv.trigger_contact_name && (
                  <span>Contact: {selectedConv.trigger_contact_name}</span>
                )}
                {selectedConv.contact_identifier && (
                  <span>{selectedConv.contact_identifier}</span>
                )}
                {selectedConv.user_email && (
                  <span>User: {selectedConv.user_email}</span>
                )}
                {selectedConv.estimated_tokens > 0 && (
                  <span>
                    Tokens: {formatTokens(selectedConv.estimated_tokens)}
                  </span>
                )}
                {selectedConv.created_at && (
                  <span>
                    Created: {formatDateTime(selectedConv.created_at)}
                  </span>
                )}
              </div>
            )}
          </SheetHeader>
          {selectedId && (
            <ConversationDetail
              key={selectedId}
              conversationId={selectedId}
              getToken={getToken}
              onDelete={handleDelete}
            />
          )}
        </SheetContent>
      </Sheet>
    </AuthGuard>
  );
}
