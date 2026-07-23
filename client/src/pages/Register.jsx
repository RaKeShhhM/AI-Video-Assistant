import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export default function Register() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const { register } = useAuth();
  const navigate = useNavigate();

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await register(name, email, password);
      navigate("/");
    } catch (err) {
      setError(err.response?.data?.message || "Registration failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="container" style={{ maxWidth: 420, paddingTop: 96 }}>
      <h2 style={{ fontSize: 26, marginBottom: 24 }}>Create your account</h2>
      <form className="card" onSubmit={handleSubmit}>
        {error && <div className="error-banner">{error}</div>}
        <div className="field">
          <label htmlFor="name">Name</label>
          <input id="name" type="text" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="email">Email</label>
          <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        <button className="btn btn-primary" type="submit" disabled={submitting} style={{ width: "100%" }}>
          {submitting ? "Creating..." : "Create account"}
        </button>
      </form>
      <p className="dim" style={{ marginTop: 16, fontSize: 14 }}>
        Already have an account? <Link to="/login" style={{ color: "var(--accent)" }}>Log in</Link>
      </p>
    </div>
  );
}
