import { useState } from "react";
import { Button } from "~/components/ui/button";
import { Textarea } from "~/components/ui/textarea";
import { Badge } from "~/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "~/components/ui/card";
import { Label } from "~/components/ui/label";
import { cn } from "~/lib/utils";
import {
  AlertCircle,
  CheckCircle2,
  XCircle,
  Loader2,
  Edit3,
  Zap,
} from "lucide-react";

export interface PendingApproval {
  toolCallId: string;
  toolName: string;
  args: Record<string, any>;
}

/**
 * Convert pending approvals from API format (snake_case array) to frontend format (camelCase single).
 * API returns: [{tool_call_id, tool_name, args}, ...] or empty array/null
 * Frontend expects: {toolCallId, toolName, args} or null
 */
export function convertPendingFromApi(
  pending: any[] | null
): PendingApproval | null {
  if (!pending || pending.length === 0) return null;
  const first = pending[0];
  return {
    toolCallId: first.tool_call_id,
    toolName: first.tool_name,
    args: first.args || {},
  };
}

/**
 * Tool approval card with edit capability.
 * `apiBase` controls where the approval POST goes (e.g. "/api/ai-demos/hitl-agent" or "/api/sernia-ai").
 */
export function ToolApprovalCard({
  pending,
  conversationId,
  onApprovalComplete,
  isProcessing,
  getToken,
  apiBase,
}: {
  pending: PendingApproval;
  conversationId: string;
  onApprovalComplete: (result: any) => void;
  isProcessing: boolean;
  getToken: () => Promise<string | null>;
  apiBase: string;
}) {
  const [isEditing, setIsEditing] = useState(false);
  // Support both "body" and "message" arg names
  const messageArgKey = pending.args?.message !== undefined ? "message" : "body";
  const messageArgValue = pending.args?.[messageArgKey] || "";
  const [editedBody, setEditedBody] = useState(messageArgValue);
  const [processing, setProcessing] = useState(false);

  const handleApproval = async (approved: boolean) => {
    setProcessing(true);
    try {
      const token = await getToken();
      const overrideArgs =
        isEditing && editedBody !== messageArgValue
          ? { [messageArgKey]: editedBody }
          : undefined;

      const url = `${apiBase}/conversation/${conversationId}/approve`;

      const res = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          decisions: [
            {
              tool_call_id: pending.toolCallId,
              approved,
              override_args: overrideArgs,
              reason: approved ? undefined : "Denied by user",
            },
          ],
        }),
      });

      if (!res.ok) {
        const error = await res.json();
        throw new Error(error.detail || "Failed to process approval");
      }

      const result = await res.json();
      onApprovalComplete(result);
    } catch (err) {
      console.error("Approval error:", err);
      alert(
        err instanceof Error ? err.message : "Failed to process approval"
      );
    } finally {
      setProcessing(false);
    }
  };

  const isDisabled = processing || isProcessing;

  return (
    <Card className="border-2 border-amber-500 bg-amber-50 dark:bg-amber-950/20">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-5 h-5 text-amber-500" />
            <CardTitle className="text-sm font-medium">
              Approval Required
            </CardTitle>
          </div>
          <Badge variant="outline">{pending.toolName}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Recipient */}
        {pending.args?.to && (
          <div className="text-sm">
            <Label className="text-muted-foreground text-xs">To</Label>
            <p className="font-mono">{pending.args.to}</p>
          </div>
        )}
        {!pending.args?.to && (
          <div className="text-sm">
            <Label className="text-muted-foreground text-xs">To</Label>
            <p className="font-mono text-muted-foreground">
              Default (Emilio)
            </p>
          </div>
        )}

        {/* Message - editable */}
        <div className="text-sm">
          <div className="flex items-center justify-between mb-1">
            <Label className="text-muted-foreground text-xs">Message</Label>
            {!isEditing && (
              <Button
                variant="ghost"
                size="sm"
                className="h-6 px-2 text-xs"
                onClick={() => setIsEditing(true)}
                disabled={isDisabled}
              >
                <Edit3 className="w-3 h-3 mr-1" />
                Edit
              </Button>
            )}
          </div>
          {isEditing ? (
            <div className="space-y-2">
              <Textarea
                value={editedBody}
                onChange={(e) => setEditedBody(e.target.value)}
                className="min-h-[80px] bg-background"
                disabled={isDisabled}
              />
              {editedBody !== messageArgValue && (
                <p className="text-xs text-amber-600 dark:text-amber-400">
                  Modified - will override AI's suggestion
                </p>
              )}
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setIsEditing(false);
                  setEditedBody(messageArgValue);
                }}
                disabled={isDisabled}
              >
                Cancel edit
              </Button>
            </div>
          ) : (
            <p className="p-2 bg-background rounded border whitespace-pre-wrap">
              {messageArgValue || "(No message body)"}
            </p>
          )}
        </div>

        {/* Actions */}
        <div className="flex gap-2 pt-2">
          <Button
            size="sm"
            onClick={() => handleApproval(true)}
            disabled={isDisabled}
            className="gap-1"
          >
            {processing ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <CheckCircle2 className="w-4 h-4" />
            )}
            {processing ? "Sending..." : "Approve & Send"}
          </Button>
          <Button
            size="sm"
            variant="destructive"
            onClick={() => handleApproval(false)}
            disabled={isDisabled}
            className="gap-1"
          >
            <XCircle className="w-4 h-4" />
            Deny
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// Hard truncation limit for very long tool results (chars).
const RESULT_TRUNCATE_LIMIT = 2000;
// Preview limit for collapsed sub-boxes (chars).
const PREVIEW_LIMIT = 120;

/** Expandable sub-box for input/output inside a tool card */
function ToolDetailBox({
  label,
  content,
  color,
}: {
  label: string;
  content: string;
  color?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const isLong = content.length > PREVIEW_LIMIT;
  const preview = isLong ? content.slice(0, PREVIEW_LIMIT) + "…" : content;

  return (
    <div className="rounded border bg-background overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-1.5 px-2 py-1 text-xs text-muted-foreground hover:bg-muted/50 transition-colors"
      >
        <span className="shrink-0">{expanded ? "\u25BC" : "\u25B6"}</span>
        <span className="font-medium">{label}</span>
      </button>
      <div
        className={cn(
          "px-2 pb-1.5 text-xs whitespace-pre-wrap break-all",
          color,
          !expanded && "line-clamp-2"
        )}
      >
        {expanded ? content : preview}
      </div>
    </div>
  );
}

/** Completed tool result display — collapsed by default, with expandable input/output sub-boxes */
export function ToolResultCard({
  toolName,
  args,
  result,
}: {
  toolName: string;
  args?: Record<string, any>;
  result: string;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const isDenied =
    result === "Denied by user" ||
    result === "The tool call was denied." ||
    result.startsWith("Denied:");

  const isTruncated = result.length > RESULT_TRUNCATE_LIMIT;
  const displayResult = isTruncated
    ? result.slice(0, RESULT_TRUNCATE_LIMIT) + "…"
    : result;

  const inputText = args != null
    ? typeof args === "string"
      ? args
      : JSON.stringify(args, null, 2)
    : "(no input)";

  return (
    <div
      className={cn(
        "rounded-lg border text-sm overflow-hidden",
        isDenied
          ? "border-red-300 bg-red-50/50 dark:bg-red-950/10"
          : "border-green-300 bg-green-50/50 dark:bg-green-950/10"
      )}
    >
      {/* Clickable header — always visible */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-muted/30 transition-colors"
      >
        {isDenied ? (
          <XCircle className="w-3.5 h-3.5 text-red-500 shrink-0" />
        ) : (
          <CheckCircle2 className="w-3.5 h-3.5 text-green-500 shrink-0" />
        )}
        <span className="text-xs font-medium flex-1 text-left">
          {isDenied ? "Denied" : "Completed"}
        </span>
        <Badge variant="outline" className="text-[10px] px-1.5 py-0">
          {toolName}
        </Badge>
        <span className="text-[10px] text-muted-foreground">
          {isOpen ? "\u25BC" : "\u25B6"}
        </span>
      </button>

      {/* Expandable details — input then output */}
      {isOpen && (
        <div className="px-3 pb-2 space-y-1.5">
          <ToolDetailBox label="Input" content={inputText} />
          <ToolDetailBox
            label="Output"
            content={displayResult + (isTruncated ? " (truncated)" : "")}
            color={
              isDenied
                ? "text-red-600 dark:text-red-400"
                : "text-green-700 dark:text-green-400"
            }
          />
        </div>
      )}
    </div>
  );
}

/** Generic tool invocation display */
export function ToolInvocationDisplay({
  toolName,
  args,
  result,
}: {
  toolName: string;
  args?: any;
  result?: any;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const inputText = args ? JSON.stringify(args, null, 2) : "";
  const outputText = result
    ? typeof result === "string"
      ? result
      : JSON.stringify(result, null, 2)
    : "";

  return (
    <div className="rounded-lg border border-border overflow-hidden bg-muted/20">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-muted/50 transition-colors"
      >
        <Zap className="w-3.5 h-3.5 shrink-0" />
        <span className="text-xs font-medium flex-1 text-left">Tool</span>
        <Badge variant="outline" className="text-[10px] px-1.5 py-0">
          {toolName}
        </Badge>
        <span className="text-[10px] text-muted-foreground">
          {isOpen ? "\u25BC" : "\u25B6"}
        </span>
      </button>
      {isOpen && (
        <div className="px-3 pb-2 space-y-1.5">
          {inputText && <ToolDetailBox label="Input" content={inputText} />}
          {outputText && <ToolDetailBox label="Output" content={outputText} />}
        </div>
      )}
    </div>
  );
}
