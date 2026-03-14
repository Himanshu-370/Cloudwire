import React, { useCallback, useRef, useState } from "react";
import { useClickOutside } from "../../hooks/useClickOutside";

/**
 * Dropdown panel anchored below the TopBar showing uploaded Terraform files.
 * Allows adding/removing files and re-uploading.
 */
export function TerraformFilePanel({
  open,
  onClose,
  files,
  onAddFiles,
  onRemoveFile,
  onClearFiles,
  onParse,
  loading,
  error,
}) {
  const ref = useRef(null);
  const inputRef = useRef(null);
  useClickOutside(ref, onClose, open);

  const [dragOver, setDragOver] = useState(false);

  const handleDragOver = useCallback((e) => {
    e.preventDefault();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e) => {
    e.preventDefault();
    if (!e.currentTarget.contains(e.relatedTarget)) {
      setDragOver(false);
    }
  }, []);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer?.files?.length) {
      onAddFiles(e.dataTransfer.files);
    }
  }, [onAddFiles]);

  const handleInputChange = useCallback((e) => {
    if (e.target.files?.length) {
      onAddFiles(e.target.files);
    }
    e.target.value = "";
  }, [onAddFiles]);

  if (!open) return null;

  return (
    <div ref={ref} className="tf-panel">
      <div className="tf-panel-header">TERRAFORM FILES</div>

      {files.length === 0 && (
        <div className="tf-panel-empty">No files added yet.</div>
      )}

      <div className="tf-panel-list">
        {files.map((entry, i) => (
          <div
            key={`${entry.name}-${i}`}
            className={`tf-panel-file ${entry.status === "error" ? "tf-panel-file--error" : ""}`}
          >
            <span className="tf-panel-file-icon">
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                <rect x="1" y="1" width="10" height="10" rx="2" stroke="currentColor" strokeWidth="1" />
                <path d="M4 4h4M4 6h4M4 8h2" stroke="currentColor" strokeWidth="0.8" strokeLinecap="round" />
              </svg>
            </span>
            <span className="tf-panel-file-name" title={entry.name}>
              {entry.name}
            </span>
            <span className="tf-panel-file-meta">
              {entry.status === "uploading" ? (
                <span className="tf-panel-file-loading">parsing...</span>
              ) : entry.status === "error" ? (
                <span className="tf-panel-file-error">{entry.error || "failed"}</span>
              ) : entry.status === "done" ? (
                <span className="tf-panel-file-done">DONE</span>
              ) : (
                <span className="tf-panel-file-ready">READY</span>
              )}
            </span>
            <button
              className="tf-panel-file-remove"
              onClick={() => onRemoveFile(i)}
              title="Remove file"
              disabled={loading}
            >
              &times;
            </button>
          </div>
        ))}
      </div>

      <div
        className={`tf-panel-drop ${dragOver ? "tf-panel-drop--active" : ""}`}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        Drop more files, or{" "}
        <button className="tf-panel-browse" onClick={() => inputRef.current?.click()} disabled={loading}>
          BROWSE
        </button>
        <input
          ref={inputRef}
          type="file"
          accept=".tfstate,.json,.tf"
          multiple
          className="tf-dropzone-input"
          onChange={handleInputChange}
        />
      </div>

      {error && <div className="tf-panel-error">{error}</div>}

      <div className="tf-panel-actions">
        <button
          className="tf-panel-clear"
          onClick={onClearFiles}
          disabled={loading || files.length === 0}
        >
          CLEAR ALL
        </button>
        <button
          className="tf-panel-parse"
          onClick={onParse}
          disabled={loading || files.length === 0}
        >
          {loading ? "BUILDING..." : "BUILD GRAPH"}
        </button>
      </div>
    </div>
  );
}
