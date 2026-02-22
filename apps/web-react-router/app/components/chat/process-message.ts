/**
 * Process a Vercel AI SDK message to extract text content and completed tool invocations.
 *
 * Handles multiple message formats:
 * - Standard `content` string
 * - `parts` array with text and tool parts
 * - Tool parts from PydanticAI (type: "tool-{name}", "dynamic-tool", etc.)
 */
export function processMessage(message: any): {
  textContent: string;
  completedTools: {
    toolCallId: string;
    toolName: string;
    args: any;
    result: any;
  }[];
} {
  let textContent = "";
  if (message.content && typeof message.content === "string") {
    textContent = message.content;
  } else if (message.parts && Array.isArray(message.parts)) {
    const textParts = message.parts.filter(
      (part: any) => part.type === "text"
    );
    textContent = textParts.map((p: any) => p.text).join("");
  }

  let completedTools: any[] = [];
  if (message.parts && Array.isArray(message.parts)) {
    completedTools = message.parts
      .filter((part: any) => {
        const isToolPart =
          part.type?.startsWith("tool-") ||
          part.type === "tool-invocation" ||
          part.type === "tool-call" ||
          part.type === "dynamic-tool" ||
          part.toolCallId ||
          part.tool_call_id;
        if (!isToolPart) return false;

        const hasOutput =
          part.result !== undefined || part.output !== undefined;
        const isOutputAvailable = part.state === "output-available";

        return hasOutput || isOutputAvailable;
      })
      .map((part: any) => {
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
          toolCallId: part.toolCallId || part.tool_call_id || part.id,
          toolName: toolName || "unknown",
          args,
          result: part.result || part.output || "Completed",
        };
      });
  }

  return { textContent, completedTools };
}
