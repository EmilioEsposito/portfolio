import { useState, useRef, useCallback } from "react";

const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5 MB
const ACCEPTED_TYPES = [
  "image/jpeg",
  "image/png",
  "image/gif",
  "image/webp",
  "application/pdf",
] as const;

const ACCEPT_STRING = ACCEPTED_TYPES.join(",");

export interface FileAttachment {
  type: "file";
  mediaType: string;
  url: string; // data URI (base64)
  filename?: string;
}

function readFileAsDataURL(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function isAcceptedType(type: string): boolean {
  return (ACCEPTED_TYPES as readonly string[]).includes(type);
}

export function useFileAttachments() {
  const [files, setFiles] = useState<FileAttachment[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dragCounterRef = useRef(0);

  const addFiles = useCallback(async (fileList: FileList | File[]) => {
    const newFiles: FileAttachment[] = [];

    for (const file of Array.from(fileList)) {
      if (!isAcceptedType(file.type)) continue;
      if (file.size > MAX_FILE_SIZE) continue;

      const dataUrl = await readFileAsDataURL(file);
      newFiles.push({
        type: "file",
        mediaType: file.type,
        url: dataUrl,
        filename: file.name,
      });
    }

    if (newFiles.length > 0) {
      setFiles((prev) => [...prev, ...newFiles]);
    }
  }, []);

  const removeFile = useCallback((index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const clearFiles = useCallback(() => {
    setFiles([]);
  }, []);

  const openFilePicker = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleFileInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files) {
        addFiles(e.target.files);
      }
      // Reset so the same file can be re-selected
      e.target.value = "";
    },
    [addFiles]
  );

  const handlePaste = useCallback(
    (e: React.ClipboardEvent) => {
      const items = e.clipboardData?.items;
      if (!items) return;

      const imageFiles: File[] = [];
      for (const item of Array.from(items)) {
        if (item.kind === "file" && isAcceptedType(item.type)) {
          const file = item.getAsFile();
          if (file) imageFiles.push(file);
        }
      }

      if (imageFiles.length > 0) {
        e.preventDefault();
        addFiles(imageFiles);
      }
    },
    [addFiles]
  );

  const dropTargetProps = {
    onDragEnter: (e: React.DragEvent) => {
      e.preventDefault();
      dragCounterRef.current++;
      if (dragCounterRef.current === 1) setIsDragging(true);
    },
    onDragOver: (e: React.DragEvent) => {
      e.preventDefault();
    },
    onDragLeave: (e: React.DragEvent) => {
      e.preventDefault();
      dragCounterRef.current--;
      if (dragCounterRef.current === 0) setIsDragging(false);
    },
    onDrop: (e: React.DragEvent) => {
      e.preventDefault();
      dragCounterRef.current = 0;
      setIsDragging(false);
      if (e.dataTransfer.files.length > 0) {
        addFiles(e.dataTransfer.files);
      }
    },
  };

  return {
    files,
    hasFiles: files.length > 0,
    isDragging,
    fileInputRef,
    acceptString: ACCEPT_STRING,
    addFiles,
    removeFile,
    clearFiles,
    openFilePicker,
    handleFileInputChange,
    handlePaste,
    dropTargetProps,
  };
}
