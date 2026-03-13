import React, { useCallback, useRef, useState } from "react";
import { useClickOutside } from "../../hooks/useClickOutside";

function TagKeyDropdown({ keys, selectedKeys, onToggle, loading }) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const ref = useRef(null);
  const close = useCallback(() => setOpen(false), []);
  useClickOutside(ref, close, open);

  const filtered = search
    ? keys.filter((k) => k.toLowerCase().includes(search.toLowerCase()))
    : keys;

  return (
    <div ref={ref} className="tag-filter-dropdown-wrap">
      <button
        className={`tag-filter-dropdown-trigger ${open ? "open" : ""}`}
        onClick={() => setOpen((v) => !v)}
        title="Select tag keys"
      >
        <span className="tag-filter-dropdown-label">
          {loading
            ? "Loading..."
            : selectedKeys.length === 0
            ? "Tag keys..."
            : selectedKeys.length === 1
            ? selectedKeys[0]
            : `${selectedKeys.length} keys`}
        </span>
        <span className="tag-filter-dropdown-caret">{open ? "\u25B2" : "\u25BC"}</span>
      </button>

      {open && (
        <div className="tag-filter-dropdown-panel">
          <input
            className="tag-filter-search"
            type="text"
            placeholder="Search keys..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            autoFocus
          />
          <div className="tag-filter-key-list">
            {filtered.length === 0 && (
              <div className="tag-filter-empty">
                {keys.length === 0
                  ? "No tags found in this region"
                  : "No matching keys"}
              </div>
            )}
            {filtered.map((key) => {
              const checked = selectedKeys.includes(key);
              return (
                <label key={key} className={`tag-filter-value-item ${checked ? "checked" : ""}`}>
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => onToggle(key)}
                    className="tag-filter-checkbox"
                  />
                  <span className="tag-filter-value-check">
                    {checked && (
                      <svg viewBox="0 0 8 8" fill="none" width="8" height="8">
                        <path d="M1 4l2 2 4-4" stroke="#ff9900" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    )}
                  </span>
                  <span>{key}</span>
                </label>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function TagValueGroupedDropdown({
  selectedKeys,
  tagValuesByKey,
  valuesLoadingKeys,
  selectedValuesByKey,
  onToggleValue,
  onApply,
  hasSelectedValues,
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const ref = useRef(null);
  const close = useCallback(() => setOpen(false), []);
  useClickOutside(ref, close, open);

  // Auto-open when a new key is added (values will show as "Loading..." initially)
  const prevKeyCountRef = useRef(0);
  React.useEffect(() => {
    if (selectedKeys.length > prevKeyCountRef.current) {
      setOpen(true);
    }
    prevKeyCountRef.current = selectedKeys.length;
  }, [selectedKeys.length]);

  if (selectedKeys.length === 0) return null;

  const totalSelected = selectedKeys.reduce(
    (sum, key) => sum + (selectedValuesByKey[key] || []).length, 0
  );

  return (
    <div ref={ref} className="tag-filter-dropdown-wrap">
      <button
        className={`tag-filter-dropdown-trigger ${open ? "open" : ""}`}
        onClick={() => setOpen((v) => !v)}
        title="Select tag values"
      >
        <span className="tag-filter-dropdown-label">
          {totalSelected === 0
            ? "Select values..."
            : `${totalSelected} value${totalSelected !== 1 ? "s" : ""} selected`}
        </span>
        <span className="tag-filter-dropdown-caret">{open ? "\u25B2" : "\u25BC"}</span>
      </button>

      {open && (
        <div className="tag-filter-dropdown-panel tag-filter-dropdown-panel--grouped">
          <input
            className="tag-filter-search"
            type="text"
            placeholder="Search values..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            autoFocus
          />
          <div className="tag-filter-value-list">
            {selectedKeys.map((key) => {
              const values = tagValuesByKey[key] || [];
              const isLoading = valuesLoadingKeys.has(key);
              const keySelected = selectedValuesByKey[key] || [];
              const filtered = search
                ? values.filter((v) => v.toLowerCase().includes(search.toLowerCase()))
                : values;

              return (
                <div key={key} className="tag-filter-value-group">
                  <div className="tag-filter-value-group-header">{key}</div>
                  {isLoading && (
                    <div className="tag-filter-empty">Loading values...</div>
                  )}
                  {!isLoading && filtered.length === 0 && (
                    <div className="tag-filter-empty">
                      {values.length === 0 ? "No values" : "No matches"}
                    </div>
                  )}
                  {filtered.map((val) => {
                    const checked = keySelected.includes(val);
                    return (
                      <label key={val} className={`tag-filter-value-item ${checked ? "checked" : ""}`}>
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => onToggleValue(key, val)}
                          className="tag-filter-checkbox"
                        />
                        <span className="tag-filter-value-check">
                          {checked && (
                            <svg viewBox="0 0 8 8" fill="none" width="8" height="8">
                              <path d="M1 4l2 2 4-4" stroke="#ff9900" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
                            </svg>
                          )}
                        </span>
                        <span>{val}</span>
                      </label>
                    );
                  })}
                </div>
              );
            })}
          </div>
          {hasSelectedValues && (
            <button
              className="tag-filter-apply-btn"
              onClick={() => {
                onApply();
                setOpen(false);
                setSearch("");
              }}
            >
              ADD FILTER ({totalSelected})
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export function TagFilterBar({
  tagKeys,
  tagKeysLoading,
  tagKeysError,
  selectedTagKeys,
  onToggleTagKey,
  tagValuesByKey,
  valuesLoadingKeys,
  selectedValuesByKey,
  onToggleTagValue,
  hasSelectedValues,
  onApplyTagFilter,
  activeTagFilters,
  onRemoveTagFilter,
  onClearAllTagFilters,
  onRefreshTagKeys,
}) {
  return (
    <div className="tag-filter-bar">
      <TagKeyDropdown
        keys={tagKeys}
        selectedKeys={selectedTagKeys}
        onToggle={onToggleTagKey}
        loading={tagKeysLoading}
      />

      <TagValueGroupedDropdown
        selectedKeys={selectedTagKeys}
        tagValuesByKey={tagValuesByKey}
        valuesLoadingKeys={valuesLoadingKeys}
        selectedValuesByKey={selectedValuesByKey}
        onToggleValue={onToggleTagValue}
        onApply={onApplyTagFilter}
        hasSelectedValues={hasSelectedValues}
      />

      <button
        className="tag-filter-refresh-btn"
        onClick={onRefreshTagKeys}
        disabled={tagKeysLoading}
        title="Refresh available tags"
      >
        <svg width="10" height="10" viewBox="0 0 12 12" fill="none">
          <path d="M10 2L10 5H7M2 10L2 7H5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M2.5 4.5A4 4 0 0 1 9.5 3.5M9.5 7.5A4 4 0 0 1 2.5 8.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
        </svg>
      </button>

      {activeTagFilters.length > 0 && (
        <div className="tag-filter-chips">
          {activeTagFilters.map((f, i) => (
            <React.Fragment key={f.key}>
              {i > 0 && <span className="tag-filter-chip-conjunction">AND</span>}
              <span className="tag-filter-chip">
                <span className="tag-filter-chip-key">{f.key}</span>
                <span className="tag-filter-chip-eq">=</span>
                <span className="tag-filter-chip-val" title={f.values.join(", ")}>
                  {f.values.length <= 2 ? f.values.join(", ") : `${f.values[0]} +${f.values.length - 1}`}
                </span>
                <button className="tag-filter-chip-remove" onClick={() => onRemoveTagFilter(f.key)}>
                  ×
                </button>
              </span>
            </React.Fragment>
          ))}
          <button className="tag-filter-clear-btn" onClick={onClearAllTagFilters}>
            Clear all
          </button>
        </div>
      )}

      {tagKeysError && (
        <span className="tag-filter-error" title={tagKeysError}>
          ⚠
        </span>
      )}
    </div>
  );
}
