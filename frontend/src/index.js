import React from "react";
import ReactDOM from "react-dom/client";
import "@/index.css";
import App from "@/App";

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    console.error('ResearchForge render error:', error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          minHeight: '100vh',
          background: '#09090b',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#e4e4e7',
          fontFamily: 'monospace',
          padding: '2rem',
          textAlign: 'center',
        }}>
          <div style={{
            fontSize: '2rem',
            marginBottom: '1rem',
          }}>⚡</div>
          <h2 style={{ marginBottom: '0.5rem', fontSize: '1.25rem' }}>
            Something went wrong
          </h2>
          <p style={{
            color: '#71717a',
            marginBottom: '1.5rem',
            maxWidth: '400px',
            fontSize: '0.875rem',
          }}>
            ResearchForge encountered an unexpected error.
          </p>
          <button
            onClick={() => window.location.reload()}
            style={{
              background: '#06b6d4',
              color: '#000',
              border: 'none',
              borderRadius: '4px',
              padding: '0.5rem 1.25rem',
              cursor: 'pointer',
              fontSize: '0.875rem',
              fontFamily: 'monospace',
            }}
          >
            Reload
          </button>
          {this.state.error && (
            <p style={{
              marginTop: '1rem',
              color: '#52525b',
              fontSize: '0.75rem',
              maxWidth: '500px',
            }}>
              {this.state.error.toString()}
            </p>
          )}
        </div>
      );
    }
    return this.props.children;
  }
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
);
