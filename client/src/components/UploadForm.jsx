import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import api from "../lib/api";
import axios from "axios";

export default function UploadForm() {
  const submission = useRef(null);
  const abortController = useRef(null);
  const [mode, setMode] = useState("youtube"); // "youtube" | "upload"
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [file, setFile] = useState(null);
  const [language, setLanguage] = useState("english");
  const [submitting, setSubmitting] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [error, setError] = useState("");
  const navigate = useNavigate();
  const [policy, setPolicy] = useState(null);

  useEffect(() => {
    let active = true;
    api.get("/videos/limits").then(({ data }) => { if (active) setPolicy(data); }).catch(() => {});
    return () => { active = false; };
  }, []);

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setUploadProgress(0);

    if (mode === "youtube" && !youtubeUrl.trim()) {
      setError("Paste a YouTube URL first.");
      return;
    }
    if (mode === "upload" && !file) {
      setError("Choose a video or audio file first.");
      return;
    }
    if (mode === "upload" && policy && file.size > policy.maxUploadBytes) {
      setError(`Choose a file smaller than ${policy.maxUploadBytes / 1024 / 1024} MB.`);
      return;
    }

    if (submitting) return;
    const previous = submission.current;
    if (!previous || previous.mode !== mode || previous.url !== youtubeUrl.trim() || previous.file !== file || previous.language !== language) {
      submission.current = { mode, url: youtubeUrl.trim(), file, language, key: crypto.randomUUID() };
    }
    setSubmitting(true);
    abortController.current = new AbortController();

    try {
      const form = new FormData();
      form.append("language", language);
      if (mode === "youtube") {
        form.append("youtubeUrl", youtubeUrl.trim());
      } else {
        form.append("file", file);
      }

      const { data } = await api.post("/videos", form, {
        headers: { "Content-Type": "multipart/form-data", "Idempotency-Key": submission.current.key },
        signal: abortController.current.signal,
        onUploadProgress: (progressEvent) => {
          const percentCompleted = Math.round((progressEvent.loaded * 100) / progressEvent.total);
          setUploadProgress(percentCompleted);
        },
      });
      navigate(`/jobs/${data.job._id}`);
    } catch (err) {
      if (err.name === "CanceledError" || err.name === "AbortError") return;
      setError(err.response?.data?.message || "Something went wrong. Try again.");
    } finally {
      setSubmitting(false);
      setUploadProgress(0);
    }
  }

  function cancelUpload() {
    if (abortController.current) {
      abortController.current.abort();
      setSubmitting(false);
      setUploadProgress(0);
      setError("Upload cancelled.");
    }
  }

  return (
    <form className="card" onSubmit={handleSubmit}>
      <div style={{ display: "flex", gap: 8, marginBottom: 18 }}>
        <button
          type="button"
          className="btn"
          style={{ borderColor: mode === "youtube" ? "var(--accent)" : "var(--border)" }}
          onClick={() => setMode("youtube")}
        >
          YouTube link
        </button>
        <button
          type="button"
          className="btn"
          style={{ borderColor: mode === "upload" ? "var(--accent)" : "var(--border)" }}
          onClick={() => setMode("upload")}
        >
          Upload file
        </button>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {mode === "youtube" ? (
        <div className="field">
          <label htmlFor="youtubeUrl">YouTube URL</label>
          <input
            id="youtubeUrl"
            type="url"
            placeholder="https://www.youtube.com/watch?v=..."
            value={youtubeUrl}
            onChange={(e) => setYoutubeUrl(e.target.value)}
          />
        </div>
      ) : (
        <div className="field">
          <label htmlFor="file">Video or audio file</label>
          <input
            id="file"
            type="file"
            accept={policy?.extensions.join(",") || ".mp4,.m4a,.mov,.mkv,.webm,.mp3,.wav,.ogg,.flac,.aac"}
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
        </div>
      )}

      {policy && <p className="dim" style={{ fontSize: 12, marginBottom: 16 }}>
        Up to {policy.maxUploadBytes / 1024 / 1024} MB and {policy.maxMediaSeconds / 60} minutes per video.
        Daily limits: {policy.dailyMediaMinutes} processing minutes and {policy.dailyQuestions} questions.
        Stored videos are deleted after {policy.retentionDays} days.
      </p>}

      <div className="field">
        <label htmlFor="language">Language</label>
        <select id="language" value={language} onChange={(e) => setLanguage(e.target.value)}>
          <option value="english">English</option>
          <option value="hinglish">Hinglish (translated to English)</option>
        </select>
      </div>

      {submitting && mode === "upload" && uploadProgress > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 4 }}>
            <span className="dim">Uploading media...</span>
            <span className="mono">{uploadProgress}%</span>
          </div>
          <div style={{ height: 6, background: "var(--border)", borderRadius: 3, overflow: "hidden" }}>
            <div style={{ height: "100%", background: "var(--accent)", width: `${uploadProgress}%`, transition: "width 0.2s ease" }} />
          </div>
        </div>
      )}

      <div style={{ display: "flex", gap: 8 }}>
        <button className="btn btn-primary" type="submit" disabled={submitting} style={{ flex: 1 }}>
          {submitting ? "Starting..." : "Process video"}
        </button>
        {submitting && (
          <button type="button" className="btn" onClick={cancelUpload}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
