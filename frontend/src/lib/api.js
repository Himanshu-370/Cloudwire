/**
 * Shared API utilities — single source of truth for the API prefix,
 * JSON fetching, and error parsing.
 */

export const API_PREFIX = "/api";

/**
 * Parse a non-OK fetch Response into a human-readable error string.
 */
export async function parseErrorResponse(response, fallbackMessage) {
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

/**
 * Fetch JSON from the backend API with consistent error handling.
 *
 * @param {string} path       - API path (without the /api prefix)
 * @param {RequestInit} [options]   - fetch options (method, body, signal, etc.)
 * @param {string} [fallbackMessage] - error message when no structured error is returned
 * @returns {Promise<any>}
 */
export async function fetchApi(path, options = {}, fallbackMessage = "API request failed") {
  let response;
  try {
    response = await fetch(`${API_PREFIX}${path}`, options);
  } catch (error) {
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
