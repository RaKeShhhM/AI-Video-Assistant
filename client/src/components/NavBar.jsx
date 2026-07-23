import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export default function NavBar() {
  const { user, logout } = useAuth();

  return (
    <header style={{ borderBottom: "1px solid var(--border)" }}>
      <div
        className="container"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          height: 64,
        }}
      >
        <Link to="/" style={{ textDecoration: "none" }}>
          <h1 style={{ fontSize: 20, color: "var(--text)" }}>
            reel<span style={{ color: "var(--accent)" }}>.</span>
          </h1>
        </Link>

        {user && (
          <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
            <span className="dim" style={{ fontSize: 14 }}>
              {user.name}
            </span>
            <button className="btn" onClick={logout}>
              Log out
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
