import { useEffect, useState } from "react";
import api from "../lib/api";

export default function ChatPanel({ jobId, initialChat = [], onAnswered }) {
  const [chat, setChat] = useState(initialChat);
  const [nextCursor, setNextCursor] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let mounted = true;
    setChat([]);
    setNextCursor(null);
    setLoading(true);
    api.get(`/videos/${jobId}/chat?limit=20`).then(({ data }) => {
      if (mounted) {
        setChat(data.messages);
        setNextCursor(data.nextCursor);
      }
    }).catch((err) => mounted && setError(err.response?.data?.message || "Could not load chat history."))
      .finally(() => mounted && setLoading(false));
    return () => { mounted = false; };
  }, [jobId]);

  useEffect(() => {
    if (initialChat && initialChat.length > 0) {
      setChat(prev => {
        const currentIds = new Set(prev.map(m => m.messageId || m._id));
        const newMessages = initialChat.filter(m => !currentIds.has(m.messageId || m._id));
        return [...prev, ...newMessages];
      });
    }
  }, [initialChat]);

  async function loadOlder() {
    if (!nextCursor || loadingOlder) return;
    setLoadingOlder(true);
    try {
      const { data } = await api.get(`/videos/${jobId}/chat?limit=20&cursor=${encodeURIComponent(nextCursor)}`);
      setChat((prev) => [...data.messages, ...prev]);
      setNextCursor(data.nextCursor);
    } catch (err) {
      setError(err.response?.data?.message || "Could not load older chat messages.");
    } finally {
      setLoadingOlder(false);
    }
  }

  async function handleAsk(e) {
    e.preventDefault();
    if (!question.trim()) return;
    setError("");
    setAsking(true);
    const q = question.trim();
    setQuestion("");

    try {
      const { data } = await api.post(`/videos/${jobId}/ask`, { question: q });
      setChat((prev) => [...prev, data.message]);
      onAnswered?.(data.message);
    } catch (err) {
      setError(err.response?.data?.message || "Could not get an answer. Try again.");
      setQuestion(q);
    } finally {
      setAsking(false);
    }
  }

  return (
    <div className="card">
      <h3 style={{ fontSize: 16, marginBottom: 14 }}>Chat with this video</h3>

      <div style={{ display: "flex", flexDirection: "column", gap: 14, marginBottom: 16 }}>
        {nextCursor && <button className="btn" disabled={loadingOlder} onClick={loadOlder}>
          {loadingOlder ? "Loading..." : "Load older messages"}
        </button>}
        {loading && <p className="dim">Loading chat...</p>}
        {!loading && chat.length === 0 && (
          <p className="dim" style={{ fontSize: 14 }}>
            Ask anything about the video — the answer is grounded only in its transcript.
          </p>
        )}
        {chat.map((c) => (
          <div key={c.messageId || c._id}>
            <p style={{ fontSize: 14, fontWeight: 600 }}>You: {c.question}</p>
            <p className="dim" style={{ fontSize: 14, marginTop: 4 }}>
              {c.answer}
            </p>
          </div>
        ))}
      </div>

      {error && <div className="error-banner">{error}</div>}

      <form onSubmit={handleAsk} style={{ display: "flex", gap: 8 }}>
        <input
          type="text"
          placeholder="Ask a question about this video..."
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          style={{
            flex: 1,
            background: "var(--surface-2)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            color: "var(--text)",
            padding: "10px 12px",
            fontSize: 14,
          }}
        />
        <button className="btn btn-primary" type="submit" disabled={asking}>
          {asking ? "..." : "Ask"}
        </button>
      </form>
    </div>
  );
}
