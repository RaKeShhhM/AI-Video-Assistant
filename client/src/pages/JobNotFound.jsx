import React from "react";
import { Link } from "react-router-dom";

export default function JobNotFound() {
  return (
    <div className="container" style={{ paddingTop: 60, textAlign: "center" }}>
      <div className="card" style={{ maxWidth: 500, margin: "0 auto", padding: 40 }}>
        <h2 style={{ fontSize: 24, marginBottom: 12 }}>Job Not Found</h2>
        <p className="dim" style={{ marginBottom: 24 }}>
          We couldn't find the video you're looking for. It may have been deleted or the ID is incorrect.
        </p>
        <Link to="/" className="btn" style={{ textDecoration: "none", display: "inline-block" }}>
          Back to My Videos
        </Link>
      </div>
    </div>
  );
}
