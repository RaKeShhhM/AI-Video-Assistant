import React from "react";

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error("ErrorBoundary caught an error:", error, errorInfo);
  }

  handleReload = () => {
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="container" style={{ paddingTop: 60, textAlign: "center" }}>
          <div className="card" style={{ maxWidth: 500, margin: "0 auto", padding: 40 }}>
            <h2 style={{ fontSize: 24, marginBottom: 12 }}>Something went wrong</h2>
            <p className="dim" style={{ marginBottom: 24 }}>
              An unexpected error occurred in the application. Please try reloading the page.
            </p>
            <button className="btn" onClick={this.handleReload}>
              Reload Page
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
