import { useEffect, useState } from "react";
import api from "../lib/api";
import { getSocket } from "../lib/socket";
import JobCard from "../components/JobCard";
import UploadForm from "../components/UploadForm";

export default function Dashboard() {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;

    api.get("/videos").then(({ data }) => {
      if (mounted) {
        setJobs(data.jobs);
        setLoading(false);
      }
    });

    const socket = getSocket();
    function onUpdate(updated) {
      setJobs((prev) => {
        const exists = prev.some((j) => j._id === updated._id);
        if (exists) {
          return prev.map((j) => (j._id === updated._id ? { ...j, ...updated } : j));
        }
        return [updated, ...prev];
      });
    }
    socket?.on("job:update", onUpdate);

    return () => {
      mounted = false;
      socket?.off("job:update", onUpdate);
    };
  }, []);

  return (
    <div className="container" style={{ paddingTop: 40, paddingBottom: 80 }}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 380px", gap: 32 }}>
        <div>
          <h2 style={{ fontSize: 22, marginBottom: 18 }}>Your videos</h2>
          {loading && <p className="dim">Loading...</p>}
          {!loading && jobs.length === 0 && (
            <div className="card">
              <p className="dim">
                Nothing here yet. Drop in a YouTube link or upload a file to get a transcript,
                summary, and a chat-ready assistant for it.
              </p>
            </div>
          )}
          {jobs.map((job) => (
            <JobCard key={job._id} job={job} />
          ))}
        </div>

        <div>
          <h2 style={{ fontSize: 22, marginBottom: 18 }}>New video</h2>
          <UploadForm />
        </div>
      </div>
    </div>
  );
}
