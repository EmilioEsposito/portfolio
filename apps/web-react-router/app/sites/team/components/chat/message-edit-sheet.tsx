import { useEffect, useRef, useState } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { RotateCcw } from "lucide-react";
import { Button } from "~/components/ui/button";
import { cn } from "~/lib/utils";

/**
 * Where the visible part of the page is: the iOS keyboard shrinks it and can
 * slide it down the page, and a fixed element that ignores this ends up half
 * under the keyboard with blank space above it.
 */
function useVisibleViewport(active: boolean) {
  const [viewport, setViewport] = useState<{ top: number; height: number } | null>(
    null,
  );
  useEffect(() => {
    const vv = typeof window === "undefined" ? undefined : window.visualViewport;
    if (!active || !vv) return;
    const update = () => setViewport({ top: vv.offsetTop, height: vv.height });
    update();
    vv.addEventListener("resize", update);
    vv.addEventListener("scroll", update);
    return () => {
      vv.removeEventListener("resize", update);
      vv.removeEventListener("scroll", update);
    };
  }, [active]);
  return viewport;
}

const MIRRORED_STYLES = [
  "boxSizing",
  "width",
  "paddingTop",
  "paddingRight",
  "paddingBottom",
  "paddingLeft",
  "borderTopWidth",
  "borderRightWidth",
  "borderBottomWidth",
  "borderLeftWidth",
  "fontFamily",
  "fontSize",
  "fontWeight",
  "fontStyle",
  "letterSpacing",
  "lineHeight",
  "textTransform",
  "wordSpacing",
  "textIndent",
  "tabSize",
] as const;

/**
 * Scroll a textarea so its caret shows. Browsers do this as you type, but not
 * when the textarea shrinks under the caret, which is what the iOS keyboard
 * does to this editor as it opens.
 */
function revealCaret(textarea: HTMLTextAreaElement) {
  const style = window.getComputedStyle(textarea);
  const mirror = document.createElement("div");
  for (const name of MIRRORED_STYLES) mirror.style[name] = style[name];
  mirror.style.position = "absolute";
  mirror.style.visibility = "hidden";
  mirror.style.top = "0";
  mirror.style.left = "-9999px";
  mirror.style.whiteSpace = "pre-wrap";
  mirror.style.overflowWrap = "break-word";
  mirror.textContent = textarea.value.slice(0, textarea.selectionEnd);
  const caret = document.createElement("span");
  caret.textContent = "​";
  mirror.appendChild(caret);
  document.body.appendChild(mirror);
  const caretTop = caret.offsetTop;
  const caretBottom = caretTop + caret.offsetHeight;
  mirror.remove();

  const { scrollTop, clientHeight } = textarea;
  if (caretTop < scrollTop || caretBottom > scrollTop + clientHeight) {
    textarea.scrollTop = Math.max(0, caretBottom - clientHeight * 0.6);
  }
}

/**
 * Full-screen editor for the message on an approval card. On a phone it
 * covers exactly the visible screen and keeps Cancel and Done above the
 * keyboard; on a wider screen it is a large dialog. Edits stay in the editor
 * until Done, so Cancel leaves the card as it was.
 */
export function MessageEditSheet({
  open,
  onOpenChange,
  recipient,
  draft,
  value,
  onSave,
  author = "Sernia AI",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Who the message goes to, shown under the title. */
  recipient: string;
  /** Sernia AI's draft, which "Restore draft" puts back. */
  draft: string;
  /** The message as it stands on the card when the editor opens. */
  value: string;
  onSave: (value: string) => void;
  /** Who wrote the draft: "Sernia AI", or "The lead agent" for a Zillow lead's reply. */
  author?: string;
}) {
  const [text, setText] = useState(value);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const viewport = useVisibleViewport(open);

  useEffect(() => {
    if (open) setText(value);
  }, [open, value]);

  // Keep the caret in sight as the keyboard opens or the screen turns.
  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea || document.activeElement !== textarea) return;
    const frame = requestAnimationFrame(() => revealCaret(textarea));
    return () => cancelAnimationFrame(frame);
  }, [viewport?.top, viewport?.height]);

  // An empty message is never sent; the card would quietly fall back to the draft.
  const canSave = text.trim() !== "";
  const save = () => {
    if (!canSave) return;
    onSave(text);
    onOpenChange(false);
  };
  const edited = text !== draft;
  const keyboardOpen =
    viewport !== null && window.innerHeight - viewport.height > 120;

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/60 max-md:hidden" />
        <DialogPrimitive.Content
          aria-describedby={undefined}
          style={
            viewport
              ? ({
                  "--sheet-top": `${viewport.top}px`,
                  "--sheet-h": `${viewport.height}px`,
                } as React.CSSProperties)
              : undefined
          }
          className={cn(
            "sheet-rise fixed z-50 flex flex-col bg-background outline-none",
            // Phone: exactly the visible screen, following the keyboard.
            "max-md:inset-x-0 max-md:top-[var(--sheet-top,0px)] max-md:h-[var(--sheet-h,100dvh)]",
            // Wider screens: a large centered dialog.
            "md:inset-0 md:m-auto md:h-[min(40rem,calc(100dvh-6rem))] md:w-[min(42rem,calc(100vw-4rem))] md:rounded-xl md:border md:shadow-lg",
          )}
          onOpenAutoFocus={(event) => {
            // On a phone the keyboard would cover half the message before
            // anyone has picked a spot; a tap in the text puts the caret there.
            if (window.matchMedia("(pointer: coarse)").matches) return;
            event.preventDefault();
            const textarea = textareaRef.current;
            if (!textarea) return;
            textarea.focus();
            textarea.setSelectionRange(textarea.value.length, textarea.value.length);
            revealCaret(textarea);
          }}
          // A stray click beside the dialog must not throw away an edit.
          onInteractOutside={(event) => event.preventDefault()}
        >
          <header className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 border-b px-2 pb-2 pt-[calc(0.5rem+env(safe-area-inset-top))] md:pt-2">
            <DialogPrimitive.Close asChild>
              <Button variant="ghost" className="justify-self-start">
                Cancel
              </Button>
            </DialogPrimitive.Close>
            <div className="min-w-0 text-center">
              <DialogPrimitive.Title className="text-sm font-semibold">
                Edit message
              </DialogPrimitive.Title>
              <p className="truncate text-xs text-muted-foreground">{recipient}</p>
            </div>
            <Button className="justify-self-end" onClick={save} disabled={!canSave}>
              Done
            </Button>
          </header>

          <textarea
            ref={textareaRef}
            value={text}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                event.preventDefault();
                save();
              }
            }}
            aria-label="Message"
            autoCapitalize="sentences"
            spellCheck
            className="min-h-0 w-full flex-1 resize-none overflow-y-auto overscroll-contain bg-transparent px-4 py-3 text-base leading-relaxed outline-none placeholder:text-muted-foreground"
          />

          <footer
            className={cn(
              "border-t px-4 text-xs text-muted-foreground",
              // The home-bar gap matters only while the keyboard is down.
              !keyboardOpen && "pb-[env(safe-area-inset-bottom)]",
            )}
          >
            <div className="flex min-h-11 items-center gap-2">
              {edited ? (
                <>
                  <span className="flex-1">Edited</span>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="-mr-2 shrink-0 gap-1 text-xs"
                    onClick={() => setText(draft)}
                  >
                    <RotateCcw className="h-3 w-3" />
                    Restore draft
                  </Button>
                </>
              ) : (
                <span>{author}&rsquo;s draft</span>
              )}
            </div>
          </footer>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
