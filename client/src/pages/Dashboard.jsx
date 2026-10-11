import { useEffect, useRef, useState } from "react";
import api from "../lib/api";
import { getSocket } from "../lib/socket";
import JobCard from "../components/JobCard";
import UploadForm from "../components/UploadForm";

export default function Dashboard() {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [nextCursor, setNextCursor] = useState(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const requestNumber = useRef(0);

  useEffect(() => {
    let mounted = true;

    const load = () => {
      const request = ++requestNumber.current;
      return api.get("/videos?limit=20").then(({ data }) => {
      if (mounted) {
        setJobs((previous) => {
          const incoming = new Map(data.jobs.map(job => [job._id, job]));
          return data.jobs.map(job => {
            const current = previous.find(item => item._id === job._id);
            return current && (current.progressVersion || 0) >= (job.progressVersion || 0)
              ? { ...job, ...current } : job;
          }).concat(previous.filter(job => !incoming.has(job._id) && request !== requestNumber.current));
        });
        if (request === requestNumber.current) setNextCursor(data.nextCursor);
        setLoading(false);
      }
      });
    };
    load();
    const refresh = setInterval(load, 10000);

    const socket = getSocket();
    function onUpdate(updated) {
      const event = updated?.version === 1 ? updated.job : updated;
      if (!event?._id) return;
      setJobs((prev) => {
        const exists = prev.some((j) => j._id === event._id);
        if (exists) {
          return prev.map((j) => (j._id === event._id && (event.progressVersion || 0) >= (j.progressVersion || 0)
            ? { ...j, ...event } : j));
        }
        return event.status === "deleted" ? prev : [event, ...prev];
      });
    }
    socket?.on("job:update", onUpdate);

    return () => {
      mounted = false;
      clearInterval(refresh);
      socket?.off("job:update", onUpdate);
    };
  }, []);

  async function loadMore() {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const { data } = await api.get(`/videos?limit=20&cursor=${encodeURIComponent(nextCursor)}`);
      setJobs((prev) => [...prev, ...data.jobs.filter(job => !prev.some(existing => existing._id === job._id))]);
      setNextCursor(data.nextCursor);
    } finally {
      setLoadingMore(false);
    }
  }

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
          {nextCursor && <button className="btn" disabled={loadingMore} onClick={loadMore}>
            {loadingMore ? "Loading..." : "Load older videos"}
          </button>}
        </div>

        <div>
          <h2 style={{ fontSize: 22, marginBottom: 18 }}>New video</h2>
          <UploadForm />
        </div>
      </div>
    </div>
  );
}
