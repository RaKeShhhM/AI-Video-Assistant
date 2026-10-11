import axios from "axios";

export const API_URL = import.meta.env.VITE_API_URL || "http://localhost:5000";

const api = axios.create({ baseURL: `${API_URL}/api`, withCredentials: true });

api.interceptors.request.use((config) => {
  const token = sessionStorage.getItem("token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

let refreshPromise;
api.interceptors.response.use(undefined, async (error) => {
  const original = error.config;
  if (error.response?.status !== 401 || original?.url?.includes("/auth/refresh") || original?._retry) throw error;
  original._retry = true;
  refreshPromise ||= api.post("/auth/refresh").then(({ data }) => {
    sessionStorage.setItem("token", data.token);
    window.dispatchEvent(new CustomEvent("auth:refreshed", { detail: data }));
    return data.token;
  }).finally(() => { refreshPromise = undefined; });
  const token = await refreshPromise;
  original.headers.Authorization = `Bearer ${token}`;
  return api(original);
});

export default api;
