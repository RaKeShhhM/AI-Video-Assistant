import { useState } from "react";
import { useNavigate } from "react-router-dom";
import api from "../lib/api";

export default function UploadForm() {
  const [mode, setMode] = useState("youtube"); // "youtube" | "upload"
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [file, setFile] = useState(null);
  const [language, setLanguage] = useState("english");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const navigate = useNavigate();

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");

    if (mode === "youtube" && !youtubeUrl.trim()) {
      setError("Paste a YouTube URL first.");
      return;
    }
    if (mode === "upload" && !file) {
      setError("Choose a video or audio file first.");
      return;
    }

    setSubmitting(true);
    try {
      const form = new FormData();
      form.append("language", language);
      if (mode === "youtube") {
        form.append("youtubeUrl", youtubeUrl.trim());
      } else {
        form.append("file", file);
      }

      const { data } = await api.post("/videos", form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      navigate(`/jobs/${data.job._id}`);
    } catch (err) {
      setError(err.response?.data?.message || "Something went wrong. Try again.");
    } finally {
      setSubmitting(false);
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
            accept="video/*,audio/*"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
        </div>
      )}

      <div className="field">
        <label htmlFor="language">Language</label>
        <select id="language" value={language} onChange={(e) => setLanguage(e.target.value)}>
          <option value="english">English</option>
          <option value="hinglish">Hinglish (translated to English)</option>
        </select>
      </div>

      <button className="btn btn-primary" type="submit" disabled={submitting} style={{ width: "100%" }}>
        {submitting ? "Starting..." : "Process video"}
      </button>
    </form>
  );
}
