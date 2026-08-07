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
import {
  serverHasResponseForRequest,
  userMessageFingerprint,
  type PendingRequestMarker,
} from "~/lib/sernia-chat-recovery";
import { Markdown } from "~/components/markdown";
import { AuthGuard } from "~/components/auth-guard";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "~/components/ui/tabs";
import {
  AlertCircle,
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
import {
  SidebarProvider,
  SidebarInset,
  useSidebar,
} from "~/components/ui/sidebar";
import {
  ConversationSidebar,
  prefetchConversations,
} from "~/components/sernia/conversation-sidebar";

const API_BASE = "/api/sernia-ai";
const PENDING_REQUEST_STORAGE_PREFIX = "sernia-pending-request-";

function pendingRequestStorageKey(conversationId: string): string {
  return `${PENDING_REQUEST_STORAGE_PREFIX}${conversationId}`;
}

function clearPendingRequestMarkerStorage(conversationId: string): void {
  if (typeof window === "undefined") return;
  try {
    sessionStorage.removeItem(pendingRequestStorageKey(conversationId));
  } catch {
    // In-memory recovery still works if storage is unavailable.
  }
}

function readPendingRequestMarker(
  conversationId: string,
): PendingRequestMarker | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = sessionStorage.getItem(
      pendingRequestStorageKey(conversationId),
    );
    if (!raw) return null;
    const marker = JSON.parse(raw);
    if (
      typeof marker?.fingerprint === "string" &&
      Number.isInteger(marker?.occurrence) &&
      marker.occurrence > 0
    ) {
      return {
        fingerprint: marker.fingerprint,
        occurrence: marker.occurrence,
        createdAt:
          typeof marker.createdAt === "number" &&
          Number.isFinite(marker.createdAt)
            ? marker.createdAt
            : Date.now(),
      };
    }
    return null;
  } catch {
    return null;
  }
}

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
// Thinking state
// ---------------------------------------------------------------------------

function AssistantAvatar({ active = false }: { active?: boolean }) {
  return (
    <div className="relative w-8 h-8 shrink-0" aria-hidden="true">
      {active && (
        <span className="absolute -inset-1 animate-sernia-thinking-orbit">
          <span className="absolute left-1/2 top-0 w-1.5 h-1.5 -translate-x-1/2 rounded-full bg-primary shadow-[0_0_0_3px_hsl(var(--background))]" />
        </span>
      )}
    <div
        className={cn(
          "relative w-8 h-8 rounded-full bg-primary flex items-center justify-center",
          active &&
            "ring-1 ring-primary/25 ring-offset-2 ring-offset-background",
        )}
    >
          <Building className="w-4 h-4 text-primary-foreground" />
        </div>
      </div>
  );
}

function ThinkingBubble({
  label = "Sernia AI is thinking",
}: {
  label?: string;
}) {
  return (
    <div className="bg-muted/50 rounded-2xl px-4 py-2.5 shadow-sm text-sm text-muted-foreground">
      {label}
      <span aria-hidden="true">…</span>
      </div>
  );
}

function ThinkingIndicator({ label }: { label?: string }) {
  return (
    <div
      className="flex gap-3 justify-start"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <AssistantAvatar active />
      <ThinkingBubble label={label} />
    </div>
  );
}

// Backend error responses are JSON bodies like {"error": "friendly message"}.
// The AI SDK surfaces the raw body as the Error message — unwrap it so users
// see the friendly text instead of JSON.
function formatStreamError(error: Error | undefined): string {
  const fallback = "The response was interrupted.";
  if (!error?.message) return fallback;
  try {
    const parsed = JSON.parse(error.message);
    return parsed.error || fallback;
  } catch {
    return error.message;
  }
}

// Lightweight signature for change detection between the rendered messages
// and the DB copy. Streamed and DB-dumped messages carry different ids for
// the same content, so compare role + visible content (text, tool call ids
// and states) instead of ids.
function messagesSignature(msgs: any[]): string {
  return msgs
    .map((m: any) => {
      const parts = Array.isArray(m.parts) ? m.parts : [];
      const content = parts
        .map((p: any) =>
          p.type === "text"
            ? p.text
            : p.toolCallId
              ? `${p.toolCallId}:${p.state ?? ""}`
              : p.type,
        )
        .join("\u0000");
      return `${m.role}:${content}`;
    })
    .join("\u0001");
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
  onMountedChange,
}: {
  conversationId: string;
  initialMessages: any[];
  initialPending: PendingApproval | null;
  initialAllPending?: PendingApproval[];
  getToken: () => Promise<string | null>;
  readOnly?: boolean;
  onMountedChange?: (mounted: boolean) => void;
}) {
  const [pendingApproval, setPendingApproval] =
    useState<PendingApproval | null>(initialPending);
  const [allPendingApprovals, setAllPendingApprovals] = useState<
    PendingApproval[]
  >(initialAllPending || (initialPending ? [initialPending] : []));
  const [isProcessingApproval, setIsProcessingApproval] = useState(false);
  const [recoveryState, setRecoveryState] = useState<
    "idle" | "checking" | "failed"
  >("idle");
  const [recoveryCycle, setRecoveryCycle] = useState(0);
  const [isPreparingRequest, setIsPreparingRequest] = useState(false);
  const [pendingRequestMarker, setPendingRequestMarker] =
    useState<PendingRequestMarker | null>(() =>
      readPendingRequestMarker(conversationId),
    );
  const draftKey = `sernia-draft-${conversationId}`;
  const [input, setInput] = useState(
    () =>
      (typeof window !== "undefined" && sessionStorage.getItem(draftKey)) || "",
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
  const inputBarRef = useRef<HTMLDivElement>(null);
  const [messagesContainerRef, messagesEndRef] =
    useScrollToBottom<HTMLDivElement>();
  const attachment = useFileAttachments();
  const lastChatHttpStatusRef = useRef<number | null>(null);

  useEffect(() => {
    onMountedChange?.(true);
    return () => onMountedChange?.(false);
  }, [onMountedChange]);

  // Track the actual height of the input bar so the messages list reserves
  // matching bottom padding. The input bar is position:fixed on mobile, so
  // without this the latest message would render under it.
  useEffect(() => {
    const el = inputBarRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const h = entries[0]?.contentRect.height ?? 0;
      document.documentElement.style.setProperty("--chat-input-h", `${h}px`);
    });
    observer.observe(el);
    return () => {
      observer.disconnect();
      document.documentElement.style.removeProperty("--chat-input-h");
    };
  }, [readOnly]);

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
      fetch: async (input, init) => {
        // A non-2xx response is a terminal server rejection, while a fetch
        // failure or a broken 2xx stream may still finish and persist in the
        // backend. Recovery polling is limited to the latter cases.
        lastChatHttpStatusRef.current = null;
        const response = await fetch(input, init);
        lastChatHttpStatusRef.current = response.status;
        return response;
      },
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
    }),
  ).current;

  const {
    messages,
    sendMessage,
    status,
    stop,
    setMessages,
    error,
    clearError,
  } = useChat({
      id: conversationId,
      messages: initialMessages,
      transport,
    });

  // Refs so async callbacks can read the latest values without re-subscribing
  const statusRef = useRef(status);
  statusRef.current = status;
  const isProcessingApprovalRef = useRef(isProcessingApproval);
  isProcessingApprovalRef.current = isProcessingApproval;
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const authoritativeMessagesRef = useRef(initialMessages);
  const pendingRequestRef = useRef<PendingRequestMarker | null>(
    pendingRequestMarker,
  );
  const syncGenerationRef = useRef(0);
  const syncAbortControllerRef = useRef<AbortController | null>(null);
  const isPreparingRequestRef = useRef(false);

  const invalidateServerSync = useCallback(() => {
    syncGenerationRef.current += 1;
    syncAbortControllerRef.current?.abort();
    syncAbortControllerRef.current = null;
  }, []);

  const handleApprovalProcessingChange = useCallback(
    (processing: boolean) => {
      isProcessingApprovalRef.current = processing;
      if (processing) invalidateServerSync();
      setIsProcessingApproval(processing);
    },
    [invalidateServerSync],
  );

  const storePendingRequest = useCallback(
    (marker: PendingRequestMarker | null) => {
      pendingRequestRef.current = marker;
      setPendingRequestMarker(marker);
      if (marker) {
        try {
          sessionStorage.setItem(
            pendingRequestStorageKey(conversationId),
            JSON.stringify(marker),
          );
        } catch {
          // Keep the in-memory marker if browser storage is unavailable.
        }
      } else {
        clearPendingRequestMarkerStorage(conversationId);
      }
    },
    [conversationId],
  );

  const rememberPendingRequest = useCallback(
    async (parts: any[]) => {
      invalidateServerSync();
      isPreparingRequestRef.current = true;
      setIsPreparingRequest(true);
      try {
        const fingerprint = await userMessageFingerprint({
          role: "user",
          parts,
        });
        let occurrence = 1;
        for (const message of authoritativeMessagesRef.current) {
          if ((await userMessageFingerprint(message)) === fingerprint) {
            occurrence += 1;
          }
        }
        storePendingRequest({
          fingerprint,
          occurrence,
          createdAt: Date.now(),
        });
      } finally {
        isPreparingRequestRef.current = false;
        setIsPreparingRequest(false);
      }
    },
    [invalidateServerSync, storePendingRequest],
  );

  const handleStop = useCallback(() => {
    stop();
  }, [stop]);

  // Re-sync the chat from the DB (the authoritative source). useChat only
  // reads its `messages` option at mount, so without this any response that
  // finishes after the stream dies client-side (mobile tab suspension,
  // network blip, provider error) stays invisible until a full page reload.
  const syncFromServer = useCallback(
    async (allowWhileActive = false) => {
      const chatActive = () =>
        statusRef.current === "submitted" || statusRef.current === "streaming";
      const busy = () => chatActive() || isProcessingApprovalRef.current;
      if (isProcessingApprovalRef.current || (!allowWhileActive && busy())) {
        return false;
      }
      const generation = syncGenerationRef.current + 1;
      syncGenerationRef.current = generation;
      syncAbortControllerRef.current?.abort();
      const controller = new AbortController();
      syncAbortControllerRef.current = controller;
      const pendingRequestAtStart = pendingRequestRef.current;
    try {
      const token = await getToken();
      const res = await fetch(
        `${API_BASE}/conversation/${conversationId}/messages`,
          {
            headers: { Authorization: `Bearer ${token}` },
            signal: controller.signal,
          },
      );
        if (!res.ok) return false;
      const data = await res.json();
      // Re-check after the awaits — the user may have hit send meanwhile.
        if (
          controller.signal.aborted ||
          generation !== syncGenerationRef.current ||
          isProcessingApprovalRef.current ||
          (!allowWhileActive && busy())
        ) {
          return false;
        }
      const serverMessages: any[] = data.messages || [];
      // Empty server copy = nothing persisted yet; keep local state.
        if (serverMessages.length === 0) return false;

        // Never let an early recovery fetch replace the optimistic user turn
        // with an older DB snapshot. Persistence happens only after the agent
        // finishes, so wait until the exact submitted turn and a later assistant
        // response are both present.
        const pendingRequest =
          pendingRequestRef.current ?? pendingRequestAtStart;
        if (
          pendingRequest &&
          !(await serverHasResponseForRequest(serverMessages, pendingRequest))
        ) {
          return false;
        }
        // Digest comparison is asynchronous; a new request may have started
        // while it was running even though the fetch itself had completed.
        if (
          controller.signal.aborted ||
          generation !== syncGenerationRef.current ||
          (pendingRequestAtStart !== null &&
            pendingRequestRef.current !== pendingRequestAtStart)
        ) {
          return false;
        }

        if (allowWhileActive && chatActive()) {
          stop();
        }

      if (
        messagesSignature(serverMessages) !==
        messagesSignature(messagesRef.current)
      ) {
        setMessages(serverMessages);
        const allPending = convertAllPendingFromApi(data.pending);
        setPendingApproval(allPending[0] ?? null);
        setAllPendingApprovals(allPending);
      }
        authoritativeMessagesRef.current = serverMessages;
        storePendingRequest(null);
        if (statusRef.current === "error") {
          clearError();
        }
        setRecoveryState("idle");
        return true;
    } catch {
      // Network failure — keep whatever we have.
        return false;
      } finally {
        if (syncAbortControllerRef.current === controller) {
          syncAbortControllerRef.current = null;
        }
    }
    },
    [
      clearError,
      conversationId,
      getToken,
      setMessages,
      stop,
      storePendingRequest,
    ],
  );

  // A push completion signal can arrive in the narrow window before an
  // approval request leaves its processing state. Reconcile once processing
  // ends so that signal cannot be lost if the HTTP response itself broke.
  const wasProcessingApprovalRef = useRef(false);
  useEffect(() => {
    const wasProcessing = wasProcessingApprovalRef.current;
    wasProcessingApprovalRef.current = isProcessingApproval;
    if (wasProcessing && !isProcessingApproval) {
      void syncFromServer(true);
    }
  }, [isProcessingApproval, syncFromServer]);

  useEffect(
    () => () => {
      syncGenerationRef.current += 1;
      syncAbortControllerRef.current?.abort();
    },
    [],
  );

  // Sync when the tab regains visibility or the network comes back. Allow an
  // authoritative completed response to replace a locally stuck stream — a
  // suspended mobile tab can retain "submitted"/"streaming" indefinitely.
  useEffect(() => {
    const handleVisibility = () => {
      if (document.visibilityState === "visible") syncFromServer(true);
    };
    const handleOnline = () => syncFromServer(true);
    document.addEventListener("visibilitychange", handleVisibility);
    window.addEventListener("online", handleOnline);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibility);
      window.removeEventListener("online", handleOnline);
    };
  }, [syncFromServer]);

  // When this view is mounted, apply focused completion signals in place so
  // attachments, drafts, scroll state, and the selected admin tab are kept.
  useEffect(() => {
    const handleServiceWorkerMessage = (event: MessageEvent) => {
      const isCompletionSignal =
        event.data?.type === "response-ready" ||
        event.data?.type === "notification-click";
      if (
        isCompletionSignal &&
        event.data.data?.conversation_id === conversationId
      ) {
        void syncFromServer(true);
      }
    };

    navigator.serviceWorker?.addEventListener(
      "message",
      handleServiceWorkerMessage,
    );
    return () =>
      navigator.serviceWorker?.removeEventListener(
        "message",
        handleServiceWorkerMessage,
      );
  }, [conversationId, syncFromServer]);

  // Confirm every submitted turn against the authoritative DB copy. The
  // marker survives remounts, so switching tabs/conversations or reloading
  // cannot unlock a conversation while its backend run is still active.
  useEffect(() => {
    const isRecoverableInterruption =
      lastChatHttpStatusRef.current === null ||
      (lastChatHttpStatusRef.current >= 200 &&
        lastChatHttpStatusRef.current < 300);

    if (status === "submitted" || status === "streaming") {
      setRecoveryState("idle");
      return;
    }

    if (status === "error" && !isRecoverableInterruption) {
      // A non-2xx response means the server rejected the request before a run
      // began, so there is no background work to protect from overlap.
      storePendingRequest(null);
      setRecoveryState("failed");
      return;
    }

    if (status !== "ready" && status !== "error") {
      setRecoveryState("idle");
      return;
    }

    if (!pendingRequestMarker) {
      setRecoveryState(status === "error" ? "failed" : "idle");
      return;
    }

    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    // Long tool-heavy runs regularly take close to a minute. Keep checking
    // for ~85 seconds before asking the user to intervene.
    const retryDelays = [
      0, 1000, 2000, 4000, 8000, 10000, 10000, 10000, 10000, 10000, 10000,
      10000,
    ];

    const wait = (delay: number) =>
      new Promise<void>((resolve) => {
        retryTimer = setTimeout(resolve, delay);
      });

    const recover = async () => {
      setRecoveryState("checking");

      for (const delay of retryDelays) {
        if (delay > 0) await wait(delay);
        const currentStatus = statusRef.current;
        if (
          cancelled ||
          pendingRequestRef.current === null ||
          (currentStatus !== "ready" && currentStatus !== "error")
        ) {
          return;
        }

        const recovered = await syncFromServer();
        if (recovered) {
          return;
        }
    }

      if (!cancelled) setRecoveryState("failed");
    };

    void recover();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, [
    pendingRequestMarker,
    recoveryCycle,
    status,
    storePendingRequest,
    syncFromServer,
  ]);

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

  const isRecoverableStreamError =
    status === "error" &&
    (lastChatHttpStatusRef.current === null ||
      (lastChatHttpStatusRef.current >= 200 &&
        lastChatHttpStatusRef.current < 300));
  const canRecoverResponse =
    pendingRequestMarker !== null || isRecoverableStreamError;
  const isRecoveringResponse =
    canRecoverResponse &&
    (status === "ready" || status === "error") &&
    recoveryState !== "failed";
  // A recoverable disconnect means the backend may still be executing. Keep
  // this conversation locked even after automatic polling pauses so a second
  // run cannot race and overwrite the first run's persisted history. The user
  // can keep checking or start a separate conversation from the header.
  const hasUnresolvedResponse = canRecoverResponse || isPreparingRequest;

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
    }
  }, [input]);

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (
      status === "submitted" ||
      status === "streaming" ||
      isProcessingApproval ||
      hasUnresolvedResponse ||
      pendingRequestRef.current !== null ||
      isPreparingRequestRef.current
    ) {
      return;
    }

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
      handleApprovalProcessingChange(true);
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
        setMessages((prev: any[]) =>
          prev.filter((m) => m.id !== optimisticUserMsg.id),
        );
      } finally {
        handleApprovalProcessingChange(false);
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
      parts.push({ type: "text", text });
    }
    setPendingApproval(null);
    setAllPendingApprovals([]);
    await rememberPendingRequest(parts);
    sendMessage({ role: "user", parts });
    setInput("");
    attachment.clearFiles();
  };

  const handleSuggestedPrompt = async (prompt: string) => {
    if (
      status === "submitted" ||
      status === "streaming" ||
      isProcessingApproval ||
      hasUnresolvedResponse ||
      pendingRequestRef.current !== null ||
      isPreparingRequestRef.current
    ) {
      return;
    }
    const parts: any[] = [{ type: "text", text: prompt }];
    setPendingApproval(null);
    setAllPendingApprovals([]);
    await rememberPendingRequest(parts);
    sendMessage({ role: "user", parts });
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
        let lastAssistantIdx = -1;
        for (let index = updated.length - 1; index >= 0; index -= 1) {
          if (updated[index].role === "assistant") {
            lastAssistantIdx = index;
            break;
          }
        }
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
                    realResult ||
                    (wasApproved ? "Completed" : "Denied by user"),
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

        authoritativeMessagesRef.current = updated;
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
          }),
        );
        setPendingApproval(asPendingApprovals[0]);
        setAllPendingApprovals(asPendingApprovals);
      }
    },
    [setMessages],
  );

  const isAiThinking =
    status === "submitted" ||
    status === "streaming" ||
    isPreparingRequest ||
    isProcessingApproval ||
    isRecoveringResponse;
  const lastMessageIsAssistant =
    messages[messages.length - 1]?.role === "assistant";
  const lastAssistantHasVisibleContent =
    lastMessageIsAssistant &&
    processMessage(messages[messages.length - 1]).segments.length > 0;
  const activityLabel = isRecoveringResponse
    ? status === "ready"
      ? "Confirming your response"
      : "Reconnecting to your response"
    : isProcessingApproval
      ? "Sernia AI is completing the action"
      : "Sernia AI is thinking";
  const allowRetryAfterUnresolvedResponse = useCallback(() => {
    const confirmed = window.confirm(
      "The original request may still finish and may already have performed actions. Allow another request in this conversation anyway? Start a new conversation instead if you are unsure.",
    );
    if (!confirmed) return;

    invalidateServerSync();
    storePendingRequest(null);
    if (statusRef.current === "error") clearError();
    setRecoveryState("idle");
  }, [clearError, invalidateServerSync, storePendingRequest]);
  const showStreamError =
    (status === "error" && !isRecoverableStreamError) ||
    (canRecoverResponse && recoveryState === "failed");

  return (
    <>
      {/* Messages */}
      <div
        ref={messagesContainerRef}
        className="flex flex-col min-w-0 gap-6 flex-1 overflow-y-scroll overflow-x-hidden overscroll-none pt-4 relative max-md:pb-[var(--chat-input-h,5rem)]"
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
                  pullProgress >= 1 && "text-primary",
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
                message.role === "assistant" && index === messages.length - 1;

              return (
                <div
                  key={message.id || index}
                  className={cn(
                    "flex gap-3",
                    message.role === "user" ? "justify-end" : "justify-start",
                  )}
                >
                  {message.role === "assistant" && (
                    <AssistantAvatar active={isLastAssistant && isAiThinking} />
                  )}

                  <div
                    className={cn(
                      "flex flex-col gap-2 max-w-[85%] min-w-0",
                      message.role === "user" && "items-end",
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
                          <div className="bg-primary text-primary-foreground rounded-2xl px-4 py-2.5 shadow-sm overflow-hidden max-w-full">
                            <p className="text-sm whitespace-pre-wrap [overflow-wrap:anywhere]">
                              {segments.find((s) => s.type === "text")?.type ===
                              "text"
                                ? (
                                    segments.find(
                                      (s) => s.type === "text",
                                    ) as any
                                  ).content
                                : ""}
                            </p>
                          </div>
                        )}
                      </>
                    ) : (
                      <>
                        {segments.map((seg, i) =>
                          seg.type === "text" ? (
                            <div
                              key={i}
                              className="bg-muted/50 rounded-2xl px-4 py-2.5 shadow-sm overflow-hidden min-w-0"
                            >
                              <div className="text-sm prose prose-sm dark:prose-invert max-w-none [overflow-wrap:anywhere]">
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
                          ) : null,
                        )}

                        {isLastAssistant &&
                          isAiThinking &&
                          (segments.length === 0 || isRecoveringResponse) && (
                            <div
                              role="status"
                              aria-live="polite"
                              aria-atomic="true"
                            >
                              <ThinkingBubble label={activityLabel} />
                            </div>
                        )}

                        {isLastAssistant && pendingApproval && (
                          <ToolApprovalCard
                            pending={pendingApproval}
                            allPending={allPendingApprovals}
                            conversationId={conversationId}
                            onApprovalComplete={handleApprovalComplete}
                            onProcessingChange={handleApprovalProcessingChange}
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
          </div>
        )}
        <div className="mx-auto w-full max-w-3xl px-4 space-y-6">
          {isAiThinking && !lastMessageIsAssistant && (
            <ThinkingIndicator label={activityLabel} />
          )}
          {isAiThinking &&
            lastMessageIsAssistant &&
            lastAssistantHasVisibleContent &&
            !isRecoveringResponse && (
              <span className="sr-only" role="status" aria-live="polite">
                {activityLabel}
              </span>
          )}
          {showStreamError && (
            <div className="flex items-start gap-3 rounded-lg border border-red-300 bg-red-50 dark:bg-red-950/20 px-4 py-3 text-sm text-red-700 dark:text-red-400">
              <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
              <div className="flex-1 min-w-0">
                <p>{formatStreamError(error)}</p>
                {canRecoverResponse && (
                  <p className="text-xs opacity-80 mt-1">
                    {pendingRequestMarker?.createdAt
                      ? `Request started at ${new Date(
                          pendingRequestMarker.createdAt,
                        ).toLocaleTimeString()}. `
                      : ""}
                    The response may still finish in the background. This
                    conversation stays locked to prevent overlapping runs;
                    check again, start a new conversation, or explicitly allow
                    a retry.
                  </p>
                )}
              </div>
              {canRecoverResponse && (
                <div className="flex shrink-0 flex-col gap-1">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      setRecoveryState("checking");
                      setRecoveryCycle((cycle) => cycle + 1);
                    }}
                  >
                    Check again
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={allowRetryAfterUnresolvedResponse}
                  >
                    Allow retry
                  </Button>
                </div>
              )}
            </div>
          )}
          <div
            ref={messagesEndRef}
            className="shrink-0 min-w-[24px] min-h-[24px]"
          />
        </div>
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

      {/* Input Area — hidden for read-only SMS conversations.
          On mobile we pin it with position:fixed at --keyboard-inset above
          the bottom so it stays directly above the iOS keyboard instead of
          being pushed off-screen with the page. */}
      {readOnly ? (
        <div
          ref={inputBarRef}
          className="flex items-center justify-center px-4 pt-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] border-t text-sm text-muted-foreground bg-background max-md:fixed max-md:left-0 max-md:right-0 max-md:bottom-[var(--keyboard-inset,0px)] max-md:z-30"
        >
          <Phone className="w-4 h-4 mr-2" />
          SMS conversation — reply via text message
        </div>
      ) : (
      <div
        ref={inputBarRef}
        className="shrink-0 flex mx-auto px-4 bg-background pt-3 md:pt-4 pb-[calc(0.75rem+env(safe-area-inset-bottom))] md:pb-[calc(1rem+env(safe-area-inset-bottom))] gap-2 w-full md:max-w-3xl border-t max-md:fixed max-md:left-0 max-md:right-0 max-md:bottom-[var(--keyboard-inset,0px)] max-md:z-30"
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
                    disabled={
                      status === "submitted" ||
                      status === "streaming" ||
                      isProcessingApproval ||
                      hasUnresolvedResponse
                    }
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
                  disabled={
                    status === "submitted" ||
                    status === "streaming" ||
                    isProcessingApproval ||
                    hasUnresolvedResponse
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
                placeholder="Ask Sernia AI anything..."
                className="min-h-0 max-h-[calc(75dvh)] overflow-hidden resize-none rounded-lg py-2 text-base md:text-sm bg-muted"
                rows={1}
                disabled={
                    status === "submitted" ||
                    status === "streaming" ||
                    isProcessingApproval ||
                    hasUnresolvedResponse
                }
              />
              <Button
                type="button"
                onClick={() => handleSubmit()}
                size="icon"
                disabled={
                  (!input.trim() && !attachment.hasFiles) ||
                  status === "submitted" ||
                    status === "streaming" ||
                    isProcessingApproval ||
                    hasUnresolvedResponse
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
                  Sending a message will deny the pending action
                  {allPendingApprovals.length > 1 ? "s" : ""} — your text
                  becomes the feedback the AI sees.
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
                    isProcessingApproval ||
                    hasUnresolvedResponse
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
                    isProcessingApproval ||
                    hasUnresolvedResponse
                }
              />
              {status === "streaming" ? (
                <Button
                  type="button"
                    onClick={handleStop}
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
                      hasUnresolvedResponse ||
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
      const res = await fetch(`${API_BASE}/admin/context?${params}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
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
                  <code className="text-xs bg-muted px-1 rounded">{model}</code>
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
                {totalTools} custom + {builtinTools.length} builtin — exactly
                what pydantic-ai packages for the model via{" "}
                <code className="bg-muted px-1 rounded">
                  Toolset.get_tools()
                </code>
                .
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
                        <span className="text-muted-foreground">
                          ({bt.type})
                        </span>
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
                    <div
                      key={t.name}
                      className="space-y-1 border-t pt-2 first:border-t-0 first:pt-0"
                    >
                      <div className="flex items-baseline gap-2 flex-wrap">
                        <code className="font-mono text-sm">{t.name}</code>
                        {t.kind && t.kind !== "function" && (
                          <Badge variant="outline" className="text-xs">
                            {t.kind}
                          </Badge>
                        )}
                        {t.metadata &&
                          (t.metadata as { requires_approval?: boolean })
                            .requires_approval && (
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
    () => urlConversationId || crypto.randomUUID(),
  );

  // Loaded messages for the current conversation (null = new conversation)
  const [loadedMessages, setLoadedMessages] = useState<any[] | null>(
    urlConversationId ? null : [],
  );
  const [loadedPending, setLoadedPending] = useState<PendingApproval | null>(
    null,
  );
  const [loadedAllPending, setLoadedAllPending] = useState<PendingApproval[]>(
    [],
  );
  const [conversationModality, setConversationModality] =
    useState<string>("web_chat");
  const push = usePushNotifications();
  const intendedConversationIdRef = useRef(conversationId);
  const conversationLoadGenerationRef = useRef(0);
  const conversationLoadAbortRef = useRef<AbortController | null>(null);
  const chatViewMountedRef = useRef(false);
  const handleChatViewMountedChange = useCallback((mounted: boolean) => {
    chatViewMountedRef.current = mounted;
  }, []);

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
      opts?: {
        updateUrl?: boolean;
        modality?: string;
        silent?: boolean;
        clearPendingMarkerOnSuccess?: boolean;
      },
    ) => {
      if (!isSignedIn) return false;
      intendedConversationIdRef.current = convId;
      const generation = conversationLoadGenerationRef.current + 1;
      conversationLoadGenerationRef.current = generation;
      conversationLoadAbortRef.current?.abort();
      const controller = new AbortController();
      conversationLoadAbortRef.current = controller;
      if (!opts?.silent) setLoadedMessages(null);

      try {
        const token = await getToken();
        if (generation !== conversationLoadGenerationRef.current) return false;
        const res = await fetch(`${API_BASE}/conversation/${convId}/messages`, {
          headers: { Authorization: `Bearer ${token}` },
          signal: controller.signal,
        });

        if (generation !== conversationLoadGenerationRef.current) return false;
        if (!res.ok) {
          console.error("Failed to load conversation");
          if (!opts?.silent) {
          navigate("/sernia-chat", { replace: true });
          setLoadedMessages([]);
          }
          return false;
        }

        const data = await res.json();
        if (
          controller.signal.aborted ||
          generation !== conversationLoadGenerationRef.current ||
          intendedConversationIdRef.current !== convId
        ) {
          return false;
        }
        const allPending = convertAllPendingFromApi(data.pending);
        setLoadedPending(allPending.length > 0 ? allPending[0] : null);
        setLoadedAllPending(allPending);
        setConversationId(convId);
        setLoadedMessages(data.messages || []);
        setConversationModality(
          opts?.modality ||
            (convId.startsWith("ai_sms_from_") ? "sms" : "web_chat"),
        );

        if (opts?.updateUrl !== false) {
          navigate(`/sernia-chat?id=${convId}`, { replace: true });
        }
        if (opts?.clearPendingMarkerOnSuccess) {
          clearPendingRequestMarkerStorage(convId);
        }
        return true;
      } catch (err) {
        if (
          controller.signal.aborted ||
          generation !== conversationLoadGenerationRef.current
        ) {
          return false;
        }
        console.error("Failed to load conversation:", err);
        if (!opts?.silent) {
        navigate("/sernia-chat", { replace: true });
        setLoadedMessages([]);
      }
        return false;
      } finally {
        if (conversationLoadAbortRef.current === controller) {
          conversationLoadAbortRef.current = null;
        }
      }
    },
    [isSignedIn, getToken, navigate],
  );

  useEffect(
    () => () => {
      conversationLoadGenerationRef.current += 1;
      conversationLoadAbortRef.current?.abort();
    },
    [],
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

  // NOTE: re-syncing the open conversation on visibility regain lives inside
  // ChatView (which owns the useChat state) — useChat only reads its
  // `messages` option at mount, so updating loadedMessages here wouldn't
  // reach the rendered chat.

  // Listen for service worker completion signals and notification clicks.
  // This lives outside ChatView so it remains active on the admin Context tab.
  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (!isSignedIn) return;
      const convId = event.data?.data?.conversation_id;
      if (!convId) return;

      if (event.data?.type === "notification-click") {
        if (
          convId !== intendedConversationIdRef.current ||
          !chatViewMountedRef.current
        ) {
          loadConversation(convId, { updateUrl: true });
        }
      } else if (
        event.data?.type === "response-ready" &&
        convId === intendedConversationIdRef.current &&
        !chatViewMountedRef.current
      ) {
        // ChatView may be unmounted on the admin Context tab. Refresh its next
        // initial snapshot silently while preserving the active tab and UI.
        loadConversation(convId, {
          updateUrl: false,
          silent: true,
          clearPendingMarkerOnSuccess: true,
        });
      }
    };
    navigator.serviceWorker?.addEventListener("message", handler);
    return () =>
      navigator.serviceWorker?.removeEventListener("message", handler);
  }, [isSignedIn, loadConversation]);

  const startNewConversation = useCallback(() => {
    const newId = crypto.randomUUID();
    conversationLoadGenerationRef.current += 1;
    conversationLoadAbortRef.current?.abort();
    intendedConversationIdRef.current = newId;
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
    [loadConversation],
  );

  const handleDeleteConversation = useCallback(
    (convId: string) => {
      if (convId === conversationId) {
        startNewConversation();
      }
    },
    [conversationId, startNewConversation],
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
                  onMountedChange={handleChatViewMountedChange}
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
