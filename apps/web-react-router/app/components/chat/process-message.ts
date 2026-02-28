/**
 * Process a Vercel AI SDK message into ordered segments preserving
 * the original interleaving of text and tool calls.
 */

export interface TextSegment {
  type: "text";
  content: string;
}

export interface ToolSegment {
  type: "tool";
  toolCallId: string;
  toolName: string;
  args: any;
  result: any;
}

export type MessageSegment = TextSegment | ToolSegment;

function isToolPart(part: any): boolean {
  return (
    part.type?.startsWith("tool-") ||
    part.type === "tool-invocation" ||
    part.type === "tool-call" ||
    part.type === "dynamic-tool" ||
    !!part.toolCallId ||
    !!part.tool_call_id
  );
}

function isCompletedTool(part: any): boolean {
  if (!isToolPart(part)) return false;
  return (
    part.result !== undefined ||
    part.output !== undefined ||
    part.state === "output-available"
  );
}

function parseToolPart(part: any): ToolSegment {
  let toolName = part.tool_name || part.toolName || part.name;
  if (
    !toolName &&
    part.type?.startsWith("tool-") &&
    part.type !== "tool-invocation" &&
    part.type !== "tool-call"
  ) {
    toolName = part.type.replace("tool-", "");
  }

  let args = part.args || part.input;
  if (typeof args === "string") {
    try {
      args = JSON.parse(args);
    } catch {
      // Keep as string if not valid JSON
    }
  }

  return {
    type: "tool",
    toolCallId: part.toolCallId || part.tool_call_id || part.id,
    toolName: toolName || "unknown",
    args,
    result: part.result || part.output || "Completed",
  };
}

/**
 * Process a message into ordered segments (text and completed tool calls)
 * preserving the original part ordering.
 */
export function processMessage(message: any): {
  segments: MessageSegment[];
} {
  // Simple content string — single text segment
  if (message.content && typeof message.content === "string") {
    return {
      segments: [{ type: "text", content: message.content }],
    };
  }

  if (!message.parts || !Array.isArray(message.parts)) {
    return { segments: [] };
  }

  const segments: MessageSegment[] = [];
  let pendingText = "";

  const flushText = () => {
    if (pendingText) {
      segments.push({ type: "text", content: pendingText });
      pendingText = "";
    }
  };

  for (const part of message.parts) {
    if (part.type === "text") {
      pendingText += part.text;
    } else if (isCompletedTool(part)) {
      flushText();
      segments.push(parseToolPart(part));
    }
    // Skip non-completed tool parts (in-progress, pending approval, etc.)
  }

  flushText();

  return { segments };
}
