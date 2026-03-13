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
 *
 * Selection model: multi-key. User picks one or more keys, values are fetched
 * and displayed per-key (grouped). Each key's values are independent — no
 * cross-key contamination.
 */
export function useTagDiscovery(region, enabled = false) {
  const [tagKeys, setTagKeys] = useState([]);
  const [tagKeysLoading, setTagKeysLoading] = useState(false);
  const [tagKeysError, setTagKeysError] = useState("");

  // Multi-key selection
  const [selectedTagKeys, setSelectedTagKeys] = useState([]); // string[]
  // Per-key values: { [key]: string[] }
  const [tagValuesByKey, setTagValuesByKey] = useState({});
  // Per-key loading state: Set of keys currently loading
  const [valuesLoadingKeys, setValuesLoadingKeys] = useState(new Set());
  // Per-key selected values: { [key]: string[] }
  const [selectedValuesByKey, setSelectedValuesByKey] = useState({});

  const [activeTagFilters, setActiveTagFilters] = useState([]); // [{ key, values: [] }]

  const [discoveredServices, setDiscoveredServices] = useState([]);
  const [discoveredArns, setDiscoveredArns] = useState(null);
  const [discoveryLoading, setDiscoveryLoading] = useState(false);

  const fetchTokenRef = useRef(0);
  const valuesAbortRefs = useRef({}); // { [key]: AbortController }
  const discoveryAbortRef = useRef(null);

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

  // Fetch values for a single key
  const fetchValuesForKey = useCallback((key) => {
    // Abort any existing fetch for this key
    if (valuesAbortRefs.current[key]) {
      valuesAbortRefs.current[key].abort();
    }
    const controller = new AbortController();
    valuesAbortRefs.current[key] = controller;

    setValuesLoadingKeys((prev) => new Set([...prev, key]));

    fetchJson(
      `/tags/values?region=${encodeURIComponent(region)}&key=${encodeURIComponent(key)}`,
      controller.signal
    )
      .then((data) => {
        setTagValuesByKey((prev) => ({ ...prev, [key]: (data.values || []).sort() }));
      })
      .catch((err) => {
        if (err.name === "AbortError") return;
        setTagValuesByKey((prev) => ({ ...prev, [key]: [] }));
      })
      .finally(() => {
        setValuesLoadingKeys((prev) => {
          const next = new Set(prev);
          next.delete(key);
          return next;
        });
        if (valuesAbortRefs.current[key] === controller) {
          delete valuesAbortRefs.current[key];
        }
      });
  }, [region]);

  // Auto-fetch keys when enabled (TAGS mode) and region changes
  useEffect(() => {
    if (!enabled) return;
    // Abort all in-flight value fetches before resetting
    Object.values(valuesAbortRefs.current).forEach((c) => c.abort());
    valuesAbortRefs.current = {};
    refreshTagKeys();
    // Reset all selections on region change
    setSelectedTagKeys([]);
    setTagValuesByKey({});
    setValuesLoadingKeys(new Set());
    setSelectedValuesByKey({});
    setActiveTagFilters([]);
    setDiscoveredServices([]);
    setDiscoveredArns(null);
  }, [region, enabled, refreshTagKeys]);

  // Toggle a key in/out of selection, fetch values when adding
  const toggleTagKey = useCallback((key) => {
    setSelectedTagKeys((prev) => {
      if (prev.includes(key)) {
        // Deselect: clean up values for this key
        setTagValuesByKey((v) => { const next = { ...v }; delete next[key]; return next; });
        setSelectedValuesByKey((v) => { const next = { ...v }; delete next[key]; return next; });
        // Abort in-flight fetch
        if (valuesAbortRefs.current[key]) {
          valuesAbortRefs.current[key].abort();
          delete valuesAbortRefs.current[key];
        }
        return prev.filter((k) => k !== key);
      } else {
        // Select: fetch values
        fetchValuesForKey(key);
        return [...prev, key];
      }
    });
  }, [fetchValuesForKey]);

  // Toggle a value for a specific key
  const toggleTagValue = useCallback((key, value) => {
    setSelectedValuesByKey((prev) => {
      const keyValues = prev[key] || [];
      const next = keyValues.includes(value)
        ? keyValues.filter((v) => v !== value)
        : [...keyValues, value];
      return { ...prev, [key]: next };
    });
  }, []);

  // Commit all keys with selected values as filters
  const addTagFilter = useCallback(() => {
    const newFilters = [];
    for (const key of selectedTagKeys) {
      const values = selectedValuesByKey[key];
      if (values && values.length > 0) {
        newFilters.push({ key, values: [...values] });
      }
    }
    if (newFilters.length === 0) return;

    setActiveTagFilters((prev) => {
      let updated = [...prev];
      for (const f of newFilters) {
        updated = updated.filter((existing) => existing.key !== f.key);
        updated.push(f);
      }
      return updated;
    });

    // Clear selections after committing
    setSelectedTagKeys([]);
    setTagValuesByKey({});
    setSelectedValuesByKey({});
  }, [selectedTagKeys, selectedValuesByKey]);

  const removeTagFilter = useCallback((key) => {
    setActiveTagFilters((prev) => prev.filter((f) => f.key !== key));
  }, []);

  const clearAllTagFilters = useCallback(() => {
    setActiveTagFilters([]);
    setSelectedTagKeys([]);
    setTagValuesByKey({});
    setSelectedValuesByKey({});
    setDiscoveredServices([]);
    setDiscoveredArns(null);
  }, []);

  // Discover resources matching active tag filters (with AbortController)
  const discoverResources = useCallback(async () => {
    if (activeTagFilters.length === 0) return null;

    // Cancel any in-flight discovery
    if (discoveryAbortRef.current) {
      discoveryAbortRef.current.abort();
    }
    const controller = new AbortController();
    discoveryAbortRef.current = controller;

    setDiscoveryLoading(true);
    try {
      const awsFilters = activeTagFilters.map((f) => ({
        Key: f.key,
        Values: f.values,
      }));
      const data = await fetchJson(
        `/tags/resources?region=${encodeURIComponent(region)}&tag_filters=${encodeURIComponent(JSON.stringify(awsFilters))}`,
        controller.signal
      );
      setDiscoveredServices(data.services || []);
      setDiscoveredArns(data.arns || []);
      return data;
    } catch (err) {
      if (err.name === "AbortError") {
        setDiscoveredServices([]);
        setDiscoveredArns(null);
        return null;
      }
      setDiscoveredServices([]);
      setDiscoveredArns(null);
      throw err;
    } finally {
      setDiscoveryLoading(false);
      if (discoveryAbortRef.current === controller) {
        discoveryAbortRef.current = null;
      }
    }
  }, [region, activeTagFilters]);

  // Derived: whether any values have been selected (for enabling ADD FILTER)
  const hasSelectedValues = selectedTagKeys.some(
    (key) => (selectedValuesByKey[key] || []).length > 0
  );

  // Derived: any key still loading values
  const tagValuesLoading = valuesLoadingKeys.size > 0;

  return {
    tagKeys,
    tagKeysLoading,
    tagKeysError,
    refreshTagKeys,

    // Multi-key selection
    selectedTagKeys,
    toggleTagKey,

    // Per-key values
    tagValuesByKey,
    valuesLoadingKeys,
    tagValuesLoading,

    // Per-key selected values
    selectedValuesByKey,
    toggleTagValue,

    hasSelectedValues,
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
