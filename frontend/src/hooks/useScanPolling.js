import { useCallback, useEffect, useRef, useState } from "react";
import { DEFAULT_REGION } from "../lib/awsRegions";
import { fetchApi } from "../lib/api";
import { normalizeGraph } from "../lib/graphTransforms";

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
  const [bootstrapLoading, setBootstrapLoading] = useState(false);
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
    const payload = await fetchApi("/graph", {}, "Unable to load the latest graph");
    setGraphData(normalizeGraph(payload));
    return payload;
  }, []);

  const fetchJobStatus = useCallback(async (jobId) => {
    return fetchApi(`/scan/${encodeURIComponent(jobId)}`, {}, "Unable to fetch scan status");
  }, []);

  // fetchJobGraph accepts a token and only updates state if still valid
  const fetchJobGraph = useCallback(async (jobId, token) => {
    const payload = await fetchApi(
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
    return fetchApi(
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

        if (isTerminalJobStatus(statusPayload.status)) {
          try {
            await fetchJobGraph(jobId, token);
            if (token !== pollState.current.token) return;
            setError("");
          } catch (graphError) {
            if (token !== pollState.current.token) return;
            setError(toMessage(graphError));
          }
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
    async ({ region = DEFAULT_REGION, services = [], mode = "quick", forceRefresh = false, includeCosts = false, tagArns = null }) => {
      clearPolling();
      const startToken = pollState.current.token;
      // FIX #1: immediately clear stale graph and status so the UI doesn't show
      // the previous scan's data while the new scan is running
      setGraphData(EMPTY_GRAPH);
      setJobStatus(null);
      setError("");
      setScanLoading(true);

      try {
        const scanBody = { region, services, mode, force_refresh: forceRefresh };
        if (includeCosts) scanBody.include_costs = true;
        if (tagArns) scanBody.tag_arns = tagArns;
        const payload = await fetchApi("/scan", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(scanBody),
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
      const payload = await fetchApi(`/scan/${encodeURIComponent(currentJobId)}/stop`, {
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

  // Inject a pre-built graph from an external source (e.g. Terraform parse).
  // Atomically sets graphData, jobStatus, and currentJobId in one batch.
  const injectGraph = useCallback((jobId, graphPayload, extraStatus = {}) => {
    clearPolling();
    setGraphData(normalizeGraph(graphPayload));
    setCurrentJobId(jobId);
    setScanLoading(false);
    setError("");
    setJobStatus({
      job_id: jobId,
      status: "completed",
      mode: "quick",
      region: "terraform",
      services: [],
      progress_percent: 100,
      node_count: graphPayload?.metadata?.node_count ?? graphPayload?.nodes?.length ?? 0,
      edge_count: graphPayload?.metadata?.edge_count ?? graphPayload?.edges?.length ?? 0,
      warnings: graphPayload?.metadata?.warnings || [],
      ...extraStatus,
    });
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
    injectGraph,
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
