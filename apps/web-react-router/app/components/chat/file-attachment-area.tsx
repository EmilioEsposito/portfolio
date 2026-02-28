import { Paperclip, X, FileText } from "lucide-react";
import { Button } from "~/components/ui/button";
import type { FileAttachment } from "~/hooks/use-file-attachments";

export function FileAttachmentButton({
  onClick,
  disabled,
}: {
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      onClick={onClick}
      disabled={disabled}
      className="h-9 w-9 shrink-0 rounded-lg"
      title="Attach file"
    >
      <Paperclip className="w-4 h-4" />
    </Button>
  );
}

export function FilePreviewStrip({
  files,
  onRemove,
}: {
  files: FileAttachment[];
  onRemove: (index: number) => void;
}) {
  if (files.length === 0) return null;

  return (
    <div className="flex gap-2 overflow-x-auto pb-1">
      {files.map((file, index) => (
        <div
          key={index}
          className="relative shrink-0 w-16 h-16 rounded-lg border bg-muted/50 overflow-hidden group"
        >
          {file.mediaType === "application/pdf" ? (
            <div className="flex flex-col items-center justify-center h-full gap-0.5">
              <FileText className="w-5 h-5 text-muted-foreground" />
              <span className="text-[9px] text-muted-foreground truncate max-w-[56px] px-0.5">
                {file.filename || "PDF"}
              </span>
            </div>
          ) : (
            <img
              src={file.url}
              alt={file.filename || "Attachment"}
              className="w-full h-full object-cover"
            />
          )}
          <button
            type="button"
            onClick={() => onRemove(index)}
            className="absolute -top-1 -right-1 w-5 h-5 rounded-full bg-destructive text-destructive-foreground flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
          >
            <X className="w-3 h-3" />
          </button>
        </div>
      ))}
    </div>
  );
}
