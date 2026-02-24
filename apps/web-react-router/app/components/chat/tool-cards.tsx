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

/** Completed tool result display — always shows result, expand for input details */
export function ToolResultCard({
  toolName,
  args,
  result,
}: {
  toolName: string;
  args?: Record<string, any>;
  result: string;
}) {
  const [isExpanded, setIsExpanded] = useState(false);
  const isDenied =
    result === "Denied by user" ||
    result === "The tool call was denied." ||
    result.startsWith("Denied:");

  const isTruncated = result.length > RESULT_TRUNCATE_LIMIT;
  const displayResult = isTruncated
    ? result.slice(0, RESULT_TRUNCATE_LIMIT) + "…"
    : result;

  const hasArgs = args && Object.keys(args).length > 0;

  return (
    <Card
      className={cn(
        "border",
        isDenied
          ? "border-red-300 bg-red-50/50 dark:bg-red-950/10"
          : "border-green-300 bg-green-50/50 dark:bg-green-950/10"
      )}
    >
      <CardHeader className="pb-0 py-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {isDenied ? (
              <XCircle className="w-4 h-4 text-red-500 shrink-0" />
            ) : (
              <CheckCircle2 className="w-4 h-4 text-green-500 shrink-0" />
            )}
            <span className="text-sm font-medium">
              {isDenied ? "Action Denied" : "Action Completed"}
            </span>
          </div>
          <Badge variant="outline" className="text-xs">
            {toolName}
          </Badge>
        </div>
      </CardHeader>

      {/* Result — always visible */}
      <CardContent className="pt-1 pb-2 space-y-2">
        <div
          className={cn(
            "text-xs whitespace-pre-wrap rounded border p-2 bg-background",
            isDenied
              ? "text-red-600 dark:text-red-400"
              : "text-green-700 dark:text-green-400",
            // Scrollable for medium-length results
            displayResult.length > 200 && "max-h-[160px] overflow-y-auto"
          )}
        >
          {displayResult}
          {isTruncated && (
            <span className="text-muted-foreground"> (truncated)</span>
          )}
        </div>

        {/* Expandable input details */}
        {hasArgs && (
          <>
            <button
              onClick={() => setIsExpanded(!isExpanded)}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors flex items-center gap-1"
            >
              <span>{isExpanded ? "\u25BC" : "\u25B6"}</span>
              Input details
            </button>
            {isExpanded && (
              <div className="text-sm">
                {args.to && (
                  <p className="text-xs">
                    <span className="text-muted-foreground">To:</span>{" "}
                    {args.to}
                  </p>
                )}
                {(args.body || args.message) && (
                  <p className="text-xs mt-1 p-2 bg-background rounded border whitespace-pre-wrap">
                    {args.body || args.message}
                  </p>
                )}
                {!args.to && !args.body && !args.message && (
                  <pre className="text-xs overflow-x-auto bg-background p-2 rounded border mt-1">
                    {JSON.stringify(args, null, 2)}
                  </pre>
                )}
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
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
  const [isExpanded, setIsExpanded] = useState(false);

  return (
    <div className="border border-border rounded-lg overflow-hidden bg-muted/20">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full px-4 py-2 text-left flex items-center justify-between hover:bg-muted/50 transition-colors"
      >
        <span className="text-sm font-medium flex items-center gap-2">
          <Zap className="w-4 h-4" />
          Tool:{" "}
          <code className="text-xs bg-muted px-1.5 py-0.5 rounded">
            {toolName}
          </code>
        </span>
        <span className="text-xs text-muted-foreground">
          {isExpanded ? "\u25BC" : "\u25B6"}
        </span>
      </button>
      {isExpanded && (
        <div className="px-4 py-3 bg-muted/30 border-t border-border space-y-2">
          {args && (
            <div>
              <div className="text-xs font-medium text-muted-foreground mb-1">
                Input:
              </div>
              <pre className="text-xs overflow-x-auto bg-background p-2 rounded border">
                {JSON.stringify(args, null, 2)}
              </pre>
            </div>
          )}
          {result && (
            <div>
              <div className="text-xs font-medium text-muted-foreground mb-1">
                Output:
              </div>
              <pre className="text-xs overflow-x-auto bg-background p-2 rounded border">
                {typeof result === "string"
                  ? result
                  : JSON.stringify(result, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
