import { useEffect, useState, useRef } from "react";
import api from "../lib/api";
import ReactMarkdown from "react-markdown";

export default function ChatPanel({ jobId, initialChat = [], onAnswered }) {
  const [chat, setChat] = useState(initialChat);
  const [nextCursor, setNextCursor] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState("");
  const textareaRef = useRef(null);

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

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
    }
  }, [question]);

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
    <div className="card" role="region" aria-label="Video Chat">
      <h3 style={{ fontSize: 16, marginBottom: 14 }}>Chat with this video</h3>

      <div style={{ display: "flex", flexDirection: "column", gap: 14, marginBottom: 16 }} role="log" aria-live="polite">
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
            <div className="dim" style={{ fontSize: 14, marginTop: 4 }}>
              <ReactMarkdown>{c.answer}</ReactMarkdown>
            </div>
          </div>
        ))}
        {asking && (
          <div>
            <p style={{ fontSize: 14, fontWeight: 600 }}>You: {question || "..."}</p>
            <div className="dim" style={{ fontSize: 14, marginTop: 4, fontStyle: "italic" }}>
              Thinking...
            </div>
          </div>
        )}
      </div>

      {error && <div className="error-banner" role="alert">{error}</div>}

      <form onSubmit={handleAsk} style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
        <div className="field" style={{ flex: 1, marginBottom: 0 }}>
          <label htmlFor="chat-input" className="sr-only" style={{ position: "absolute", width: 1, height: 1, padding: 0, margin: -1, overflow: "hidden", clip: "rect(0,0,0,0)", border: 0 }}>
            Ask a question about this video
          </label>
          <textarea
            ref={textareaRef}
            id="chat-input"
            placeholder="Ask a question..."
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleAsk(e); } }}
            style={{
              width: "100%",
              background: "var(--surface-2)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              color: "var(--text)",
              padding: "10px 12px",
              fontSize: 14,
              resize: "none",
              minHeight: 40,
              maxHeight: 150,
              fontFamily: "inherit"
            }}
          />
        </div>
        <button className="btn btn-primary" type="submit" disabled={asking} style={{ height: 40 }}>
          {asking ? "..." : "Ask"}
        </button>
      </form>
    </div>
  );
}
