import { useCallback, useEffect, useRef, useState } from "react";

const API_PREFIX = "/api";

async function fetchJson(path, signal) {
  const response = await fetch(`${API_PREFIX}${path}`, signal ? { signal } : undefined);
  if (!response.ok) {
    let msg = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      msg = body?.error?.message || msg;
    } catch { /* ignore */ }
    throw new Error(msg);
  }
  return response.json();
}

/**
 * Hook for tag-based resource discovery.
 * Fetches tag keys/values from AWS and discovers resources matching tag filters.
 */
export function useTagDiscovery(region, enabled = false) {
  const [tagKeys, setTagKeys] = useState([]);
  const [tagKeysLoading, setTagKeysLoading] = useState(false);
  const [tagKeysError, setTagKeysError] = useState("");

  const [selectedTagKeys, setSelectedTagKeys] = useState([]);
  const [tagValues, setTagValues] = useState([]);
  const [tagValuesLoading, setTagValuesLoading] = useState(false);

  const [selectedTagValues, setSelectedTagValues] = useState([]);
  const [activeTagFilters, setActiveTagFilters] = useState([]); // [{ key, values: [] }]

  const [discoveredServices, setDiscoveredServices] = useState([]);
  const [discoveredArns, setDiscoveredArns] = useState(null);
  const [discoveryLoading, setDiscoveryLoading] = useState(false);

  const fetchTokenRef = useRef(0);
  const valuesTokenRef = useRef(0);

  // Fetch tag keys
  const refreshTagKeys = useCallback(async () => {
    fetchTokenRef.current += 1;
    const token = fetchTokenRef.current;
    setTagKeysLoading(true);
    setTagKeysError("");

    try {
      const data = await fetchJson(
        `/tags/keys?region=${encodeURIComponent(region)}`
      );
      if (token !== fetchTokenRef.current) return;
      setTagKeys(data.keys || []);
    } catch (err) {
      if (token !== fetchTokenRef.current) return;
      setTagKeysError(err instanceof Error ? err.message : String(err));
      setTagKeys([]);
    } finally {
      if (token === fetchTokenRef.current) {
        setTagKeysLoading(false);
      }
    }
  }, [region]);

  // Fetch values when selected keys change (merge values from all selected keys)
  useEffect(() => {
    if (selectedTagKeys.length === 0) {
      setTagValues([]);
      return;
    }

    const controller = new AbortController();
    valuesTokenRef.current += 1;
    const token = valuesTokenRef.current;
    setTagValuesLoading(true);

    Promise.all(
      selectedTagKeys.map((key) =>
        fetchJson(
          `/tags/values?region=${encodeURIComponent(region)}&key=${encodeURIComponent(key)}`,
          controller.signal
        ).then((data) => data.values || [])
         .catch(() => [])
      )
    )
      .then((results) => {
        if (token !== valuesTokenRef.current) return;
        const merged = [...new Set(results.flat())].sort();
        setTagValues(merged);
      })
      .finally(() => {
        if (token === valuesTokenRef.current) {
          setTagValuesLoading(false);
        }
      });

    return () => controller.abort();
  }, [region, selectedTagKeys]);

  // Auto-fetch keys when enabled (TAGS mode) and region changes
  useEffect(() => {
    if (!enabled) return;
    refreshTagKeys();
    // Reset all selections on region change
    setSelectedTagKeys([]);
    setSelectedTagValues([]);
    setActiveTagFilters([]);
    setDiscoveredServices([]);
    setDiscoveredArns(null);
  }, [region, enabled, refreshTagKeys]);

  const toggleTagKey = useCallback((key) => {
    setSelectedTagKeys((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
    );
    setSelectedTagValues([]);
  }, []);

  const toggleTagValue = useCallback((value) => {
    setSelectedTagValues((prev) =>
      prev.includes(value) ? prev.filter((v) => v !== value) : [...prev, value]
    );
  }, []);

  const addTagFilter = useCallback(() => {
    if (selectedTagKeys.length === 0 || selectedTagValues.length === 0) return;

    setActiveTagFilters((prev) => {
      // Remove existing filters for any of the selected keys
      const withoutKeys = prev.filter((f) => !selectedTagKeys.includes(f.key));
      // Add a filter entry for each selected key with the same values
      const newFilters = selectedTagKeys.map((key) => ({
        key,
        values: [...selectedTagValues],
      }));
      return [...withoutKeys, ...newFilters];
    });

    setSelectedTagKeys([]);
    setSelectedTagValues([]);
  }, [selectedTagKeys, selectedTagValues]);

  const removeTagFilter = useCallback((key) => {
    setActiveTagFilters((prev) => prev.filter((f) => f.key !== key));
  }, []);

  const clearAllTagFilters = useCallback(() => {
    setActiveTagFilters([]);
    setSelectedTagKeys([]);
    setSelectedTagValues([]);
    setDiscoveredServices([]);
    setDiscoveredArns(null);
  }, []);

  // Discover resources matching active tag filters
  const discoverResources = useCallback(async () => {
    if (activeTagFilters.length === 0) return null;

    setDiscoveryLoading(true);
    try {
      const awsFilters = activeTagFilters.map((f) => ({
        Key: f.key,
        Values: f.values,
      }));
      const data = await fetchJson(
        `/tags/resources?region=${encodeURIComponent(region)}&tag_filters=${encodeURIComponent(JSON.stringify(awsFilters))}`
      );
      setDiscoveredServices(data.services || []);
      setDiscoveredArns(data.arns || []);
      return data;
    } catch (err) {
      setDiscoveredServices([]);
      setDiscoveredArns(null);
      throw err;
    } finally {
      setDiscoveryLoading(false);
    }
  }, [region, activeTagFilters]);

  return {
    tagKeys,
    tagKeysLoading,
    tagKeysError,
    refreshTagKeys,

    selectedTagKeys,
    toggleTagKey,
    tagValues,
    tagValuesLoading,

    selectedTagValues,
    toggleTagValue,
    addTagFilter,

    activeTagFilters,
    removeTagFilter,
    clearAllTagFilters,

    discoveredServices,
    discoveredArns,
    discoveryLoading,
    discoverResources,
  };
}
