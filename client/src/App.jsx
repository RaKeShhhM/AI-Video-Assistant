import { Navigate, Route, Routes, useParams } from "react-router-dom";
import NavBar from "./components/NavBar";
import ProtectedRoute from "./components/ProtectedRoute";
import { useAuth } from "./context/AuthContext";
import Dashboard from "./pages/Dashboard";
import JobDetail from "./pages/JobDetail";
import Login from "./pages/Login";
import Register from "./pages/Register";
import NotFound from "./pages/NotFound";
import JobNotFound from "./pages/JobNotFound";
import ErrorBoundary from "./components/error/ErrorBoundary";
import AuthSkeleton from "./components/AuthSkeleton";

function JobDetailWrapper() {
  const { id } = useParams();
  return <JobDetail key={id} />;
}

export default function App() {
  const { user, loading } = useAuth();

  if (loading) return <AuthSkeleton />;

  return (
    <ErrorBoundary>
      <NavBar />
      <Routes>
        <Route path="/login" element={user ? <Navigate to="/" replace /> : <Login />} />
        <Route path="/register" element={user ? <Navigate to="/" replace /> : <Register />} />
        <Route
          path="/"
          element={
            <ProtectedRoute>
              <Dashboard />
            </ProtectedRoute>
          }
        />
        <Route
          path="/jobs/:id"
          element={
            <ProtectedRoute>
              <JobDetailWrapper />
            </ProtectedRoute>
          }
        />
        <Route path="/jobs/not-found" element={<JobNotFound />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </ErrorBoundary>
  );
}
