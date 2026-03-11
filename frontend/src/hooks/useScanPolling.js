import { useCallback, useEffect, useRef, useState } from "react";
import { DEFAULT_REGION } from "../lib/awsRegions";
import { normalizeGraph } from "../lib/graphTransforms";

// All requests are same-origin in production (served by the cloudwire CLI).
// In dev, vite.config.js proxies /api/* to http://localhost:8000.
const API_PREFIX = "/api";

// Auto-abandon a hung scan after 10 minutes
const MAX_SCAN_MS = 10 * 60 * 1000;

function nextPollDelayMs(startedAt) {
  const elapsed = Date.now() - startedAt;
  if (elapsed <= 30_000) return 1000;
  if (elapsed <= 60_000) return 2000;
  return 3000;
}

function isTerminalJobStatus(status) {
  return ["completed", "failed", "cancelled"].includes(status);
}

async function parseErrorResponse(response, fallbackMessage) {
  let rawText = "";
  let payload = null;
  try {
    rawText = await response.text();
    payload = rawText ? JSON.parse(rawText) : null;
  } catch {
    payload = null;
  }

  const apiError = payload?.error;
  if (apiError?.message) {
    if (apiError.code === "validation_error" && Array.isArray(apiError.details)) {
      const firstIssue = apiError.details[0];
      if (firstIssue?.msg) {
        return `${apiError.message} ${firstIssue.msg}`;
      }
    }
    return apiError.message;
  }

  if (typeof payload?.detail === "string") {
    return payload.detail;
  }

  return rawText || `${fallbackMessage} (${response.status})`;
}

async function requestJson(path, options = {}, fallbackMessage = "API request failed") {
  let response;
  try {
    response = await fetch(`${API_PREFIX}${path}`, options);
  } catch (error) {
    // Only treat network-level TypeErrors as "backend unreachable"
    if (error instanceof TypeError && /failed to fetch|network/i.test(error.message)) {
      throw new Error("Unable to reach the backend. If running in development, start uvicorn on port 8000.");
    }
    throw error;
  }

  if (!response.ok) {
    throw new Error(await parseErrorResponse(response, fallbackMessage));
  }

  return response.json();
}

const EMPTY_GRAPH = { nodes: [], edges: [], metadata: {} };

function toMessage(err) {
  if (err instanceof Error) return err.message;
  if (typeof err === "string") return err;
  return String(err);
}

export function useScanPolling() {
  const [graphData, setGraphData] = useState(EMPTY_GRAPH);
  const [jobStatus, setJobStatus] = useState(null);
  const [currentJobId, setCurrentJobId] = useState(null);
  const [scanLoading, setScanLoading] = useState(false);
  const [bootstrapLoading, setBootstrapLoading] = useState(true);
  const [error, setError] = useState("");
  const pollState = useRef({ token: 0, timer: null, startedAt: 0 });

  const clearPolling = useCallback(() => {
    pollState.current.token += 1;
    if (pollState.current.timer) {
      window.clearTimeout(pollState.current.timer);
      pollState.current.timer = null;
    }
  }, []);

  const fetchGraph = useCallback(async () => {
    const payload = await requestJson("/graph", {}, "Unable to load the latest graph");
    setGraphData(normalizeGraph(payload));
    return payload;
  }, []);

  const fetchJobStatus = useCallback(async (jobId) => {
    return requestJson(`/scan/${encodeURIComponent(jobId)}`, {}, "Unable to fetch scan status");
  }, []);

  // fetchJobGraph accepts a token and only updates state if still valid
  const fetchJobGraph = useCallback(async (jobId, token) => {
    const payload = await requestJson(
      `/scan/${encodeURIComponent(jobId)}/graph`,
      {},
      "Unable to load the scan graph"
    );
    // Only commit graph update if this request is still the active one
    if (token === undefined || token === pollState.current.token) {
      setGraphData(normalizeGraph(payload));
    }
    return payload;
  }, []);

  const fetchResource = useCallback(async (resourceId, jobId) => {
    const params = new URLSearchParams();
    if (jobId) params.set("job_id", jobId);
    const suffix = params.toString() ? `?${params.toString()}` : "";
    return requestJson(
      `/resource/${encodeURIComponent(resourceId)}${suffix}`,
      {},
      "Unable to load resource details"
    );
  }, []);

  const pollJob = useCallback(
    async (jobId, token) => {
      if (token !== pollState.current.token) return;

      // FIX #21: abandon hung scans after MAX_SCAN_MS
      if (Date.now() - pollState.current.startedAt > MAX_SCAN_MS) {
        setScanLoading(false);
        setError("Scan timed out after 10 minutes. The backend may be unresponsive.");
        return;
      }

      try {
        const statusPayload = await fetchJobStatus(jobId);
        if (token !== pollState.current.token) return;

        // FIX #17: update status and graph atomically with the same token guard
        setJobStatus(statusPayload);

        try {
          await fetchJobGraph(jobId, token);
          if (token !== pollState.current.token) return;
          setError("");
        } catch (graphError) {
          if (token !== pollState.current.token) return;
          setError(toMessage(graphError));
        }

        if (isTerminalJobStatus(statusPayload.status)) {
          setScanLoading(false);
          if (statusPayload.status === "failed" && statusPayload.error) {
            setError(statusPayload.error);
          } else if (statusPayload.status !== "failed") {
            setError("");
          }
          return;
        }

        const delay = nextPollDelayMs(pollState.current.startedAt);
        pollState.current.timer = window.setTimeout(() => {
          pollJob(jobId, token);
        }, delay);
      } catch (scanError) {
        if (token !== pollState.current.token) return;
        setScanLoading(false);
        setError(toMessage(scanError));
      }
    },
    [fetchJobGraph, fetchJobStatus]
  );

  const startPolling = useCallback(
    async (jobId) => {
      clearPolling();
      const token = pollState.current.token;
      pollState.current.startedAt = Date.now();
      await pollJob(jobId, token);
    },
    [clearPolling, pollJob]
  );

  const runScan = useCallback(
    async ({ region = DEFAULT_REGION, services = [], mode = "quick", forceRefresh = false }) => {
      clearPolling();
      const startToken = pollState.current.token;
      // FIX #1: immediately clear stale graph and status so the UI doesn't show
      // the previous scan's data while the new scan is running
      setGraphData(EMPTY_GRAPH);
      setJobStatus(null);
      setError("");
      setScanLoading(true);

      try {
        const payload = await requestJson("/scan", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ region, services, mode, force_refresh: forceRefresh }),
        }, "Unable to start the AWS scan");

        const jobId = payload.job_id;
        setCurrentJobId(jobId);

        const statusPayload = await fetchJobStatus(jobId);
        setJobStatus(statusPayload);

        // Guard: a second runScan call may have cleared our token already
        const tokenAfterPost = pollState.current.token;
        if (startToken !== tokenAfterPost) return payload;

        await fetchJobGraph(jobId, pollState.current.token);

        if (isTerminalJobStatus(statusPayload.status)) {
          setScanLoading(false);
          if (statusPayload.status === "failed" && statusPayload.error) {
            setError(statusPayload.error);
          }
          return payload;
        }

        await startPolling(jobId);
        return payload;
      } catch (scanError) {
        setScanLoading(false);
        setError(toMessage(scanError));
        throw scanError;
      }
    },
    [clearPolling, fetchJobGraph, fetchJobStatus, startPolling]
  );

  const stopScan = useCallback(async () => {
    if (!currentJobId) return null;
    // FIX #19: always stop polling + clear loading regardless of backend response
    clearPolling();
    setScanLoading(false);

    try {
      const payload = await requestJson(`/scan/${encodeURIComponent(currentJobId)}/stop`, {
        method: "POST",
      }, "Unable to stop the scan");
      setJobStatus(payload);
      try {
        await fetchJobGraph(currentJobId, pollState.current.token);
      } catch (graphErr) {
        // best-effort: graph fetch after stop is non-critical
        console.debug("Non-critical: failed to fetch graph after stop:", graphErr);
      }
      return payload;
    } catch (stopError) {
      setError(toMessage(stopError));
      return null;
    }
  }, [clearPolling, currentJobId, fetchJobGraph]);

  useEffect(() => {
    // On page load, start clean — don't restore previous scan data
    setGraphData(EMPTY_GRAPH);
    setJobStatus(null);
    setBootstrapLoading(false);
    return () => clearPolling();
  }, [clearPolling]);

  return {
    graphData,
    jobStatus,
    currentJobId,
    scanLoading,
    bootstrapLoading,
    error,
    setError,
    runScan,
    stopScan,
    fetchGraph,
    fetchResource,
    clearPolling,
  };
}

export function formatJobStatusLabel(jobStatus) {
  if (!jobStatus) return "idle";
  if (jobStatus.cancellation_requested && !isTerminalJobStatus(jobStatus.status)) {
    return "stop requested";
  }
  return jobStatus.status;
}

export function isScanTerminal(jobStatus) {
  return isTerminalJobStatus(jobStatus?.status);
}
