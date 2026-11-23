import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, ApiError, tokenStore } from "../../services/api";
import type { SystemMeta, User } from "../../lib/types";

interface AuthContextValue {
  user: User | null;
  meta: SystemMeta | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (input: { email: string; full_name: string; password: string }) => Promise<void>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [meta, setMeta] = useState<SystemMeta | null>(null);
  const [loading, setLoading] = useState(true);

  const bootstrap = useCallback(async () => {
    setLoading(true);
    try {
      const metaResponse = await api.meta();
      setMeta(metaResponse);
      if (tokenStore.access) {
        try {
          setUser(await api.me());
        } catch (error) {
          if (error instanceof ApiError && error.status === 401) tokenStore.clear();
          setUser(null);
        }
      }
    } catch {
      // The API may be unreachable in a static deployment: the UI still renders
      // with the demo/paper guardrails and explains the state to the user.
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  const login = useCallback(async (email: string, password: string) => {
    await api.login({ email, password });
    setUser(await api.me());
  }, []);

  const register = useCallback(async (input: { email: string; full_name: string; password: string }) => {
    await api.register(input);
    await api.login({ email: input.email, password: input.password });
    setUser(await api.me());
  }, []);

  const logout = useCallback(async () => {
    await api.logout();
    setUser(null);
  }, []);

  const refreshUser = useCallback(async () => {
    if (!tokenStore.access) return;
    setUser(await api.me());
  }, []);

  const value = useMemo(
    () => ({ user, meta, loading, login, register, logout, refreshUser }),
    [user, meta, loading, login, register, logout, refreshUser],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>");
  return context;
}
