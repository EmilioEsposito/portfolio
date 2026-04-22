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
 * @deprecated Use convertAllPendingFromApi for multi-approval support
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
 * Convert ALL pending approvals from API format to frontend format.
 * Returns empty array if none pending.
 */
export function convertAllPendingFromApi(
  pending: any[] | null
): PendingApproval[] {
  if (!pending || pending.length === 0) return [];
  return pending.map((p) => ({
    toolCallId: p.tool_call_id,
    toolName: p.tool_name,
    args: p.args || {},
  }));
}

/** Decision state for a single pending approval */
interface ApprovalItemDecision {
  approved: boolean | null; // null = undecided
  editedBody?: string;
}

export interface ApprovalDecisionPayload {
  tool_call_id: string;
  approved: boolean;
  reason?: string;
  override_args?: Record<string, any>;
}

/**
 * POST a batch of approve/deny decisions to the Sernia approve endpoint.
 * Shared between the approval card and the main chat input's "deny with feedback" path.
 */
export async function submitApprovalDecisions(params: {
  apiBase: string;
  conversationId: string;
  getToken: () => Promise<string | null>;
  decisions: ApprovalDecisionPayload[];
}): Promise<any> {
  const token = await params.getToken();
  const url = `${params.apiBase}/conversation/${params.conversationId}/approve`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ decisions: params.decisions }),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error.detail || "Failed to process approval");
  }
  return res.json();
}

/** Get a short summary for a tool call (e.g., recipient for emails/sms) */
function getToolSummary(p: PendingApproval): string {
  // For email tools, show recipient
  if (p.args?.to) {
    const to = Array.isArray(p.args.to) ? p.args.to.join(", ") : p.args.to;
    return `To: ${to}`;
  }
  // For SMS tools, show phone
  if (p.args?.phone_number || p.args?.to_phone_number) {
    return `To: ${p.args.phone_number || p.args.to_phone_number}`;
  }
  // For other tools, show tool name
  return p.toolName;
}

/** Get the message body from tool args */
function getMessageFromArgs(args: Record<string, any>): { key: string; value: string } {
  if (args?.message !== undefined) return { key: "message", value: args.message };
  if (args?.body !== undefined) return { key: "body", value: args.body };
  return { key: "", value: "" };
}

/**
 * Tool approval card with support for multiple pending approvals.
 * Shows all pending requests with individual approve/deny controls.
 * PydanticAI requires results for all deferred tool calls, so all must be decided.
 */
export function ToolApprovalCard({
  pending,
  allPending,
  conversationId,
  onApprovalComplete,
  isProcessing,
  getToken,
  apiBase,
}: {
  pending: PendingApproval;
  allPending?: PendingApproval[];
  conversationId: string;
  onApprovalComplete: (result: any) => void;
  isProcessing: boolean;
  getToken: () => Promise<string | null>;
  apiBase: string;
}) {
  const pendingList = allPending && allPending.length > 0 ? allPending : [pending];
  const hasMultiple = pendingList.length > 1;

  // Track individual decisions for each pending approval
  const [decisions, setDecisions] = useState<Record<string, ApprovalItemDecision>>(() => {
    const initial: Record<string, ApprovalItemDecision> = {};
    for (const p of pendingList) {
      initial[p.toolCallId] = { approved: null };
    }
    return initial;
  });
  const [processing, setProcessing] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(pendingList[0]?.toolCallId || null);

  const setItemDecision = (toolCallId: string, approved: boolean) => {
    setDecisions((prev) => ({
      ...prev,
      [toolCallId]: { ...prev[toolCallId], approved },
    }));
  };

  const setItemEditedBody = (toolCallId: string, editedBody: string) => {
    setDecisions((prev) => ({
      ...prev,
      [toolCallId]: { ...prev[toolCallId], editedBody },
    }));
  };

  // Check if all items have been decided
  const allDecided = pendingList.every((p) => decisions[p.toolCallId]?.approved !== null);
  const decidedCount = pendingList.filter((p) => decisions[p.toolCallId]?.approved !== null).length;

  const handleSubmitAll = async () => {
    if (!allDecided) return;

    setProcessing(true);
    try {
      const decisionList: ApprovalDecisionPayload[] = pendingList.map((p) => {
        const d = decisions[p.toolCallId];
        const { key: msgKey, value: originalValue } = getMessageFromArgs(p.args);
        const overrideArgs =
          d.editedBody && d.editedBody !== originalValue
            ? { [msgKey]: d.editedBody }
            : undefined;

        return {
          tool_call_id: p.toolCallId,
          approved: d.approved === true,
          override_args: overrideArgs,
          reason: d.approved ? undefined : "Denied by user",
        };
      });

      const result = await submitApprovalDecisions({
        apiBase,
        conversationId,
        getToken,
        decisions: decisionList,
      });
      onApprovalComplete(result);
    } catch (err) {
      console.error("Approval error:", err);
      alert(err instanceof Error ? err.message : "Failed to process approval");
    } finally {
      setProcessing(false);
    }
  };

  // Quick actions for single item or batch
  const handleQuickApproveAll = () => {
    const updated: Record<string, ApprovalItemDecision> = {};
    for (const p of pendingList) {
      updated[p.toolCallId] = { ...decisions[p.toolCallId], approved: true };
    }
    setDecisions(updated);
  };

  const handleQuickDenyAll = () => {
    const updated: Record<string, ApprovalItemDecision> = {};
    for (const p of pendingList) {
      updated[p.toolCallId] = { ...decisions[p.toolCallId], approved: false };
    }
    setDecisions(updated);
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
              {hasMultiple && (
                <span className="ml-1 text-xs text-amber-600 dark:text-amber-400">
                  ({decidedCount}/{pendingList.length} decided)
                </span>
              )}
            </CardTitle>
          </div>
          {!hasMultiple && <Badge variant="outline">{pending.toolName}</Badge>}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Individual approval items */}
        {pendingList.map((p, idx) => (
          <ApprovalItem
            key={p.toolCallId}
            item={p}
            index={idx}
            decision={decisions[p.toolCallId]}
            isExpanded={expandedId === p.toolCallId}
            onToggleExpand={() => setExpandedId(expandedId === p.toolCallId ? null : p.toolCallId)}
            onSetDecision={(approved) => setItemDecision(p.toolCallId, approved)}
            onSetEditedBody={(body) => setItemEditedBody(p.toolCallId, body)}
            disabled={isDisabled}
            showIndex={hasMultiple}
          />
        ))}

        {/* Quick actions and submit */}
        <div className="flex flex-wrap gap-2 pt-2 border-t">
          {hasMultiple && (
            <>
              <Button
                size="sm"
                variant="outline"
                onClick={handleQuickApproveAll}
                disabled={isDisabled}
                className="gap-1 text-xs"
              >
                <CheckCircle2 className="w-3 h-3" />
                Approve All
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={handleQuickDenyAll}
                disabled={isDisabled}
                className="gap-1 text-xs"
              >
                <XCircle className="w-3 h-3" />
                Deny All
              </Button>
              <div className="flex-1" />
            </>
          )}
          <Button
            size="sm"
            onClick={handleSubmitAll}
            disabled={isDisabled || !allDecided}
            className="gap-1"
          >
            {processing ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <CheckCircle2 className="w-4 h-4" />
            )}
            {processing ? "Processing..." : hasMultiple ? "Submit Decisions" : allDecided ? (decisions[pending.toolCallId]?.approved ? "Approve" : "Deny") : "Choose Action"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

/** Individual approval item with expand/collapse */
function ApprovalItem({
  item,
  index,
  decision,
  isExpanded,
  onToggleExpand,
  onSetDecision,
  onSetEditedBody,
  disabled,
  showIndex,
}: {
  item: PendingApproval;
  index: number;
  decision: ApprovalItemDecision;
  isExpanded: boolean;
  onToggleExpand: () => void;
  onSetDecision: (approved: boolean) => void;
  onSetEditedBody: (body: string) => void;
  disabled: boolean;
  showIndex: boolean;
}) {
  const { key: msgKey, value: msgValue } = getMessageFromArgs(item.args);
  const hasMessageArg = msgValue !== "";
  const [isEditing, setIsEditing] = useState(false);
  const editedBody = decision.editedBody ?? msgValue;

  // Decision indicator
  const decisionIcon =
    decision.approved === true ? (
      <CheckCircle2 className="w-4 h-4 text-green-600" />
    ) : decision.approved === false ? (
      <XCircle className="w-4 h-4 text-red-600" />
    ) : (
      <div className="w-4 h-4 rounded-full border-2 border-muted-foreground/30" />
    );

  return (
    <div className={cn(
      "rounded-lg border bg-background overflow-hidden",
      decision.approved === true && "border-green-300",
      decision.approved === false && "border-red-300",
      decision.approved === null && "border-border"
    )}>
      {/* Header - always visible */}
      <button
        onClick={onToggleExpand}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-muted/50 transition-colors text-left"
      >
        {decisionIcon}
        <span className="text-xs text-muted-foreground">
          {isExpanded ? "\u25BC" : "\u25B6"}
        </span>
        {showIndex && (
          <span className="text-xs font-medium text-muted-foreground">#{index + 1}</span>
        )}
        <Badge variant="outline" className="text-[10px] px-1.5 py-0">
          {item.toolName}
        </Badge>
        <span className="flex-1 text-xs truncate">{getToolSummary(item)}</span>
      </button>

      {/* Expanded content */}
      {isExpanded && (
        <div className="px-3 pb-3 space-y-2 border-t">
          {/* Full args */}
          <ToolDetailBox
            label="Input"
            content={JSON.stringify(item.args, null, 2)}
          />

          {/* Editable message */}
          {hasMessageArg && (
            <div className="text-sm">
              <div className="flex items-center justify-between mb-1">
                <Label className="text-muted-foreground text-xs">Message</Label>
                {!isEditing && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 px-2 text-xs"
                    onClick={() => setIsEditing(true)}
                    disabled={disabled}
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
                    onChange={(e) => onSetEditedBody(e.target.value)}
                    className="min-h-[80px] bg-background text-sm"
                    disabled={disabled}
                  />
                  {editedBody !== msgValue && (
                    <p className="text-xs text-amber-600 dark:text-amber-400">
                      Modified - will override AI's suggestion
                    </p>
                  )}
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setIsEditing(false);
                      onSetEditedBody(msgValue);
                    }}
                    disabled={disabled}
                  >
                    Cancel edit
                  </Button>
                </div>
              ) : (
                <p className="p-2 bg-muted/50 rounded border text-sm whitespace-pre-wrap">
                  {msgValue}
                </p>
              )}
            </div>
          )}

          {/* Approve/Deny buttons for this item */}
          <div className="flex gap-2 pt-1">
            <Button
              size="sm"
              variant={decision.approved === true ? "default" : "outline"}
              onClick={() => onSetDecision(true)}
              disabled={disabled}
              className="gap-1 flex-1"
            >
              <CheckCircle2 className="w-3 h-3" />
              Approve
            </Button>
            <Button
              size="sm"
              variant={decision.approved === false ? "destructive" : "outline"}
              onClick={() => onSetDecision(false)}
              disabled={disabled}
              className="gap-1 flex-1"
            >
              <XCircle className="w-3 h-3" />
              Deny
            </Button>
          </div>
        </div>
      )}
    </div>
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
