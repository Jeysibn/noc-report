import { useRef, useState, type DragEvent } from "react";
import { Upload } from "./icons";
import { cn } from "@/lib/cn";

export interface FileUploadProps {
  label?: string;
  accept?: string;
  onFileSelected: (file: File) => void;
}

/** Screenshot/evidence drop zone. Used by Create Incident's OCR panel and Evidence attachment. */
export function FileUpload({ label = "Drop a screenshot, or click to browse", accept, onFileSelected }: FileUploadProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setIsDragging(false);
    const file = event.dataTransfer.files?.[0];
    if (file) onFileSelected(file);
  }

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => inputRef.current?.click()}
      onKeyDown={(e) => e.key === "Enter" && inputRef.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        setIsDragging(true);
      }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={handleDrop}
      className={cn(
        "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-card border-2 border-dashed p-8 text-center transition-colors",
        isDragging ? "border-accent bg-info-tint" : "border-border bg-ground/40",
      )}
    >
      <Upload className="h-6 w-6 text-muted" />
      <p className="text-sm text-muted">{label}</p>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onFileSelected(file);
        }}
      />
    </div>
  );
}
