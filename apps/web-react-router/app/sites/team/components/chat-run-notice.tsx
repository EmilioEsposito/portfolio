import type { ChatRunStatus } from "../lib/sernia-chat-recovery";

export function ChatRunNotice({ run }: { run: ChatRunStatus }) {
  if (run.status === "failed" && run.credit_limit) {
    const { daily, key_id } = run.credit_limit;
    const href = key_id && /^[a-f0-9]{64}$/.test(key_id)
      ? `https://openrouter.ai/workspaces/default/keys/${key_id}`
      : "https://openrouter.ai/settings/keys";
    return (
      <div role="alert" className="rounded-lg border border-destructive/40 px-4 py-3 text-sm">
        <p className="font-medium">
          {daily ? "Daily AI usage credits" : "AI usage credits"} have been
          depleted for Sernia AI.
        </p>
        <p className="mt-1">
          Tell Emilio to investigate or increase the budget at{" "}
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium underline underline-offset-2"
          >
            OpenRouter
          </a>
          .
        </p>
      </div>
    );
  }
  if (!["failed", "cancelled", "interrupted"].includes(run.status)) return null;
  return (
    <p role="status" className="rounded-lg border px-4 py-3 text-sm text-muted-foreground">
      {run.status === "cancelled" ? "Response cancelled." : "The response stopped early."}{" "}
      Your message and saved progress are kept. You can send a follow-up. Actions already completed are not undone.
    </p>
  );
}
