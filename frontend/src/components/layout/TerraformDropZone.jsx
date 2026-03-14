import React, { useCallback, useRef, useState } from "react";

/**
 * Full-canvas drop zone shown when the TERRAFORM tab is active and no graph is loaded.
 * Also renders a hidden file input for the BROWSE fallback.
 */
export function TerraformDropZone({ onFilesAccepted }) {
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef(null);

  const handleDragOver = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!e.currentTarget.contains(e.relatedTarget)) {
      setDragOver(false);
    }
  }, []);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
    if (e.dataTransfer?.files?.length) {
      onFilesAccepted(e.dataTransfer.files);
    }
  }, [onFilesAccepted]);

  const handleBrowse = useCallback(() => {
    inputRef.current?.click();
  }, []);

  const handleInputChange = useCallback((e) => {
    if (e.target.files?.length) {
      onFilesAccepted(e.target.files);
    }
    e.target.value = "";
  }, [onFilesAccepted]);

  return (
    <div
      className={`tf-dropzone ${dragOver ? "tf-dropzone--active" : ""}`}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      <div className="tf-dropzone-card">
        <div className="tf-dropzone-icon">
          <svg width="40" height="40" viewBox="0 0 40 40" fill="none">
            <rect x="4" y="8" width="32" height="24" rx="4" stroke="#FF9900" strokeWidth="1.5" strokeDasharray="4 3" />
            <path d="M20 14v12M14 20h12" stroke="#FF9900" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </div>
        <div className="tf-dropzone-title">Drop .tf or .tfstate files here</div>
        <div className="tf-dropzone-hint">
          {(
            <>
              or{" "}
              <button className="tf-dropzone-browse" onClick={handleBrowse}>
                BROWSE FILES
              </button>
            </>
          )}
        </div>
        <div className="tf-dropzone-meta">Supports .tf (HCL) and .tfstate files</div>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept=".tfstate,.json,.tf"
        multiple
        className="tf-dropzone-input"
        onChange={handleInputChange}
      />
    </div>
  );
}
