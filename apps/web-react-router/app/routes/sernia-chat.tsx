import type { Route } from "./+types/sernia-chat";
import { useState, useRef, useEffect, useCallback } from "react";
import { useSearchParams, useNavigate } from "react-router";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import { useAuth, useUser } from "@clerk/react-router";
import { Button } from "~/components/ui/button";
import { Textarea } from "~/components/ui/textarea";
import { useScrollToBottom } from "~/hooks/use-scroll-to-bottom";
import { usePushNotifications } from "~/hooks/use-push-notifications";
import { useVisualViewportHeight } from "~/hooks/use-visual-viewport-height";
import {
  usePullToRefresh,
  useIsStandalonePwa,
} from "~/hooks/use-pull-to-refresh";
import { cn } from "~/lib/utils";
import { Markdown } from "~/components/markdown";
import { AuthGuard } from "~/components/auth-guard";
import {
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
} from "~/components/ui/tabs";
import {
  Building,
  StopCircle,
  Send,
  Loader2,
  Plus,
  RefreshCw,
  Bell,
  BellOff,
  Share,
  Download,
  Phone,
  Upload,
  Menu,
} from "lucide-react";
import { Badge } from "~/components/ui/badge";
import {
  ToolApprovalCard,
  ToolResultCard,
  convertAllPendingFromApi,
  submitApprovalDecisions,
  type PendingApproval,
} from "~/components/chat/tool-cards";
import { processMessage } from "~/components/chat/process-message";
import { useFileAttachments } from "~/hooks/use-file-attachments";
import {
  FileAttachmentButton,
  FilePreviewStrip,
} from "~/components/chat/file-attachment-area";
import { FileMessageDisplay } from "~/components/chat/file-message-display";
import { SidebarProvider, SidebarInset, useSidebar } from "~/components/ui/sidebar";
import {
  ConversationSidebar,
  prefetchConversations,
} from "~/components/sernia/conversation-sidebar";

const API_BASE = "/api/sernia-ai";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Sernia AI" },
    {
      name: "description",
      content:
        "AI assistant for Sernia Capital — manages tasks, searches info, sends messages, and builds knowledge over time.",
    },
  ];
}

const suggestedPrompts = [
  {
    title: "What's in your memory?",
    prompt: "What's in your memory?",
  },
  {
    title: "What are today's notes?",
    prompt: "What are today's notes?",
  },
  {
    title: "Search for recent property info",
    prompt: "Search for recent property info",
  },
];

// ---------------------------------------------------------------------------
// Typing indicator — shown while waiting on the backend's first response byte
// ---------------------------------------------------------------------------

function TypingIndicator() {
  return (
    <div
      className="flex gap-3 justify-start"
      role="status"
      aria-label="Sernia AI is typing"
    >
      <div className="shrink-0">
        <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center">
          <Building className="w-4 h-4 text-primary-foreground" />
        </div>
      </div>
      <div className="bg-muted/50 rounded-2xl px-4 py-3 shadow-sm flex items-center gap-1.5">
        <span
          className="w-1.5 h-1.5 rounded-full bg-muted-foreground animate-typing-dot"
          style={{ animationDelay: "0ms" }}
        />
        <span
          className="w-1.5 h-1.5 rounded-full bg-muted-foreground animate-typing-dot"
          style={{ animationDelay: "150ms" }}
        />
        <span
          className="w-1.5 h-1.5 rounded-full bg-muted-foreground animate-typing-dot"
          style={{ animationDelay: "300ms" }}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inner chat component — remounts on conversation switch via `key` prop
// ---------------------------------------------------------------------------

function ChatView({
  conversationId,
  initialMessages,
  initialPending,
  initialAllPending,
  getToken,
  readOnly = false,
}: {
  conversationId: string;
  initialMessages: any[];
  initialPending: PendingApproval | null;
  initialAllPending?: PendingApproval[];
  getToken: () => Promise<string | null>;
  readOnly?: boolean;
}) {
  const [pendingApproval, setPendingApproval] =
    useState<PendingApproval | null>(initialPending);
  const [allPendingApprovals, setAllPendingApprovals] =
    useState<PendingApproval[]>(initialAllPending || (initialPending ? [initialPending] : []));
  const [isProcessingApproval, setIsProcessingApproval] = useState(false);
  const draftKey = `sernia-draft-${conversationId}`;
  const [input, setInput] = useState(
    () => (typeof window !== "undefined" && sessionStorage.getItem(draftKey)) || ""
  );
  // Persist draft to sessionStorage so it survives component remounts
  useEffect(() => {
    if (input) {
      sessionStorage.setItem(draftKey, input);
    } else {
      sessionStorage.removeItem(draftKey);
    }
  }, [input, draftKey]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [messagesContainerRef, messagesEndRef] =
    useScrollToBottom<HTMLDivElement>();
  const attachment = useFileAttachments();

  const isStandalonePwa = useIsStandalonePwa();
  const pullDistance = usePullToRefresh({
    scrollContainerRef: messagesContainerRef,
    enabled: isStandalonePwa,
    threshold: 80,
    onRefresh: () => window.location.reload(),
  });
  const pullProgress = Math.min(pullDistance / 80, 1);

  // Transport is created once per mount (conversationId is stable for this instance)
  const transport = useRef(
    new DefaultChatTransport({
      api: `${API_BASE}/chat`,
      headers: async () => {
        const token = await getToken();
        return { Authorization: `Bearer ${token}` };
      },
      prepareSendMessagesRequest: ({ messages, body, trigger }) => {
        const transformedMessages = messages.map((msg: any) => {
          if (msg.parts) {
            return {
              id: msg.id || crypto.randomUUID(),
              role: msg.role,
              parts: msg.parts,
            };
          }
          return {
            id: msg.id || crypto.randomUUID(),
            role: msg.role,
            parts: [{ type: "text", text: msg.content || "" }],
          };
        });
        return {
          body: {
            trigger,
            id: conversationId,
            messages: transformedMessages,
            ...body,
          },
        };
      },
    })
  ).current;

  const { messages, sendMessage, status, stop, setMessages } = useChat({
    id: conversationId,
    messages: initialMessages,
    transport,
  });

  // Extract ALL pending approvals from latest assistant message
  // PydanticAI requires results for all deferred tool calls, so we need to track all of them
  useEffect(() => {
    if (status !== "ready") return;

    const lastAssistantMsg = [...messages]
      .reverse()
      .find((m) => m.role === "assistant");
    if (!lastAssistantMsg) {
      setPendingApproval(null);
      setAllPendingApprovals([]);
      return;
    }

    const parts = (lastAssistantMsg as any).parts || [];
    const allPending: PendingApproval[] = [];

    for (const part of parts) {
      if (!part.type?.startsWith("tool-")) continue;
      const toolName = part.type.replace("tool-", "");
      const isPending =
        part.state === "input-available" && part.output === undefined;

      if (isPending && part.toolCallId && part.input) {
        allPending.push({
          toolCallId: part.toolCallId,
          toolName,
          args: part.input,
        });
      }
    }

    if (allPending.length > 0) {
      setPendingApproval(allPending[0]); // Display the first one
      setAllPendingApprovals(allPending); // Track all for API call
    } else {
      setPendingApproval(null);
      setAllPendingApprovals([]);
    }
  }, [messages, status]);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
    }
  }, [input]);

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (status === "submitted" || status === "streaming" || isProcessingApproval) return;

    const text = input.trim();
    const hasContent = text || attachment.hasFiles;
    if (!hasContent) return;

    // Deny-with-feedback path: when a HITL approval is pending and the user
    // types a message, submit implicitly denies every pending tool call and
    // attaches the typed text as a real user turn. The backend passes the
    // text to PydanticAI's agent.run(user_prompt=...), which bundles it with
    // the ToolReturnParts into a single ModelRequest (UserPromptPart). So it
    // lives in the DB as a normal chat turn, not just a tool-denial reason.
    // This also collapses the old two-round-trip flow (deny → wait → type
    // feedback) into one LLM call.
    if (allPendingApprovals.length > 0 && text) {
      setIsProcessingApproval(true);
      // Render the user's message optimistically so the chat feels responsive.
      // On refresh, the same text will load from the DB as a UserPromptPart;
      // IDs differ but the visible bubble is identical so there is no dupe.
      const optimisticUserMsg = {
        id: crypto.randomUUID(),
        role: "user" as const,
        parts: [{ type: "text", text }],
      };
      setMessages((prev: any[]) => [...prev, optimisticUserMsg]);
      try {
        const result = await submitApprovalDecisions({
          apiBase: API_BASE,
          conversationId,
          getToken,
          decisions: allPendingApprovals.map((p) => ({
            tool_call_id: p.toolCallId,
            approved: false,
            reason: text,
          })),
          userMessage: text,
        });
        setInput("");
        handleApprovalComplete(result);
      } catch (err) {
        console.error("Deny-with-feedback error:", err);
        alert(err instanceof Error ? err.message : "Failed to submit feedback");
        setMessages((prev: any[]) => prev.filter((m) => m.id !== optimisticUserMsg.id));
      } finally {
        setIsProcessingApproval(false);
      }
      return;
    }

    const parts: any[] = [
      ...attachment.files.map((f) => ({
        type: "file",
        mediaType: f.mediaType,
        url: f.url,
        filename: f.filename,
      })),
    ];
    if (text) {
      parts.push({ type: "text", text: input });
    }
    setPendingApproval(null);
    setAllPendingApprovals([]);
    sendMessage({ role: "user", parts });
    setInput("");
    attachment.clearFiles();
  };

  const handleSuggestedPrompt = (prompt: string) => {
    setPendingApproval(null);
    setAllPendingApprovals([]);
    sendMessage({ role: "user", parts: [{ type: "text", text: prompt }] });
  };

  const handleApprovalComplete = useCallback(
    (result: any) => {
      setPendingApproval(null);
      setAllPendingApprovals([]);

      const decisionMap = new Map<string, boolean>();
      if (result.decisions) {
        for (const d of result.decisions) {
          decisionMap.set(d.tool_call_id, d.approved);
        }
      }

      // Backend returns actual tool results keyed by tool_call_id
      const toolResults: Record<string, string> = result.tool_results || {};

      // If the backend surfaced a new round of pending approvals (because the
      // resumed agent called more deferred tools), we need to render a fresh
      // approval card. Build assistant-message parts that mirror what the
      // streaming chat would have produced (state: "input-available", no output).
      const newPendingParts =
        Array.isArray(result.pending) && result.pending.length > 0
          ? result.pending.map((p: any) => ({
              type: `tool-${p.tool_name}`,
              toolCallId: p.tool_call_id,
              input: p.args || {},
              state: "input-available",
            }))
          : [];

      setMessages((prev: any[]) => {
        const updated = [...prev];
        const lastAssistantIdx = updated.findLastIndex(
          (m: any) => m.role === "assistant"
        );
        if (lastAssistantIdx >= 0) {
          const lastMsg = updated[lastAssistantIdx];
          if (lastMsg.parts) {
            lastMsg.parts = lastMsg.parts.map((part: any) => {
              if (
                part.type?.startsWith("tool-") &&
                part.state === "input-available"
              ) {
                const wasApproved = part.toolCallId
                  ? (decisionMap.get(part.toolCallId) ?? true)
                  : true;
                const realResult = part.toolCallId
                  ? toolResults[part.toolCallId]
                  : undefined;
                // Match the Vercel AI SDK / PydanticAI adapter's on-refresh
                // encoding: denied returns use state "output-denied" so the
                // renderer doesn't have to sniff the output string.
                return {
                  ...part,
                  state: wasApproved ? "output-available" : "output-denied",
                  output:
                    realResult || (wasApproved ? "Completed" : "Denied by user"),
                };
              }
              return part;
            });
          }
          updated[lastAssistantIdx] = { ...lastMsg };
        }

        const followUpParts: any[] = [];
        if (result.output) {
          followUpParts.push({ type: "text", text: result.output });
        }
        followUpParts.push(...newPendingParts);

        if (followUpParts.length > 0) {
          updated.push({
            id: crypto.randomUUID(),
            role: "assistant" as const,
            parts: followUpParts,
          });
        }

        return updated;
      });

      // If there are new pending approvals, immediately reflect them in state
      // so the approval card shows without waiting for the messages-watcher
      // useEffect to tick.
      if (newPendingParts.length > 0) {
        const asPendingApprovals: PendingApproval[] = newPendingParts.map(
          (p: any) => ({
            toolCallId: p.toolCallId,
            toolName: p.type.replace("tool-", ""),
            args: p.input,
          })
        );
        setPendingApproval(asPendingApprovals[0]);
        setAllPendingApprovals(asPendingApprovals);
      }
    },
    [setMessages]
  );

  return (
    <>
      {/* Messages */}
      <div
        ref={messagesContainerRef}
        className="flex flex-col min-w-0 gap-6 flex-1 overflow-y-scroll overflow-x-hidden overscroll-none pt-4 relative"
        {...attachment.dropTargetProps}
      >
        {isStandalonePwa && pullDistance > 0 && (
          <div
            className="absolute inset-x-0 top-0 flex justify-center pointer-events-none z-10"
            style={{
              transform: `translateY(${pullDistance - 32}px)`,
              opacity: pullProgress,
            }}
          >
            <div className="rounded-full bg-background/90 border shadow-sm p-2">
              <RefreshCw
                className={cn(
                  "w-4 h-4 text-muted-foreground",
                  pullProgress >= 1 && "text-primary"
                )}
                style={{ transform: `rotate(${pullProgress * 360}deg)` }}
              />
            </div>
          </div>
        )}
        {attachment.isDragging && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/80 border-2 border-dashed border-primary rounded-lg m-2">
            <div className="flex flex-col items-center gap-2 text-primary">
              <Upload className="w-8 h-8" />
              <p className="text-sm font-medium">Drop files here</p>
            </div>
          </div>
        )}
        {messages.length === 0 ? (
          <div className="mx-auto w-full max-w-3xl px-4">
            <div className="flex flex-col items-center gap-4 py-8">
              <div className="w-16 h-16 rounded-full bg-primary/10 flex items-center justify-center">
                <Building className="w-8 h-8 text-primary" />
              </div>
              <h2 className="text-2xl font-bold">Sernia AI</h2>
              <p className="text-sm text-muted-foreground text-center max-w-md">
                Your AI assistant for Sernia Capital. Manages tasks, searches
                info, sends messages, and builds knowledge over time.
              </p>
            </div>
          </div>
        ) : (
          <div className="mx-auto w-full max-w-3xl px-4 space-y-6">
            {messages.map((message, index) => {
              const { segments } = processMessage(message);
              const isLastAssistant =
                message.role === "assistant" &&
                index === messages.length - 1;

              return (
                <div
                  key={message.id || index}
                  className={cn(
                    "flex gap-3",
                    message.role === "user"
                      ? "justify-end"
                      : "justify-start"
                  )}
                >
                  {message.role === "assistant" && (
                    <div className="shrink-0">
                      <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center">
                        <Building className="w-4 h-4 text-primary-foreground" />
                      </div>
                    </div>
                  )}

                  <div
                    className={cn(
                      "flex flex-col gap-2 max-w-[85%] min-w-0",
                      message.role === "user" && "items-end"
                    )}
                  >
                    {message.role === "user" ? (
                      <>
                        <FileMessageDisplay
                          files={segments.filter((s) => s.type === "file") as any}
                        />
                        {segments.some((s) => s.type === "text") && (
                          <div className="bg-primary text-primary-foreground rounded-2xl px-4 py-2.5 shadow-sm">
                            <p className="text-sm whitespace-pre-wrap">
                              {segments.find((s) => s.type === "text")?.type === "text"
                                ? (segments.find((s) => s.type === "text") as any).content
                                : ""}
                            </p>
                          </div>
                        )}
                      </>
                    ) : (
                      <>
                        {segments.map((seg, i) =>
                          seg.type === "text" ? (
                            <div key={i} className="bg-muted/50 rounded-2xl px-4 py-2.5 shadow-sm overflow-hidden min-w-0">
                              <div className="text-sm prose prose-sm dark:prose-invert max-w-none break-words [overflow-wrap:anywhere]">
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
                              denied={seg.denied}
                            />
                          ) : null
                        )}

                        {isLastAssistant && pendingApproval && (
                          <ToolApprovalCard
                            pending={pendingApproval}
                            allPending={allPendingApprovals}
                            conversationId={conversationId}
                            onApprovalComplete={handleApprovalComplete}
                            isProcessing={isProcessingApproval}
                            getToken={getToken}
                            apiBase={API_BASE}
                          />
                        )}
                      </>
                    )}
                  </div>

                  {message.role === "user" && (
                    <div className="shrink-0">
                      <div className="w-8 h-8 rounded-full bg-muted flex items-center justify-center text-sm font-medium">
                        U
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
            {(status === "submitted" ||
              (status === "streaming" &&
                messages[messages.length - 1]?.role !== "assistant")) && (
              <TypingIndicator />
            )}
            <div
              ref={messagesEndRef}
              className="shrink-0 min-w-[24px] min-h-[24px]"
            />
          </div>
        )}
      </div>

      {/* Hidden file input */}
      <input
        ref={attachment.fileInputRef}
        type="file"
        accept={attachment.acceptString}
        multiple
        className="hidden"
        onChange={attachment.handleFileInputChange}
      />

      {/* Input Area — hidden for read-only SMS conversations */}
      {readOnly ? (
        <div className="flex items-center justify-center px-4 pt-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] border-t text-sm text-muted-foreground">
          <Phone className="w-4 h-4 mr-2" />
          SMS conversation — reply via text message
        </div>
      ) : (
      <div
        className="shrink-0 flex mx-auto px-4 bg-background pt-3 md:pt-4 pb-[calc(0.75rem+env(safe-area-inset-bottom))] md:pb-[calc(1rem+env(safe-area-inset-bottom))] gap-2 w-full md:max-w-3xl border-t"
      >
        {messages.length === 0 ? (
          <div className="flex flex-col gap-4 w-full">
            <p className="text-sm text-muted-foreground text-center">
              Try a suggestion or type your own request
            </p>
            <div className="grid sm:grid-cols-3 gap-2">
              {suggestedPrompts.map((suggestion, idx) => (
                <Button
                  key={idx}
                  variant="ghost"
                  type="button"
                  onClick={() => handleSuggestedPrompt(suggestion.prompt)}
                  className="text-left border rounded-xl px-4 py-3.5 text-sm h-auto justify-start"
                >
                  {suggestion.title}
                </Button>
              ))}
            </div>
            <FilePreviewStrip
              files={attachment.files}
              onRemove={attachment.removeFile}
            />
            <div className="flex gap-2 items-end">
              <FileAttachmentButton
                onClick={attachment.openFilePicker}
                disabled={status === "submitted" || status === "streaming"}
              />
              <Textarea
                ref={textareaRef}
                autoComplete="off"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSubmit();
                  }
                }}
                onPaste={attachment.handlePaste}
                placeholder="Ask Sernia AI anything..."
                className="min-h-0 max-h-[calc(75dvh)] overflow-hidden resize-none rounded-lg py-2 text-base md:text-sm bg-muted"
                rows={1}
                disabled={
                  status === "submitted" || status === "streaming"
                }
              />
              <Button
                type="button"
                onClick={() => handleSubmit()}
                size="icon"
                disabled={
                  (!input.trim() && !attachment.hasFiles) ||
                  status === "submitted" ||
                  status === "streaming"
                }
                className="h-9 w-9 shrink-0 rounded-lg"
              >
                <Send className="w-4 h-4" />
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-2 w-full">
            {pendingApproval && (
              <p className="text-xs text-amber-700 dark:text-amber-400 px-1">
                Sending a message will deny the pending action{allPendingApprovals.length > 1 ? "s" : ""} — your text becomes the feedback the AI sees.
              </p>
            )}
            <FilePreviewStrip
              files={attachment.files}
              onRemove={attachment.removeFile}
            />
            <div className="flex gap-2 items-end">
              <FileAttachmentButton
                onClick={attachment.openFilePicker}
                disabled={
                  status === "submitted" ||
                  status === "streaming" ||
                  !!pendingApproval ||
                  isProcessingApproval
                }
              />
              <Textarea
                ref={textareaRef}
                autoComplete="off"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSubmit();
                  }
                }}
                onPaste={attachment.handlePaste}
                placeholder={
                  pendingApproval
                    ? "Type feedback to deny pending action… or use the Approve/Deny buttons above."
                    : "Ask Sernia AI anything..."
                }
                className="min-h-0 max-h-[calc(75dvh)] overflow-hidden resize-none rounded-lg py-2 text-base md:text-sm bg-muted"
                rows={1}
                disabled={
                  status === "submitted" ||
                  status === "streaming" ||
                  isProcessingApproval
                }
              />
              {status === "streaming" ? (
                <Button
                  type="button"
                  onClick={stop}
                  size="icon"
                  variant="outline"
                  className="h-9 w-9 shrink-0 rounded-lg"
                >
                  <StopCircle className="w-4 h-4" />
                </Button>
              ) : (
                <Button
                  type="button"
                  onClick={() => handleSubmit()}
                  size="icon"
                  disabled={
                    (!input.trim() && !attachment.hasFiles) ||
                    status === "submitted" ||
                    isProcessingApproval ||
                    (!!pendingApproval && !input.trim())
                  }
                  className="h-9 w-9 shrink-0 rounded-lg"
                >
                  {isProcessingApproval ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Send className="w-4 h-4" />
                  )}
                </Button>
              )}
            </div>
          </div>
        )}
      </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// System Instructions admin view
// ---------------------------------------------------------------------------

interface InstructionSection {
  label: string;
  content: string;
}

interface ToolEntry {
  name: string;
  description: string;
  parameters_json_schema: unknown;
  kind?: string | null;
  metadata?: Record<string, unknown> | null;
}

interface ToolsetEntry {
  name: string;
  tools: ToolEntry[];
  error?: string;
}

interface BuiltinToolEntry {
  name: string;
  type: string;
  config: Record<string, unknown>;
}

const MODALITIES = ["web_chat", "sms", "email"] as const;

function SystemInstructionsView({
  getToken,
}: {
  getToken: () => Promise<string | null>;
}) {
  const [sections, setSections] = useState<InstructionSection[] | null>(null);
  const [toolsets, setToolsets] = useState<ToolsetEntry[]>([]);
  const [builtinTools, setBuiltinTools] = useState<BuiltinToolEntry[]>([]);
  const [totalTools, setTotalTools] = useState<number>(0);
  const [model, setModel] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Mock overrides
  const [modality, setModality] = useState<string>("web_chat");
  const [userName, setUserName] = useState<string>("");

  const fetchInstructions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const token = await getToken();
      const params = new URLSearchParams({ modality });
      if (userName.trim()) params.set("user_name", userName.trim());
      const res = await fetch(
        `${API_BASE}/admin/context?${params}`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data = await res.json();
      setSections(data.sections);
      setToolsets(data.toolsets || []);
      setBuiltinTools(data.builtin_tools || []);
      setTotalTools(data.total_tools || 0);
      setModel(data.model || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [getToken, modality, userName]);

  useEffect(() => {
    fetchInstructions();
  }, [fetchInstructions]);

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl px-4 py-6 space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">Context</h2>
            <p className="text-sm text-muted-foreground">
              Resolved system prompt + tools as the model sees them.
              {model && (
                <>
                  {" "}
                  Model:{" "}
                  <code className="text-xs bg-muted px-1 rounded">
                    {model}
                  </code>
                </>
              )}
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={fetchInstructions}
            disabled={loading}
            className="gap-1.5"
          >
            <RefreshCw
              className={cn("w-3.5 h-3.5", loading && "animate-spin")}
            />
            Refresh
          </Button>
        </div>

        {/* Mock context controls */}
        <div className="flex flex-wrap items-end gap-4 rounded-lg border bg-muted/30 p-4">
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">
              Modality
            </label>
            <div className="flex gap-1">
              {MODALITIES.map((m) => (
                <Button
                  key={m}
                  variant={modality === m ? "default" : "outline"}
                  size="sm"
                  className="text-xs h-8"
                  onClick={() => setModality(m)}
                >
                  {m}
                </Button>
              ))}
            </div>
          </div>
          <div className="space-y-1.5 flex-1 min-w-[200px]">
            <label className="text-xs font-medium text-muted-foreground">
              User name override
            </label>
            <input
              value={userName}
              onChange={(e) => setUserName(e.target.value)}
              placeholder="(uses your Clerk name)"
              className="flex h-8 w-full rounded-md border border-input bg-background px-3 py-1 text-xs shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            />
          </div>
        </div>

        {error && (
          <div className="rounded-lg border border-red-300 bg-red-50 dark:bg-red-950/20 p-4 text-sm text-red-700 dark:text-red-400">
            {error}
          </div>
        )}

        {loading && !sections && (
          <div className="flex justify-center py-12">
            <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
          </div>
        )}

        {sections?.map((section, idx) => (
          <details
            key={idx}
            className="rounded-lg border bg-muted/30"
            open={idx === 0}
          >
            <summary className="cursor-pointer px-4 py-2 text-sm font-medium select-none flex items-center gap-2">
              <Badge variant="outline" className="text-xs font-mono">
                {section.label}
              </Badge>
              <span className="text-xs text-muted-foreground">
                {section.content.length.toLocaleString()} chars
              </span>
            </summary>
            <div className="prose prose-sm dark:prose-invert max-w-none px-4 pb-4 pt-1 overflow-x-auto">
              <Markdown>{section.content}</Markdown>
            </div>
          </details>
        ))}

        {(toolsets.length > 0 || builtinTools.length > 0) && (
          <div className="space-y-3 pt-2">
            <div className="flex items-baseline gap-2">
              <h3 className="text-base font-semibold">Tools</h3>
              <p className="text-xs text-muted-foreground">
                {totalTools} custom + {builtinTools.length} builtin — exactly what
                pydantic-ai packages for the model via{" "}
                <code className="bg-muted px-1 rounded">Toolset.get_tools()</code>.
              </p>
            </div>

            {builtinTools.length > 0 && (
              <details className="rounded-lg border bg-muted/30">
                <summary className="cursor-pointer px-4 py-2 text-sm font-medium select-none">
                  Builtin tools ({builtinTools.length})
                </summary>
                <div className="px-4 pb-3 space-y-2">
                  {builtinTools.map((bt) => (
                    <div key={bt.name} className="text-xs space-y-1">
                      <div>
                        <code className="font-mono text-sm">{bt.name}</code>{" "}
                        <span className="text-muted-foreground">({bt.type})</span>
                      </div>
                      {Object.keys(bt.config).length > 0 && (
                        <pre className="text-xs bg-muted/50 rounded p-2 overflow-x-auto">
                          {JSON.stringify(bt.config, null, 2)}
                        </pre>
                      )}
                    </div>
                  ))}
                </div>
              </details>
            )}

            {toolsets.map((ts, tsIdx) => (
              <details
                key={tsIdx}
                className="rounded-lg border bg-muted/30"
                open={tsIdx === 0}
              >
                <summary className="cursor-pointer px-4 py-2 text-sm font-medium select-none flex items-center gap-2">
                  <span className="font-mono text-xs text-muted-foreground">
                    {ts.name}
                  </span>
                  <Badge variant="secondary" className="text-xs">
                    {ts.tools.length}
                  </Badge>
                  {ts.error && (
                    <span className="text-xs text-red-500">{ts.error}</span>
                  )}
                </summary>
                <div className="px-4 pb-3 space-y-3">
                  {ts.tools.map((t) => (
                    <div key={t.name} className="space-y-1 border-t pt-2 first:border-t-0 first:pt-0">
                      <div className="flex items-baseline gap-2 flex-wrap">
                        <code className="font-mono text-sm">{t.name}</code>
                        {t.kind && t.kind !== "function" && (
                          <Badge variant="outline" className="text-xs">
                            {t.kind}
                          </Badge>
                        )}
                        {t.metadata && (t.metadata as { requires_approval?: boolean }).requires_approval && (
                          <Badge variant="destructive" className="text-xs">
                            HITL approval
                          </Badge>
                        )}
                      </div>
                      {t.description && (
                        <div className="prose prose-xs dark:prose-invert max-w-none text-xs text-muted-foreground">
                          <Markdown>{t.description}</Markdown>
                        </div>
                      )}
                      <details className="mt-1">
                        <summary className="cursor-pointer text-xs text-muted-foreground select-none">
                          parameters
                        </summary>
                        <pre className="text-xs bg-muted/50 rounded p-2 mt-1 overflow-x-auto">
                          {JSON.stringify(t.parameters_json_schema, null, 2)}
                        </pre>
                      </details>
                    </div>
                  ))}
                </div>
              </details>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Admin Settings view
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Chat header with sidebar toggle, admin tabs, push notifications
// ---------------------------------------------------------------------------

function ChatHeader({
  isAdmin,
  push,
  onNewConversation,
}: {
  isAdmin: boolean;
  push: ReturnType<typeof usePushNotifications>;
  onNewConversation: () => void;
}) {
  const { toggleSidebar, isMobile } = useSidebar();

  return (
    <div className="flex items-center justify-between px-2 sm:px-4 py-2 border-b min-w-0">
      <div className="flex items-center gap-1 min-w-0">
        {/* Mobile sidebar toggle */}
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 shrink-0 md:hidden"
          onClick={toggleSidebar}
        >
          <Menu className="w-4 h-4" />
        </Button>

        {isAdmin && (
          <TabsList>
            <TabsTrigger value="chat">Chat</TabsTrigger>
            <TabsTrigger value="context">Context</TabsTrigger>
          </TabsList>
        )}
      </div>
      <div className="flex items-center gap-0.5 shrink-0">
        {push.isSupported && !push.needsInstall && (
          <Button
            variant={push.shouldPrompt ? "outline" : "ghost"}
            size="icon"
            className={cn("h-8 w-8", push.shouldPrompt && "animate-pulse")}
            onClick={push.isSubscribed ? push.unsubscribe : push.subscribe}
            disabled={push.isLoading || push.permission === "denied"}
            title={
              push.permission === "denied"
                ? "Notifications blocked — update browser settings"
                : push.isSubscribed
                  ? "Disable push notifications"
                  : "Enable push notifications"
            }
          >
            {push.isSubscribed ? (
              <Bell className="w-4 h-4" />
            ) : (
              <BellOff className="w-4 h-4 text-muted-foreground" />
            )}
          </Button>
        )}
        {push.canInstall && (
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={push.promptInstall}
            title="Install Sernia Capital app"
          >
            <Download className="w-4 h-4" />
          </Button>
        )}
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8"
          onClick={onNewConversation}
          title="New conversation"
        >
          <Plus className="w-4 h-4" />
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Outer page component — manages conversation selection & sidebar layout
// ---------------------------------------------------------------------------

export default function SerniaChatPage() {
  useVisualViewportHeight();
  const { isSignedIn, getToken } = useAuth();
  const { user } = useUser();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  const isAdmin =
    user?.primaryEmailAddress?.emailAddress === "emilio@serniacapital.com";
  const urlConversationId = searchParams.get("id");
  const [conversationId, setConversationId] = useState<string>(
    () => urlConversationId || crypto.randomUUID()
  );

  // Loaded messages for the current conversation (null = new conversation)
  const [loadedMessages, setLoadedMessages] = useState<any[] | null>(
    urlConversationId ? null : []
  );
  const [loadedPending, setLoadedPending] =
    useState<PendingApproval | null>(null);
  const [loadedAllPending, setLoadedAllPending] =
    useState<PendingApproval[]>([]);
  const [conversationModality, setConversationModality] =
    useState<string>("web_chat");
  const push = usePushNotifications();

  // Track IDs created locally so the URL-change effect skips the API call
  const newConversationIds = useRef<Set<string>>(new Set());
  // If the initial load has no URL id, it's a new conversation — register it
  if (!urlConversationId) {
    newConversationIds.current.add(conversationId);
  }

  // Prefetch conversations for the sidebar as early as possible
  useEffect(() => {
    if (isSignedIn) {
      prefetchConversations(getToken);
    }
  }, [isSignedIn, getToken]);

  // Load conversation messages from API
  const loadConversation = useCallback(
    async (
      convId: string,
      opts?: { updateUrl?: boolean; modality?: string; silent?: boolean }
    ) => {
      if (!isSignedIn) return;
      if (!opts?.silent) {
        setLoadedMessages(null);
      }

      try {
        const token = await getToken();
        const res = await fetch(
          `${API_BASE}/conversation/${convId}/messages`,
          { headers: { Authorization: `Bearer ${token}` } }
        );

        if (!res.ok) {
          console.error("Failed to load conversation");
          if (!opts?.silent) {
            navigate("/sernia-chat", { replace: true });
            setLoadedMessages([]);
          }
          return;
        }

        const data = await res.json();
        const allPending = convertAllPendingFromApi(data.pending);
        setLoadedPending(allPending.length > 0 ? allPending[0] : null);
        setLoadedAllPending(allPending);
        setConversationId(convId);
        setLoadedMessages(data.messages || []);
        setConversationModality(
          opts?.modality ||
            (convId.startsWith("ai_sms_from_") ? "sms" : "web_chat")
        );

        if (opts?.updateUrl !== false) {
          navigate(`/sernia-chat?id=${convId}`, { replace: true });
        }
      } catch (err) {
        console.error("Failed to load conversation:", err);
        if (!opts?.silent) {
          navigate("/sernia-chat", { replace: true });
          setLoadedMessages([]);
        }
      }
    },
    [isSignedIn, getToken, navigate]
  );

  // Load from URL on mount or when URL conversation ID changes
  useEffect(() => {
    if (urlConversationId && isSignedIn) {
      // Skip API call for conversations we just created locally
      if (newConversationIds.current.has(urlConversationId)) {
        newConversationIds.current.delete(urlConversationId);
        return;
      }
      loadConversation(urlConversationId, { updateUrl: false });
    } else if (!urlConversationId && isSignedIn) {
      navigate(`/sernia-chat?id=${conversationId}`, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isSignedIn, urlConversationId]);

  // Re-fetch conversation when page regains visibility
  useEffect(() => {
    const handleVisibility = () => {
      if (
        document.visibilityState === "visible" &&
        isSignedIn &&
        conversationId
      ) {
        loadConversation(conversationId, { updateUrl: false, silent: true });
      }
    };
    document.addEventListener("visibilitychange", handleVisibility);
    return () =>
      document.removeEventListener("visibilitychange", handleVisibility);
  }, [isSignedIn, conversationId, loadConversation]);

  // Listen for service worker messages (notification click)
  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (event.data?.type === "notification-click" && isSignedIn) {
        const convId = event.data.data?.conversation_id;
        if (convId) {
          loadConversation(convId, { updateUrl: true });
        }
      }
    };
    navigator.serviceWorker?.addEventListener("message", handler);
    return () =>
      navigator.serviceWorker?.removeEventListener("message", handler);
  }, [isSignedIn, loadConversation]);

  const startNewConversation = useCallback(() => {
    const newId = crypto.randomUUID();
    newConversationIds.current.add(newId);
    setConversationId(newId);
    setLoadedMessages([]);
    setLoadedPending(null);
    setLoadedAllPending([]);
    setConversationModality("web_chat");
    navigate(`/sernia-chat?id=${newId}`, { replace: true });
  }, [navigate]);

  const handleSelectConversation = useCallback(
    (convId: string, modality?: string) => {
      loadConversation(convId, { modality });
    },
    [loadConversation]
  );

  const handleDeleteConversation = useCallback(
    (convId: string) => {
      if (convId === conversationId) {
        startNewConversation();
      }
    },
    [conversationId, startNewConversation]
  );

  // Loading state (waiting for messages to load from API)
  const isLoading = loadedMessages === null;

  return (
    <AuthGuard
      message="Sernia AI assistant"
      icon={<Building className="w-16 h-16 text-muted-foreground" />}
    >
      <SidebarProvider>
        <ConversationSidebar
          activeConversationId={conversationId}
          onSelectConversation={handleSelectConversation}
          onNewConversation={startNewConversation}
          onDeleteConversation={handleDeleteConversation}
        />
        <SidebarInset className="min-w-0 overflow-x-hidden">
          {isLoading ? (
            <div className="flex flex-col items-center justify-center gap-4 h-chat-viewport">
              <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
              <p className="text-muted-foreground">Loading conversation...</p>
            </div>
          ) : (
            <Tabs
              defaultValue="chat"
              className="flex flex-col min-w-0 bg-background h-chat-viewport"
            >
              <ChatHeader
                isAdmin={isAdmin}
                push={push}
                onNewConversation={startNewConversation}
              />

              {/* iOS install banner */}
              {push.needsInstall && (
                <div className="flex items-center gap-2 px-4 py-2 border-b bg-muted/50 text-xs text-muted-foreground">
                  <Share className="w-3.5 h-3.5 shrink-0" />
                  <span>
                    {push.iosBrowser === "chrome"
                      ? "For notifications: tap Share (top right) → Add to Home Screen"
                      : "For notifications: tap Share (bottom center) → Add to Home Screen"}
                  </span>
                </div>
              )}

              <TabsContent
                value="chat"
                className="flex-1 flex flex-col min-h-0 mt-0"
              >
                <ChatView
                  key={conversationId}
                  conversationId={conversationId}
                  initialMessages={loadedMessages}
                  initialPending={loadedPending}
                  initialAllPending={loadedAllPending}
                  getToken={getToken}
                  readOnly={conversationModality === "sms"}
                />
              </TabsContent>

              {isAdmin && (
                <TabsContent
                  value="context"
                  className="flex-1 flex flex-col min-h-0 mt-0"
                >
                  <SystemInstructionsView getToken={getToken} />
                </TabsContent>
              )}
            </Tabs>
          )}
        </SidebarInset>
      </SidebarProvider>
    </AuthGuard>
  );
}
