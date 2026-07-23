import { createContext, useContext, useEffect, useState } from "react";
import api from "../lib/api";
import { connectSocket, disconnectSocket } from "../lib/socket";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(() => localStorage.getItem("token"));
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function bootstrap() {
      if (!token) {
        setLoading(false);
        return;
      }
      try {
        const { data } = await api.get("/auth/me");
        setUser(data.user);
        connectSocket(token);
      } catch {
        localStorage.removeItem("token");
        setToken(null);
      } finally {
        setLoading(false);
      }
    }
    bootstrap();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function applyAuth(newToken, newUser) {
    localStorage.setItem("token", newToken);
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
    localStorage.removeItem("token");
    setToken(null);
    setUser(null);
    disconnectSocket();
  }

  return (
    <AuthContext.Provider value={{ user, token, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
