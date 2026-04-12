import {
  useState,
  useRef,
  useEffect,
  useCallback,
  useImperativeHandle,
  forwardRef,
} from "react";
import { cn } from "~/lib/utils";

/**
 * A chat input that uses a contentEditable div instead of <textarea>.
 *
 * iOS shows a native autofill accessory bar (keys, credit card, contacts)
 * above the keyboard for <textarea> and <input> elements. There is no
 * reliable HTML attribute to suppress it. Using a contentEditable div
 * avoids the bar entirely — this is the same approach used by ChatGPT,
 * Claude.ai, and other chat UIs.
 */

export interface ChatInputHandle {
  focus: () => void;
  element: HTMLDivElement | null;
}

interface ChatInputProps {
  value: string;
  onValueChange: (value: string) => void;
  onKeyDown?: (e: React.KeyboardEvent<HTMLDivElement>) => void;
  onPaste?: (e: React.ClipboardEvent<HTMLDivElement>) => void;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  maxHeightClass?: string;
}

export const ChatInput = forwardRef<ChatInputHandle, ChatInputProps>(
  (
    {
      value,
      onValueChange,
      onKeyDown,
      onPaste,
      placeholder,
      disabled = false,
      className,
      maxHeightClass = "max-h-[calc(75dvh)]",
    },
    ref
  ) => {
    const divRef = useRef<HTMLDivElement>(null);
    // Track the last value we received from user input so we can detect
    // external value changes (e.g. clearing after submit).
    const lastInputValue = useRef(value);
    // Track empty state for placeholder display.
    // CSS :empty is unreliable with contentEditable (browsers may leave <br> nodes).
    const [isEmpty, setIsEmpty] = useState(!value);

    useImperativeHandle(ref, () => ({
      focus: () => divRef.current?.focus(),
      get element() {
        return divRef.current;
      },
    }));

    // Sync when value changes externally (not from typing).
    // The most common case is clearing the input after submit.
    useEffect(() => {
      if (!divRef.current) return;
      if (value !== lastInputValue.current) {
        divRef.current.textContent = value;
        lastInputValue.current = value;
        setIsEmpty(!value);
      }
    }, [value]);

    const handleInput = useCallback(() => {
      const text = divRef.current?.textContent ?? "";
      lastInputValue.current = text;
      setIsEmpty(!text);
      onValueChange(text);
    }, [onValueChange]);

    const handlePaste = useCallback(
      (e: React.ClipboardEvent<HTMLDivElement>) => {
        // Insert pasted content as plain text to avoid HTML formatting
        const text = e.clipboardData.getData("text/plain");
        if (text) {
          e.preventDefault();
          // Insert plain text at the current selection
          const selection = window.getSelection();
          if (selection && selection.rangeCount > 0) {
            const range = selection.getRangeAt(0);
            range.deleteContents();
            range.insertNode(document.createTextNode(text));
            // Move cursor to end of inserted text
            range.collapse(false);
            selection.removeAllRanges();
            selection.addRange(range);
          }
          handleInput();
        }
        // Delegate to parent for file paste handling
        onPaste?.(e);
      },
      [handleInput, onPaste]
    );

    return (
      <div className="relative flex-1">
        {/* Placeholder overlay — positioned behind the editable content */}
        {isEmpty && placeholder && (
          <div
            className="pointer-events-none absolute inset-0 flex items-center px-3 py-2 text-base md:text-sm text-muted-foreground select-none"
            aria-hidden="true"
          >
            {placeholder}
          </div>
        )}
        <div
          ref={divRef}
          // "plaintext-only" is a WebKit/Blink extension that prevents rich-text
          // formatting and may further signal to iOS that this is not a form field.
          // Falls back to contentEditable="true" on unsupported browsers.
          contentEditable={disabled ? false : ("plaintext-only" as any)}
          // enterKeyHint tells iOS this is a send-type input (chat), not a form
          enterKeyHint="send"
          role="textbox"
          aria-multiline="true"
          aria-placeholder={placeholder}
          aria-disabled={disabled || undefined}
          // Suppress all autofill/autocorrect signals
          autoCorrect="off"
          autoCapitalize="sentences"
          suppressContentEditableWarning
          onInput={handleInput}
          onKeyDown={disabled ? undefined : onKeyDown}
          onPaste={disabled ? undefined : handlePaste}
          className={cn(
            // Base styles matching the Textarea component
            "w-full rounded-md border border-input bg-transparent px-3 py-2 text-base shadow-sm",
            "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
            "md:text-sm",
            // contentEditable-specific: grows with content, scrolls when large
            "min-h-0 overflow-y-auto overflow-x-hidden resize-none break-words whitespace-pre-wrap",
            maxHeightClass,
            // Disabled styling
            disabled && "opacity-50 cursor-not-allowed",
            className
          )}
        />
      </div>
    );
  }
);

ChatInput.displayName = "ChatInput";
