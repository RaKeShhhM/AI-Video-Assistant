import { Link } from "react-router-dom";
import VUProgress from "./VUProgress";

export default function JobCard({ job }) {
  return (
    <Link to={`/jobs/${job._id}`} style={{ textDecoration: "none", color: "inherit" }}>
      <div className="card" style={{ marginBottom: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
          <div>
            <h3 style={{ fontSize: 16 }}>{job.title || "Untitled video"}</h3>
            <p className="dim mono" style={{ fontSize: 12, marginTop: 4 }}>
              {job.sourceType === "youtube" ? "YouTube" : "Upload"} · {job.language}
            </p>
          </div>
          <span
            className="mono"
            style={{
              fontSize: 11,
              alignSelf: "flex-start",
              padding: "3px 8px",
              borderRadius: 999,
              border: "1px solid var(--border)",
              color:
                job.status === "completed"
                  ? "var(--accent-2)"
                  : job.status === "failed"
                  ? "var(--danger)"
                  : "var(--accent)",
            }}
          >
            {job.status}
          </span>
        </div>

        {job.status !== "completed" && job.status !== "failed" && (
          <div style={{ marginTop: 14 }}>
            <VUProgress status={job.status} stage={job.stage} percent={job.percent} segments={28} />
          </div>
        )}
      </div>
    </Link>
  );
}
