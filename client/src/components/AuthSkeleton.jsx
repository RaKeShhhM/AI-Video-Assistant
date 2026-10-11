import React from "react";

export default function AuthSkeleton() {
  return (
    <div className="container" style={{ paddingTop: 60, opacity: 0.6 }}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 380px", gap: 32 }}>
        <div>
          <div style={{ height: 32, width: 200, backgroundColor: "var(--border)", marginBottom: 24, borderRadius: 4 }} />
          {[1, 2, 3].map((i) => (
            <div key={i} className="card" style={{ height: 100, marginBottom: 16, backgroundColor: "var(--border)", opacity: 0.3 }} />
          ))}
        </div>
        <div>
          <div style={{ height: 32, width: 150, backgroundColor: "var(--border)", marginBottom: 24, borderRadius: 4 }} />
          <div className="card" style={{ height: 300, backgroundColor: "var(--border)", opacity: 0.3 }} />
        </div>
      </div>
    </div>
  );
}
