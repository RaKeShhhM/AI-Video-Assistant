import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import api from "../lib/api";
import { getSocket } from "../lib/socket";
import VUProgress from "../components/VUProgress";
import ChatPanel from "../components/ChatPanel";

const TABS = ["summary", "transcript", "action_items", "key_decisions", "open_questions"];
const TAB_LABELS = {
  summary: "Summary",
  transcript: "Transcript",
  action_items: "Action items",
  key_decisions: "Key decisions",
  open_questions: "Open questions",
};

export default function JobDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const retryKey = useRef(null);
  const [actionError, setActionError] = useState("");
  const [acting, setActing] = useState(false);
  async function remove() {
    if (acting) return;
    if (job.status !== "deleting" && !window.confirm("Delete this video, transcript, chat and search index? This cannot be undone.")) return;
    setActing(true);
    setActionError("");
    try {
      const { data } = await api.delete(`/videos/${id}`);
      if (data.job) setJob(data.job);
      else navigate("/");
    } catch (error) {
      setActionError(error.response?.data?.message || "Deletion could not be scheduled. Try again.");
    } finally { setActing(false); }
  }
  async function act(action) {
    if (acting) return;
    setActing(true);
    setActionError("");
    if (retryKey.current?.id !== id) retryKey.current = { id, key: crypto.randomUUID() };
    try {
      const { data } = await api.post(`/videos/${id}/${action}`, {}, {
        headers: { "Idempotency-Key": retryKey.current.key },
      });
      if (action === "retry") navigate(`/jobs/${data.job._id}`);
      else setJob(data.job);
    } catch (error) {
      setActionError(error.response?.data?.message || "Request interrupted. Try again.");
    } finally { setActing(false); }
  }
  const [job, setJob] = useState(null);
  const [tab, setTab] = useState("summary");

  useEffect(() => {
    let mounted = true;
    setJob(null);
    api.get(`/videos/${id}`).then(({ data }) => mounted && setJob(prev => !prev || (data.job.progressVersion || 0) >= (prev.progressVersion || 0) ? data.job : prev)).catch(() => mounted && navigate("/"));
    const refresh = setInterval(() => {
      api.get(`/videos/${id}`).then(({ data }) => {
        if (mounted && data.job.status === "deleting") setJob(data.job);
      }).catch(error => { if (mounted && error.response?.status === 404) navigate("/"); });
    }, 5000);

    const socket = getSocket();
    function onUpdate(updated) {
      if (updated._id === id) {
        setJob((prev) => !prev || (updated.progressVersion || 0) >= (prev.progressVersion || 0) ? { ...prev, ...updated } : prev);
      }
    }
    socket?.on("job:update", onUpdate);

    return () => {
      mounted = false;
      clearInterval(refresh);
      socket?.off("job:update", onUpdate);
    };
  }, [id]);

  if (!job) {
    return (
      <div className="container" style={{ paddingTop: 60 }}>
        <p className="dim">Loading...</p>
      </div>
    );
  }

  const isProcessing = ["queued", "processing"].includes(job.status);

  const tabContent = {
    summary: job.summary,
    transcript: job.transcript,
    action_items: job.actionItems,
    key_decisions: job.keyDecisions,
    open_questions: job.openQuestions,
  };

  return (
    <div className="container" style={{ paddingTop: 40, paddingBottom: 80 }}>
      <h2 style={{ fontSize: 24, marginBottom: 4 }}>{job.title || "Deleting video"}</h2>
      <p className="dim mono" style={{ fontSize: 12, marginBottom: 28 }}>
        {job.sourceType === "youtube" ? job.sourceValue : "Uploaded file"} · {job.language}
      </p>

      {isProcessing && (
        <div className="card" style={{ marginBottom: 28 }}>
          <VUProgress status={job.status} stage={job.stage} percent={job.percent} />
        </div>
      )}

      {actionError && <div className="error-banner">{actionError}</div>}
      {job.status === "deleting" && <p className="dim">Deleting stored media, transcript and search index. This may take a moment.</p>}
      {job.status === "deleting" && job.deletion?.state === "failed" && <div className="error-banner">Cleanup needs another attempt ({job.deletion.lastError}).</div>}
      {job.status !== "deleting" && <button className="btn" disabled={acting} onClick={remove}>Delete video</button>}
      {job.status === "deleting" && job.deletion?.state === "failed" && <button className="btn" disabled={acting} onClick={remove}>Retry deletion</button>}
      {isProcessing && job.stage !== "accepting" && <button className="btn" disabled={acting} onClick={() => act("cancel")}>Cancel processing</button>}
      {["failed", "cancelled"].includes(job.status) && <button className="btn" disabled={acting} onClick={() => act("retry")}>{acting ? "Starting..." : "Retry as a new job"}</button>}
      {job.status === "cancelled" && <p className="dim">Processing was cancelled.</p>}

      {job.status === "failed" && (
        <div className="error-banner">{job.error || "This job failed to process."}</div>
      )}

      {job.status === "completed" && (
        <>
          <div style={{ display: "flex", gap: 6, marginBottom: 18, flexWrap: "wrap" }}>
            {TABS.map((t) => (
              <button
                key={t}
                className="btn"
                style={{ borderColor: tab === t ? "var(--accent)" : "var(--border)" }}
                onClick={() => setTab(t)}
              >
                {TAB_LABELS[t]}
              </button>
            ))}
          </div>

          <div className="card" style={{ marginBottom: 24 }}>
            <pre
              style={{
                whiteSpace: "pre-wrap",
                fontFamily: tab === "transcript" ? "var(--font-mono)" : "var(--font-body)",
                fontSize: 14,
                lineHeight: 1.6,
                margin: 0,
              }}
            >
              {tabContent[tab] || "Nothing here."}
            </pre>
          </div>

          <ChatPanel jobId={job._id} initialChat={job.chat || []} />
        </>
      )}
    </div>
  );
}
