import { useCallback, useRef, useState } from "react";
import { API_PREFIX, parseErrorResponse } from "../lib/api";

/**
 * Hook for uploading and parsing Terraform .tfstate files.
 *
 * Returns file management state and an upload function that calls
 * POST /api/terraform/parse and returns the parsed graph payload.
 */
export function useTerraformUpload() {
  const [files, setFiles] = useState([]);         // { file, name, size, status, error }[]
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [lastResult, setLastResult] = useState(null);

  // Ref to always read the latest files without re-creating uploadAndParse.
  const filesRef = useRef(files);
  filesRef.current = files;

  const addFiles = useCallback((fileList) => {
    const newEntries = Array.from(fileList)
      .filter((f) => f.name.endsWith(".tfstate") || f.name.endsWith(".json") || f.name.endsWith(".tf"))
      .map((f) => ({
        file: f,
        name: f.name,
        size: f.size,
        status: "pending",    // pending | uploading | done | error
        error: null,
      }));
    if (newEntries.length === 0) {
      setError("Only .tf, .tfstate, and .json files are accepted.");
      return;
    }
    setFiles((prev) => {
      const existing = new Set(prev.map((e) => `${e.name}:${e.size}`));
      const deduped = newEntries.filter((e) => !existing.has(`${e.name}:${e.size}`));
      return deduped.length > 0 ? [...prev, ...deduped] : prev;
    });
    setError("");
  }, []);

  const removeFile = useCallback((index) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const clearFiles = useCallback(() => {
    setFiles([]);
    setLastResult(null);
    setError("");
  }, []);

  const uploadAndParse = useCallback(async () => {
    const entries = filesRef.current;
    if (entries.length === 0) {
      setError("No files to upload.");
      return null;
    }

    setLoading(true);
    setError("");
    setFiles((prev) => prev.map((f) => ({ ...f, status: "uploading" })));

    const formData = new FormData();
    for (const entry of entries) {
      formData.append("files", entry.file);
    }

    try {
      const response = await fetch(`${API_PREFIX}/terraform/parse`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        throw new Error(await parseErrorResponse(response, "Upload failed"));
      }

      const result = await response.json();

      setFiles((prev) =>
        prev.map((f) => ({
          ...f,
          status: "done",
        }))
      );
      setLastResult(result);
      return result;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      setFiles((prev) => prev.map((f) => ({ ...f, status: "error", error: msg })));
      return null;
    } finally {
      setLoading(false);
    }
  }, []); // stable — reads files via ref

  return {
    files,
    loading,
    error,
    lastResult,
    addFiles,
    removeFile,
    clearFiles,
    uploadAndParse,
    setError,
  };
}
