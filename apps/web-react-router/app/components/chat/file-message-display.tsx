import { FileText } from "lucide-react";
import type { FileSegment } from "~/components/chat/process-message";

export function FileMessageDisplay({ files }: { files: FileSegment[] }) {
  if (files.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-2">
      {files.map((file, index) => {
        if (file.mediaType === "application/pdf") {
          return (
            <div
              key={index}
              className="flex items-center gap-2 rounded-lg border bg-muted/50 px-3 py-2"
            >
              <FileText className="w-4 h-4 text-muted-foreground shrink-0" />
              <span className="text-xs text-muted-foreground truncate max-w-[160px]">
                {file.filename || "Document.pdf"}
              </span>
            </div>
          );
        }

        return (
          <img
            key={index}
            src={file.url}
            alt={file.filename || "Attached image"}
            className="max-w-[240px] rounded-lg"
          />
        );
      })}
    </div>
  );
}
