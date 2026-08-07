export interface PendingRequestMarker {
  fingerprint: string;
  occurrence: number;
  createdAt?: number;
}

// IDs are regenerated when messages are dumped from the DB. Match submitted
// user turns by a digest of stable visible content instead. The digest keeps
// raw prompts and filenames out of sessionStorage while remaining comparable
// to the authoritative DB copy after a remount.
export async function userMessageFingerprint(message: any): Promise<string> {
  if (message?.role !== "user") return "";
  const parts = Array.isArray(message.parts) ? message.parts : [];
  const stableContent = parts
    .map((part: any) => {
      if (part.type === "text") return `text:${part.text ?? ""}`;
      if (part.type === "file") {
        // PydanticAI's Vercel adapter does not round-trip filenames when it
        // dumps URL/BinaryContent parts back to UI messages. Media type is the
        // stable field; occurrence ordering disambiguates repeated uploads.
        return `file:${part.mediaType ?? ""}`;
      }
      return part.type ?? "unknown";
    })
    .join("\u0000");

  const bytes = new TextEncoder().encode(stableContent);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

export async function serverHasResponseForRequest(
  serverMessages: any[],
  marker: PendingRequestMarker,
): Promise<boolean> {
  let occurrence = 0;
  let submittedUserIndex = -1;

  for (let index = 0; index < serverMessages.length; index += 1) {
    if (
      (await userMessageFingerprint(serverMessages[index])) !==
      marker.fingerprint
    ) {
      continue;
    }
    occurrence += 1;
    if (occurrence === marker.occurrence) {
      submittedUserIndex = index;
      break;
    }
  }

  return (
    submittedUserIndex >= 0 &&
    serverMessages
      .slice(submittedUserIndex + 1)
      .some((message: any) => message.role === "assistant")
  );
}
