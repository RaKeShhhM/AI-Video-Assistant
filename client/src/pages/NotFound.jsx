import React from "react";
import { Link } from "react-router-dom";

export default function NotFound() {
  return (
    <div className="container" style={{ paddingTop: 60, textAlign: "center" }}>
      <div className="card" style={{ maxWidth: 500, margin: "0 auto", padding: 40 }}>
        <h2 style={{ fontSize: 24, marginBottom: 12 }}>404 - Page Not Found</h2>
        <p className="dim" style={{ marginBottom: 24 }}>
          The page you are looking for doesn't exist or has been moved.
        </p>
        <Link to="/" className="btn" style={{ textDecoration: "none", display: "inline-block" }}>
          Return to Dashboard
        </Link>
      </div>
    </div>
  );
}
