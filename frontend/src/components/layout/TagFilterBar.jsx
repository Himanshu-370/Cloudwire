import React, { useCallback, useEffect, useRef, useState } from "react";
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
            ? "Tag key..."
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

function TagValueDropdown({ values, selectedValues, onToggle, onApply, tagKeys, loading }) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const ref = useRef(null);
  const close = useCallback(() => setOpen(false), []);
  useClickOutside(ref, close, open);

  const prevTagKeysLenRef = useRef(tagKeys.length);
  useEffect(() => {
    if (tagKeys.length > 0 && tagKeys.length !== prevTagKeysLenRef.current && values.length > 0) {
      setOpen(true);
    }
    prevTagKeysLenRef.current = tagKeys.length;
  }, [tagKeys.length, values.length]);

  if (tagKeys.length === 0) return null;

  const filtered = search
    ? values.filter((v) => v.toLowerCase().includes(search.toLowerCase()))
    : values;

  return (
    <div ref={ref} className="tag-filter-dropdown-wrap">
      <button
        className={`tag-filter-dropdown-trigger ${open ? "open" : ""}`}
        onClick={() => setOpen((v) => !v)}
        title="Select tag values"
      >
        <span className="tag-filter-dropdown-label">
          {loading
            ? "Loading..."
            : selectedValues.length === 0
            ? "Select values..."
            : selectedValues.length === 1
            ? selectedValues[0]
            : `${selectedValues.length} values`}
        </span>
        <span className="tag-filter-dropdown-caret">{open ? "\u25B2" : "\u25BC"}</span>
      </button>

      {open && (
        <div className="tag-filter-dropdown-panel">
          <input
            className="tag-filter-search"
            type="text"
            placeholder="Search values..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            autoFocus
          />
          <div className="tag-filter-value-list">
            {filtered.length === 0 && (
              <div className="tag-filter-empty">
                {values.length === 0 ? "No values found" : "No matching values"}
              </div>
            )}
            {filtered.map((val) => {
              const checked = selectedValues.includes(val);
              return (
                <label key={val} className={`tag-filter-value-item ${checked ? "checked" : ""}`}>
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => onToggle(val)}
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
          {selectedValues.length > 0 && (
            <button
              className="tag-filter-apply-btn"
              onClick={() => {
                onApply();
                setOpen(false);
                setSearch("");
              }}
            >
              ADD FILTER ({selectedValues.length})
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
  tagValues,
  tagValuesLoading = false,
  selectedTagValues,
  onToggleTagValue,
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

      <TagValueDropdown
        values={tagValues}
        selectedValues={selectedTagValues}
        onToggle={onToggleTagValue}
        onApply={onApplyTagFilter}
        tagKeys={selectedTagKeys}
        loading={tagValuesLoading}
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
          {activeTagFilters.map((f) => (
            <span key={f.key} className="tag-filter-chip">
              <span className="tag-filter-chip-key">{f.key}</span>
              <span className="tag-filter-chip-eq">=</span>
              <span className="tag-filter-chip-val">{f.values.join(", ")}</span>
              <button className="tag-filter-chip-remove" onClick={() => onRemoveTagFilter(f.key)}>
                ×
              </button>
            </span>
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
