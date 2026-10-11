import { createContext, useContext, useEffect, useState } from "react";
import api from "../lib/api";
import { connectSocket, disconnectSocket } from "../lib/socket";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(() => sessionStorage.getItem("token"));
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function bootstrap() {
      try {
        const { data } = await api.post("/auth/refresh");
        sessionStorage.setItem("token", data.token);
        setUser(data.user);
        setToken(data.token);
        connectSocket(data.token);
      } catch (error) {
        if (error.response?.status === 401) {
          sessionStorage.removeItem("token");
          setToken(null);
        } else {
          console.error("Auth bootstrap encountered a temporary error:", error.message);
        }
      } finally {
        setLoading(false);
      }
    }
    bootstrap();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function applyAuth(newToken, newUser) {
    sessionStorage.setItem("token", newToken);
    setToken(newToken);
    setUser(newUser);
    connectSocket(newToken);
  }

  async function login(email, password) {
    const { data } = await api.post("/auth/login", { email, password });
    applyAuth(data.token, data.user);
  }

  async function register(name, email, password) {
    const { data } = await api.post("/auth/register", { name, email, password });
    applyAuth(data.token, data.user);
  }

  function logout() {
    api.post("/auth/logout").catch(() => {});
    sessionStorage.removeItem("token");
    setToken(null);
    setUser(null);
    disconnectSocket();
  }

  useEffect(() => {
    const refresh = (event) => {
      setToken(event.detail.token);
      setUser(event.detail.user);
      connectSocket(event.detail.token);
    };
    const expired = () => logout();
    window.addEventListener("auth:refreshed", refresh);
    window.addEventListener("auth:expired", expired);
    return () => {
      window.removeEventListener("auth:refreshed", refresh);
      window.removeEventListener("auth:expired", expired);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <AuthContext.Provider value={{ user, token, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
