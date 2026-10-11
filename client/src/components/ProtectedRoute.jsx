import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export default function ProtectedRoute({ children }) {
  const { user, loading } = useAuth();

  if (loading) return null;
  if (!user) {
    const from = window.location.pathname;
    return <Navigate to={`/login?redirectTo=${encodeURIComponent(from)}`} replace />;
  }

  return children;
}
