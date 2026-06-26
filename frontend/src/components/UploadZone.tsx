import { useCallback, useRef, useState, DragEvent, ChangeEvent } from "react";

const ACCEPT = ".wav,.mp3,.flac,.ogg,audio/wav,audio/mpeg,audio/flac,audio/ogg";

interface Props {
  onFile: (file: File) => void;
  disabled?: boolean;
  error?: string | null;
}

/**
 * Drag-and-drop upload zone with click-to-pick fallback.
 * Validates file type client-side; the backend enforces the 50 MB limit.
 */
export default function UploadZone({ onFile, disabled, error }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const handleFiles = useCallback(
    (files: FileList | null) => {
      if (!files || files.length === 0) return;
      const file = files[0];
      const lower = file.name.toLowerCase();
      if (
        !lower.endsWith(".wav") &&
        !lower.endsWith(".mp3") &&
        !lower.endsWith(".flac") &&
        !lower.endsWith(".ogg")
      ) {
        setLocalError(`Unsupported file type: ${file.name || "(no name)"}`);
        return;
      }
      setLocalError(null);
      onFile(file);
    },
    [onFile]
  );

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    if (disabled) return;
    handleFiles(e.dataTransfer.files);
  };

  const onChange = (e: ChangeEvent<HTMLInputElement>) => {
    handleFiles(e.target.files);
    // reset so picking the same file twice still fires onChange
    e.target.value = "";
  };

  const displayError = localError ?? error;

  return (
    <div>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => !disabled && inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === " ") && !disabled) {
            inputRef.current?.click();
          }
        }}
        className={[
          "group relative flex flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed px-6 py-16 text-center transition",
          disabled
            ? "cursor-not-allowed border-ink-700 bg-ink-900/40 opacity-60"
            : dragging
            ? "cursor-copy border-accent-500 bg-accent-500/5"
            : "cursor-pointer border-ink-700 bg-ink-900/60 hover:border-ink-600 hover:bg-ink-900/80",
        ].join(" ")}
      >
        <div className="grid h-12 w-12 place-items-center rounded-full bg-ink-800 ring-1 ring-ink-700 transition group-hover:bg-ink-700">
          <svg viewBox="0 0 24 24" className="h-6 w-6 text-zinc-300" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 16V4" />
            <path d="m6 10 6-6 6 6" />
            <path d="M5 20h14" />
          </svg>
        </div>
        <div>
          <div className="text-base font-medium text-zinc-200">
            Drop an audio file, or <span className="text-accent-400">browse</span>
          </div>
          <div className="mt-1 text-xs text-zinc-500">
            WAV · MP3 · FLAC · OGG — up to 50&nbsp;MB
          </div>
        </div>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          onChange={onChange}
          className="hidden"
        />
      </div>

      {displayError && (
        <div className="mt-4 rounded-lg border border-red-900/50 bg-red-950/40 px-4 py-3 text-sm text-red-300">
          {displayError}
        </div>
      )}
    </div>
  );
}