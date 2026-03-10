import React from "react";
import CloudWirePage from "./pages/CloudWirePage";
import "./styles/graph.css";

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    console.error("CloudWire render error:", error, info);
  }

  render() {
    if (this.state.hasError) {
      // Only surface the raw message in dev; in production show a generic message
      const isDev = import.meta.env.DEV;
      const message = isDev
        ? (this.state.error?.message || "An unexpected error occurred.")
        : "An unexpected rendering error occurred.";
      return (
        <div className="error-boundary-shell">
          <div className="error-boundary-inner">
            <div className="error-boundary-title">Something went wrong</div>
            <div className="error-boundary-message">{message}</div>
            <div style={{ display: "flex", gap: "10px", justifyContent: "center" }}>
              <button
                className="error-boundary-reset"
                onClick={() => this.setState({ hasError: false, error: null })}
              >
                Try again
              </button>
              <button
                className="error-boundary-reset"
                onClick={() => window.location.reload()}
              >
                Reload page
              </button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

export default function App() {
  return (
    <ErrorBoundary>
      <CloudWirePage />
    </ErrorBoundary>
  );
}
