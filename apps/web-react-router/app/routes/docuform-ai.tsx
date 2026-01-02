import type { Route } from "./+types/docuform-ai";
import { useState, useRef, useEffect, useCallback } from "react";
import { useChat } from "@ai-sdk/react";
import { useAuth } from "@clerk/react-router";
import { DefaultChatTransport } from "ai";
import { Button } from "~/components/ui/button";
import { Textarea } from "~/components/ui/textarea";
import { useScrollToBottom } from "~/hooks/use-scroll-to-bottom";
import { cn } from "~/lib/utils";
import { Markdown } from "~/components/markdown";
import { SerniaAuthGuard } from "~/components/sernia-auth-guard";
import {
  FileText,
  Zap,
  StopCircle,
  Bot,
  ArrowLeft,
  RefreshCw,
  FileCheck,
  Sparkles,
  Loader2,
  RotateCcw,
} from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "~/components/ui/alert-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "~/components/ui/dialog";
import { Input } from "~/components/ui/input";
import { Label } from "~/components/ui/label";
import { Link, useSearchParams } from "react-router";

interface ContentControl {
  tag: string;
  alias: string;
  value: string;
  id: string | null;
}

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Template AI Assistant | Docuform" },
    {
      name: "description",
      content: "AI-powered template field detection and content control creation",
    },
  ];
}

// Suggested prompts shown after document is loaded
const suggestedPrompts = [
  {
    title: "Analyze for fields",
    action: "Analyze the document for potential fields that should be content controls.",
  },
  {
    title: "Show current controls",
    action: "Show me all the current content controls in the document.",
  },
  {
    title: "Wrap all detected fields",
    action: "Wrap all the detected fields in content controls.",
  },
  {
    title: "Save template",
    action: "Save the template with a new filename.",
  },
];

// Tool invocation display component
const ToolInvocationDisplay = ({
  toolName,
  args,
  result,
}: {
  toolName: string;
  args?: any;
  result?: any;
}) => {
  const [isExpanded, setIsExpanded] = useState(false);

  const truncateJson = (obj: any, maxLength: number = 200): string => {
    const str = JSON.stringify(obj, null, 2);
    if (str.length <= maxLength) return str;
    return str.substring(0, maxLength) + "...";
  };

  const getToolIcon = (name: string) => {
    switch (name) {
      case "load_document":
        return <FileText className="w-4 h-4" />;
      case "analyze_for_fields":
        return <Sparkles className="w-4 h-4" />;
      case "wrap_fields":
        return <FileCheck className="w-4 h-4" />;
      default:
        return <Zap className="w-4 h-4" />;
    }
  };

  return (
    <div className="border border-border rounded-lg overflow-hidden bg-muted/20">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full px-4 py-2 text-left flex items-center justify-between hover:bg-muted/50 transition-colors"
      >
        <span className="text-sm font-medium flex items-center gap-2">
          {getToolIcon(toolName)}
          <code className="text-xs bg-muted px-1.5 py-0.5 rounded">
            {toolName}
          </code>
        </span>
        <span className="text-xs text-muted-foreground">
          {isExpanded ? "▼" : "▶"}
        </span>
      </button>
      {isExpanded && (
        <div className="px-4 py-3 bg-muted/30 border-t border-border space-y-3">
          {args !== undefined && (
            <div>
              <div className="text-xs font-medium text-muted-foreground mb-1">
                Input:
              </div>
              <pre className="text-xs overflow-x-auto bg-background p-2 rounded border">
                {truncateJson(args)}
              </pre>
            </div>
          )}
          {result !== undefined && (
            <div>
              <div className="text-xs font-medium text-muted-foreground mb-1">
                Output:
              </div>
              <pre className="text-xs overflow-x-auto bg-background p-2 rounded border max-h-64 overflow-y-auto whitespace-pre-wrap">
                {typeof result === "string" ? result : truncateJson(result, 1000)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default function DocuformAIPage() {
  return (
    <SerniaAuthGuard>
      <DocuformAIContent />
    </SerniaAuthGuard>
  );
}

function DocuformAIContent() {
  const [searchParams] = useSearchParams();
  const docFromUrl = searchParams.get("doc");
  const { getToken } = useAuth();

  // Stable conversation ID for this session - used to scope working copies
  const [conversationId] = useState(() => crypto.randomUUID());

  const [input, setInput] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [previewMode, setPreviewMode] = useState<"original" | "working">("working");
  const [hasModifications, setHasModifications] = useState(false);

  // DOCX Preview state
  const previewContainerRef = useRef<HTMLDivElement>(null);
  const [docxPreview, setDocxPreview] = useState<typeof import("docx-preview") | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [contentControls, setContentControls] = useState<ContentControl[]>([]);

  // Save dialog state
  const [showSaveConfirm, setShowSaveConfirm] = useState(false);
  const [showSaveAsDialog, setShowSaveAsDialog] = useState(false);
  const [saveAsFilename, setSaveAsFilename] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Track previous status for auto-refresh
  const prevStatusRef = useRef<string | null>(null);

  // Resizable panel state
  const [previewWidth, setPreviewWidth] = useState(700);
  const [isResizing, setIsResizing] = useState(false);
  const resizeRef = useRef<{ startX: number; startWidth: number } | null>(null);

  // Document filename is set from URL param - required
  const documentFilename = docFromUrl;

  // Dynamic import docx-preview to avoid Vite bundling issues
  useEffect(() => {
    import("docx-preview").then(setDocxPreview).catch(console.error);
  }, []);

  const { messages, sendMessage, status, stop, setMessages } = useChat({
    id: conversationId, // Use stable conversation ID
    transport: new DefaultChatTransport({
      api: "/api/docuform/chat",
      prepareSendMessagesRequest: ({ messages, body, trigger, id }) => {
        const transformedMessages = messages.map((msg: any) => {
          if (msg.parts) {
            return msg;
          }
          return {
            id: msg.id || crypto.randomUUID(),
            role: msg.role,
            parts: [
              {
                type: "text",
                text: msg.content || "",
              },
            ],
          };
        });

        return {
          body: {
            trigger,
            id: conversationId, // Always use our stable conversation ID
            messages: transformedMessages,
            document_filename: documentFilename, // Pass filename in body
            ...body,
          },
        };
      },
    }),
  } as any);

  const [messagesContainerRef, messagesEndRef] =
    useScrollToBottom<HTMLDivElement>();

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
    }
  }, [input]);

  // Track modifications from tool results
  useEffect(() => {
    const modifyingTools = ["wrap_field", "replace_text", "reset_working_copy"];
    const successPatterns = ["Successfully wrapped", "Successfully replaced", "Working copy reset"];

    for (const message of messages) {
      // Check toolInvocations array (older SDK format)
      if (message.toolInvocations && Array.isArray(message.toolInvocations)) {
        for (const tool of message.toolInvocations) {
          if (modifyingTools.includes(tool.toolName) && tool.state === "result") {
            const output = tool.result;
            if (typeof output === "string") {
              if (successPatterns.some(p => output.includes(p))) {
                setHasModifications(true);
              }
              if (output.includes("Working copy reset")) {
                setHasModifications(false); // Reset clears modifications
              }
            }
          }
        }
      }
      // Check parts array (newer SDK format)
      if (message.parts && Array.isArray(message.parts)) {
        for (const part of message.parts as any[]) {
          // Only check parts with output available
          if (part.state === "output-available" || part.result || part.output) {
            const toolName = part.toolName || part.name;
            const output = part.result || part.output;
            if (modifyingTools.includes(toolName) && typeof output === "string") {
              if (successPatterns.some(p => output.includes(p))) {
                setHasModifications(true);
              }
              if (output.includes("Working copy reset")) {
                setHasModifications(false); // Reset clears modifications
              }
            }
          }
        }
      }
    }
  }, [messages]);

  // Auto-refresh preview when AI response completes (status changes from streaming to ready)
  useEffect(() => {
    // When status changes from "streaming" or "submitted" to "ready", refresh the preview
    if (
      (prevStatusRef.current === "streaming" || prevStatusRef.current === "submitted") &&
      status === "ready"
    ) {
      // Small delay to ensure the working copy file is written
      const timer = setTimeout(() => {
        renderDocxPreview();
      }, 500);
      return () => clearTimeout(timer);
    }
    prevStatusRef.current = status;
  }, [status]);

  // Handle resize drag
  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isResizing || !resizeRef.current) return;
      const delta = resizeRef.current.startX - e.clientX;
      const newWidth = Math.max(400, Math.min(1000, resizeRef.current.startWidth + delta));
      setPreviewWidth(newWidth);
    };

    const handleMouseUp = () => {
      setIsResizing(false);
      resizeRef.current = null;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };

    if (isResizing) {
      document.addEventListener("mousemove", handleMouseMove);
      document.addEventListener("mouseup", handleMouseUp);
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    }

    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
    };
  }, [isResizing]);

  const handleResizeStart = (e: React.MouseEvent) => {
    e.preventDefault();
    setIsResizing(true);
    resizeRef.current = { startX: e.clientX, startWidth: previewWidth };
  };

  // Highlight content controls in the rendered HTML
  const highlightContentControls = useCallback((controls: ContentControl[]) => {
    if (!previewContainerRef.current || controls.length === 0) return 0;

    let highlighted = 0;

    // Walk through text nodes and find matches
    const walker = document.createTreeWalker(
      previewContainerRef.current,
      NodeFilter.SHOW_TEXT,
      null
    );

    const nodesToWrap: { node: Text; control: ContentControl; start: number; end: number }[] = [];

    let node: Text | null;
    while ((node = walker.nextNode() as Text | null)) {
      const text = node.textContent || "";

      for (const control of controls) {
        if (!control.value || control.value.trim() === "") continue;

        const index = text.indexOf(control.value);
        if (index !== -1) {
          nodesToWrap.push({
            node,
            control,
            start: index,
            end: index + control.value.length,
          });
          break; // Only one match per text node to avoid complexity
        }
      }
    }

    // Wrap matches in highlight spans (process in reverse to maintain offsets)
    for (const { node, control, start, end } of nodesToWrap.reverse()) {
      const parent = node.parentNode;
      if (!parent) continue;

      const before = node.textContent?.substring(0, start) || "";
      const match = node.textContent?.substring(start, end) || "";
      const after = node.textContent?.substring(end) || "";

      const wrapper = document.createElement("span");
      wrapper.className = "content-control-highlight";
      wrapper.dataset.tag = control.tag;
      wrapper.dataset.alias = control.alias;
      wrapper.title = `${control.alias} (${control.tag})`;
      wrapper.textContent = match;

      const frag = document.createDocumentFragment();
      if (before) frag.appendChild(document.createTextNode(before));
      frag.appendChild(wrapper);
      if (after) frag.appendChild(document.createTextNode(after));

      parent.replaceChild(frag, node);
      highlighted++;
    }

    return highlighted;
  }, []);

  // Render DOCX preview
  const renderDocxPreview = useCallback(async () => {
    if (!documentFilename || !previewContainerRef.current || !docxPreview) return;

    setPreviewLoading(true);
    setPreviewError(null);

    try {
      const token = await getToken();
      const headers = { Authorization: `Bearer ${token}` };

      // Build query params - include conversation_id for working copy isolation
      // Add cache-busting timestamp to prevent browser caching stale document
      const params = new URLSearchParams();
      params.set("_t", Date.now().toString());
      if (previewMode === "working") {
        params.set("mode", "working");
        params.set("conversation_id", conversationId);
      }
      const queryString = `?${params.toString()}`;

      // Fetch content controls and document in parallel
      console.log("[DocuformAI] Fetching preview", { previewMode, conversationId: conversationId.slice(0, 8), queryString });
      const [controlsResponse, docResponse] = await Promise.all([
        fetch(`/api/docuform/documents/${encodeURIComponent(documentFilename)}/content-controls${queryString}`, { headers }),
        fetch(`/api/docuform/documents/${encodeURIComponent(documentFilename)}${queryString}`, { headers }),
      ]);

      // Parse content controls
      let controls: ContentControl[] = [];
      if (controlsResponse.ok) {
        const data = await controlsResponse.json();
        controls = data.content_controls || [];
        setContentControls(controls);
        // Check if we're viewing a working copy
        if (previewMode === "working" && data.is_working_copy) {
          setHasModifications(true);
        }
      } else {
        console.warn("Failed to load content controls:", controlsResponse.status, controlsResponse.statusText);
      }

      if (!docResponse.ok) {
        throw new Error(`Failed to fetch document: ${docResponse.statusText}`);
      }

      // Check header to see if working copy was returned
      if (previewMode === "working" && docResponse.headers.get("X-Working-Copy") === "true") {
        setHasModifications(true);
      }

      const blob = await docResponse.blob();
      await renderBlob(blob, controls);
    } catch (err) {
      console.error("Error rendering DOCX:", err);
      setPreviewError(err instanceof Error ? err.message : "Failed to render document");
    } finally {
      setPreviewLoading(false);
    }
  }, [documentFilename, docxPreview, getToken, previewMode, conversationId]);

  // Helper to render blob
  const renderBlob = useCallback(async (blob: Blob, controls: ContentControl[]) => {
    if (!previewContainerRef.current || !docxPreview) return;

    const options = {
      className: "docx-preview-container",
      inWrapper: true,
      ignoreWidth: false,
      ignoreHeight: true,
      ignoreFonts: false,
      debug: true,
      renderHeaders: true,
      renderFooters: true,
    };

    // Clear container
    previewContainerRef.current.innerHTML = "";

    // Render the DOCX
    await docxPreview.renderAsync(blob, previewContainerRef.current, undefined, options);

    // Highlight content controls
    if (controls.length > 0) {
      highlightContentControls(controls);
    }
  }, [docxPreview, highlightContentControls]);

  // Trigger preview render when document, preview mode, or modifications change
  // hasModifications triggers refresh to load latest working copy content
  useEffect(() => {
    renderDocxPreview();
  }, [documentFilename, docxPreview, previewMode, hasModifications, renderDocxPreview]);

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault();
    if (input.trim() && status !== "submitted" && status !== "streaming") {
      sendMessage({ role: "user", parts: [{ type: "text", text: input }] });
      setInput("");
    }
  };

  const handleSuggestedPrompt = (prompt: string) => {
    sendMessage({ role: "user", parts: [{ type: "text", text: prompt }] });
  };

  // Save handlers
  const handleSave = () => {
    setSaveError(null);
    setShowSaveConfirm(true);
  };

  const handleSaveConfirm = async () => {
    if (!documentFilename) return;
    setIsSaving(true);
    setSaveError(null);
    try {
      const token = await getToken();
      const response = await fetch(
        `/api/docuform/documents/${encodeURIComponent(documentFilename)}/save?conversation_id=${encodeURIComponent(conversationId)}`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
        }
      );
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || "Failed to save");
      }
      setShowSaveConfirm(false);
      setHasModifications(false);
      // Refresh preview to show the saved state
      renderDocxPreview();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setIsSaving(false);
    }
  };

  const handleSaveAs = () => {
    setSaveError(null);
    // Default filename based on original
    const stem = documentFilename?.replace(".docx", "") || "document";
    setSaveAsFilename(`${stem}_template.docx`);
    setShowSaveAsDialog(true);
  };

  const handleSaveAsConfirm = async () => {
    if (!documentFilename || !saveAsFilename.trim()) return;
    setIsSaving(true);
    setSaveError(null);
    try {
      const token = await getToken();
      const params = new URLSearchParams({
        new_filename: saveAsFilename,
        conversation_id: conversationId,
      });
      const response = await fetch(
        `/api/docuform/documents/${encodeURIComponent(documentFilename)}/save-as?${params.toString()}`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
        }
      );
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || "Failed to save");
      }
      setShowSaveAsDialog(false);
      // Note: Don't clear hasModifications as the working copy still exists
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setIsSaving(false);
    }
  };

  const handleReset = async () => {
    if (!documentFilename) return;
    try {
      const token = await getToken();
      await fetch(
        `/api/docuform/documents/${encodeURIComponent(documentFilename)}/working?conversation_id=${encodeURIComponent(conversationId)}`,
        {
          method: "DELETE",
          headers: { Authorization: `Bearer ${token}` },
        }
      );
      setHasModifications(false);
      setMessages([]);
      renderDocxPreview();
    } catch (err) {
      console.error("Failed to reset:", err);
    }
  };

  // Process messages to extract tool invocations
  const processMessage = (message: any) => {
    let textContent = "";
    if (message.content && typeof message.content === "string") {
      textContent = message.content;
    } else if (message.parts && Array.isArray(message.parts)) {
      const textParts = message.parts.filter((part: any) => part.type === "text");
      textContent = textParts.map((p: any) => p.text).join("");
    }

    let toolInvocations: any[] = [];
    if (message.toolInvocations && Array.isArray(message.toolInvocations)) {
      toolInvocations = message.toolInvocations;
    } else if (message.parts && Array.isArray(message.parts)) {
      const allToolParts = message.parts.filter(
        (part: any) =>
          part.type?.startsWith("tool-") ||
          part.state === "output-available" ||
          part.state === "input-available"
      );

      toolInvocations = allToolParts.map((part: any) => ({
        toolCallId: part.toolCallId || part.id,
        toolName:
          part.toolName ||
          part.name ||
          (part.type?.startsWith("tool-")
            ? part.type.replace("tool-", "")
            : "unknown"),
        state:
          part.state === "output-available"
            ? "result"
            : part.state === "input-available"
              ? "call"
              : part.state,
        args: part.args || part.input,
        result: part.result || part.output,
      }));
    }

    return { textContent, toolInvocations };
  };

  return (
    <div className="flex h-[calc(100dvh-52px)] bg-background overflow-hidden">
      {/* Chat Panel */}
      <div className="flex-1 flex flex-col min-w-0 min-h-0">
        {/* Header */}
        <header className="h-14 px-4 flex items-center justify-between border-b border-border bg-card shrink-0">
          <div className="flex items-center gap-3">
            <Link
              to="/docuform"
              className="p-2 rounded-lg hover:bg-muted transition-colors"
            >
              <ArrowLeft size={18} className="text-muted-foreground" />
            </Link>
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-violet-500 to-violet-700 flex items-center justify-center">
                <Bot size={16} className="text-white" />
              </div>
              <div>
                <h1 className="text-sm font-semibold text-foreground">
                  Template AI Assistant
                </h1>
                <p className="text-xs text-muted-foreground">
                  Detect and create content controls
                </p>
              </div>
            </div>
          </div>
          {messages.length > 0 && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => { setMessages([]); setHasModifications(false); }}
              className="gap-2"
            >
              <RefreshCw size={14} />
              Reset
            </Button>
          )}
        </header>

        {/* Messages */}
        <div
          ref={messagesContainerRef}
          className="flex-1 overflow-y-auto p-4 min-h-0"
        >
          {!documentFilename ? (
            <div className="flex flex-col items-center justify-center h-full gap-6">
              <div className="flex flex-col items-center gap-4 text-center">
                <div className="w-16 h-16 rounded-full bg-amber-500/20 flex items-center justify-center">
                  <FileText size={32} className="text-amber-500" />
                </div>
                <div>
                  <h2 className="text-xl font-semibold text-foreground">No Document Selected</h2>
                  <p className="text-sm text-muted-foreground mt-2 max-w-md">
                    Please select a template from the{" "}
                    <Link to="/docuform" className="text-violet-500 hover:underline">
                      Templates page
                    </Link>{" "}
                    to begin field detection.
                  </p>
                </div>
              </div>
            </div>
          ) : messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full gap-6">
              <div className="flex flex-col items-center gap-4 text-center">
                <div className="w-16 h-16 rounded-full bg-violet-500/20 flex items-center justify-center">
                  <Bot size={32} className="text-violet-500" />
                </div>
                <div>
                  <h2 className="text-xl font-semibold text-foreground">Ready to Analyze</h2>
                  <p className="text-sm text-muted-foreground mt-2 max-w-md">
                    Document <span className="font-medium text-foreground">{documentFilename}</span> is loaded.
                    Start a conversation to analyze fields or ask me what you'd like to do.
                  </p>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2 w-full max-w-md">
                {suggestedPrompts.map((prompt, index) => (
                  <Button
                    key={index}
                    variant="outline"
                    onClick={() => handleSuggestedPrompt(prompt.action)}
                    className="h-auto py-3 px-4 text-left justify-start"
                  >
                    <span className="text-sm">{prompt.title}</span>
                  </Button>
                ))}
              </div>
            </div>
          ) : (
            <div className="space-y-4 max-w-2xl mx-auto">
              {messages.map((message, index) => {
                const { textContent, toolInvocations } = processMessage(message);

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
                        <div className="w-8 h-8 rounded-full bg-violet-500 flex items-center justify-center">
                          <Bot className="w-5 h-5 text-white" />
                        </div>
                      </div>
                    )}

                    <div
                      className={cn(
                        "flex flex-col gap-2 max-w-[85%]",
                        message.role === "user" && "items-end"
                      )}
                    >
                      {message.role === "user" ? (
                        <div className="bg-primary text-primary-foreground rounded-2xl px-4 py-2.5 shadow-sm">
                          <p className="text-sm whitespace-pre-wrap">
                            {textContent}
                          </p>
                        </div>
                      ) : (
                        <>
                          {toolInvocations.length > 0 && (
                            <div className="flex flex-col gap-2 w-full">
                              {toolInvocations.map((toolInvocation: any) => {
                                if (toolInvocation.state !== "result") return null;
                                return (
                                  <ToolInvocationDisplay
                                    key={toolInvocation.toolCallId}
                                    toolName={toolInvocation.toolName}
                                    args={toolInvocation.args}
                                    result={toolInvocation.result}
                                  />
                                );
                              })}
                            </div>
                          )}

                          {textContent && (
                            <div className="bg-muted/50 rounded-2xl px-4 py-2.5 shadow-sm">
                              <div className="text-sm prose prose-sm dark:prose-invert max-w-none">
                                <Markdown>{textContent}</Markdown>
                              </div>
                            </div>
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
              <div
                ref={messagesEndRef}
                className="shrink-0 min-w-[24px] min-h-[24px]"
              />
            </div>
          )}
        </div>

        {/* Input */}
        <form
          onSubmit={handleSubmit}
          className="p-4 border-t border-border bg-card shrink-0"
        >
          <div className="flex gap-2 items-end max-w-2xl mx-auto">
            <Textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSubmit();
                }
              }}
              placeholder="Ask me to analyze fields, wrap text in content controls, or save the template..."
              className="min-h-[48px] max-h-[200px] overflow-hidden resize-none rounded-xl text-sm bg-muted py-3"
              rows={1}
              disabled={status === "submitted" || status === "streaming"}
            />
            {status === "streaming" ? (
              <Button
                type="button"
                onClick={stop}
                size="icon"
                variant="outline"
                className="h-11 w-11 shrink-0 rounded-xl"
              >
                <StopCircle className="w-5 h-5" />
              </Button>
            ) : (
              <Button
                type="submit"
                size="icon"
                disabled={!input.trim() || status === "submitted"}
                className="h-11 w-11 shrink-0 rounded-xl"
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  viewBox="0 0 24 24"
                  fill="currentColor"
                  className="w-5 h-5"
                >
                  <path d="M3.478 2.405a.75.75 0 00-.926.94l2.432 7.905H13.5a.75.75 0 010 1.5H4.984l-2.432 7.905a.75.75 0 00.926.94 60.519 60.519 0 0018.445-8.986.75.75 0 000-1.218A60.517 60.517 0 003.478 2.405z" />
                </svg>
              </Button>
            )}
          </div>
        </form>
      </div>

      {/* Resize Handle */}
      <div
        onMouseDown={handleResizeStart}
        className={cn(
          "w-1 hover:w-1.5 bg-border hover:bg-primary/50 cursor-col-resize transition-all shrink-0",
          isResizing && "w-1.5 bg-primary/50"
        )}
      />

      {/* Document Preview Panel */}
      <div
        className="flex flex-col bg-card shrink-0 min-h-0"
        style={{ width: previewWidth }}
      >
        <header className="h-14 px-4 flex items-center justify-between border-b border-border shrink-0">
          <div className="flex items-center gap-2">
            <FileText size={18} className="text-muted-foreground" />
            <span className="text-sm font-medium text-foreground">
              Document Preview
            </span>
          </div>
          {documentFilename && (
            <div className="flex items-center gap-2">
              {/* Version Toggle - always visible */}
              <div className="flex rounded-lg bg-muted p-0.5">
                <button
                  onClick={() => setPreviewMode("original")}
                  className={cn(
                    "py-1 px-2.5 rounded-md text-xs font-medium transition-colors",
                    previewMode === "original"
                      ? "bg-background text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                  )}
                >
                  Original
                </button>
                <button
                  onClick={() => setPreviewMode("working")}
                  className={cn(
                    "py-1 px-2.5 rounded-md text-xs font-medium transition-colors",
                    previewMode === "working"
                      ? "bg-background text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                  )}
                >
                  Working
                </button>
              </div>
              {hasModifications && (
                <span className="text-xs text-green-600 bg-green-500/10 px-2 py-1 rounded">
                  Modified
                </span>
              )}
              {/* Refresh Preview button */}
              <button
                onClick={() => renderDocxPreview()}
                className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                title="Refresh preview"
              >
                <RefreshCw size={14} />
              </button>
              {/* Save buttons - only show when there are modifications */}
              {hasModifications && (
                <>
                  <div className="w-px h-4 bg-border" />
                  <button
                    onClick={handleSave}
                    className="px-2 py-1 rounded-md text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                    title="Save (overwrite original)"
                  >
                    Save
                  </button>
                  <button
                    onClick={handleSaveAs}
                    className="px-2 py-1 rounded-md text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                    title="Save As (new file)"
                  >
                    Save As
                  </button>
                  <button
                    onClick={handleReset}
                    className="p-1.5 rounded-md text-red-400 hover:text-red-500 hover:bg-red-500/10 transition-colors"
                    title="Reset (discard changes)"
                  >
                    <RotateCcw size={14} />
                  </button>
                </>
              )}
            </div>
          )}
        </header>

        <div className="flex-1 overflow-auto min-h-0">
          {documentFilename ? (
            <div className="h-full">
              {/* Custom styles for the rendered document */}
              <style>{`
                .docx-preview-container {
                  padding: 16px;
                }
                .docx-preview-container .docx-wrapper {
                  background: white;
                  padding: 0;
                  max-width: 100%;
                  overflow-x: auto;
                }
                .docx-preview-container section.docx {
                  box-shadow: none;
                  padding: 0;
                  margin: 0;
                  max-width: 100%;
                  word-wrap: break-word;
                  overflow-wrap: break-word;
                }
                /* Content control highlights (applied via JavaScript) */
                .content-control-highlight {
                  background-color: rgba(139, 92, 246, 0.25);
                  border-bottom: 2px solid #8b5cf6;
                  padding: 1px 2px;
                  border-radius: 2px;
                  cursor: pointer;
                  transition: background-color 0.15s ease;
                }
                .content-control-highlight:hover {
                  background-color: rgba(139, 92, 246, 0.4);
                }
              `}</style>

              {/* Loading state */}
              {previewLoading && (
                <div className="flex items-center justify-center h-64">
                  <div className="text-center">
                    <Loader2 className="h-8 w-8 animate-spin text-muted-foreground mx-auto mb-2" />
                    <p className="text-sm text-muted-foreground">Loading document...</p>
                  </div>
                </div>
              )}

              {/* Error state */}
              {previewError && !previewLoading && (
                <div className="p-4">
                  <div className="bg-destructive/10 border border-destructive/30 rounded-md p-4">
                    <p className="text-sm text-destructive">{previewError}</p>
                    <Button
                      variant="outline"
                      size="sm"
                      className="mt-2"
                      onClick={renderDocxPreview}
                    >
                      Retry
                    </Button>
                  </div>
                </div>
              )}

              {/* DOCX Preview Container */}
              <div
                ref={previewContainerRef}
                className="bg-white min-h-[400px]"
                style={{ display: previewLoading ? "none" : "block" }}
              />

              {/* Content controls info */}
              {contentControls.length > 0 && !previewLoading && (
                <div className="px-4 py-3 border-t border-border bg-muted/30 text-xs text-muted-foreground">
                  {contentControls.length} content control{contentControls.length !== 1 ? "s" : ""} highlighted
                </div>
              )}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-full text-center p-4">
              <div className="w-16 h-16 rounded-full bg-muted flex items-center justify-center mb-4">
                <FileText size={32} className="text-muted-foreground" />
              </div>
              <h3 className="font-medium text-foreground mb-2">
                No Document Selected
              </h3>
              <p className="text-sm text-muted-foreground max-w-xs">
                Select a template from the{" "}
                <Link to="/docuform" className="text-violet-500 hover:underline">
                  Templates page
                </Link>{" "}
                to get started.
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Save Confirmation Dialog */}
      <AlertDialog open={showSaveConfirm} onOpenChange={setShowSaveConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Save Changes?</AlertDialogTitle>
            <AlertDialogDescription>
              This will overwrite the original document "{documentFilename}" with your changes.
              This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {saveError && (
            <p className="text-sm text-destructive">{saveError}</p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isSaving}>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleSaveConfirm} disabled={isSaving}>
              {isSaving ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Saving...
                </>
              ) : (
                "Save"
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Save As Dialog */}
      <Dialog open={showSaveAsDialog} onOpenChange={setShowSaveAsDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Save As</DialogTitle>
            <DialogDescription>
              Save a copy of the modified document with a new filename.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label htmlFor="filename">Filename</Label>
              <Input
                id="filename"
                value={saveAsFilename}
                onChange={(e) => setSaveAsFilename(e.target.value)}
                placeholder="my_template.docx"
              />
            </div>
            {saveError && (
              <p className="text-sm text-destructive">{saveError}</p>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowSaveAsDialog(false)} disabled={isSaving}>
              Cancel
            </Button>
            <Button onClick={handleSaveAsConfirm} disabled={isSaving || !saveAsFilename.trim()}>
              {isSaving ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Saving...
                </>
              ) : (
                "Save"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
