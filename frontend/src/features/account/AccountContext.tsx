import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api } from "../../services/api";
import type { AccountDetail } from "../../lib/types";
import { useAuth } from "../auth/AuthContext";

interface AccountContextValue {
  accounts: AccountDetail[];
  activeAccount: AccountDetail | null;
  activeId: string | null;
  loading: boolean;
  error: string | null;
  setActiveId: (id: string) => void;
  reload: () => Promise<void>;
}

const AccountContext = createContext<AccountContextValue | null>(null);
const ACTIVE_KEY = "aurex.activeAccount";

export function AccountProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [accounts, setAccounts] = useState<AccountDetail[]>([]);
  const [activeId, setActiveIdState] = useState<string | null>(() => {
    try {
      return localStorage.getItem(ACTIVE_KEY);
    } catch {
      return null;
    }
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!user) {
      setAccounts([]);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const list = await api.listAccounts();
      setAccounts(list);
      if (list.length && !list.some((a) => a.id === activeId)) {
        setActiveIdState(list[0].id);
        try {
          localStorage.setItem(ACTIVE_KEY, list[0].id);
        } catch {
          /* ignore */
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load accounts.");
    } finally {
      setLoading(false);
    }
  }, [user, activeId]);

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id]);

  const setActiveId = useCallback((id: string) => {
    setActiveIdState(id);
    try {
      localStorage.setItem(ACTIVE_KEY, id);
    } catch {
      /* ignore */
    }
  }, []);

  const activeAccount = useMemo(
    () => accounts.find((a) => a.id === activeId) ?? accounts[0] ?? null,
    [accounts, activeId],
  );

  const value = useMemo(
    () => ({
      accounts,
      activeAccount,
      activeId: activeAccount?.id ?? null,
      loading,
      error,
      setActiveId,
      reload,
    }),
    [accounts, activeAccount, loading, error, setActiveId, reload],
  );

  return <AccountContext.Provider value={value}>{children}</AccountContext.Provider>;
}

export function useAccounts(): AccountContextValue {
  const context = useContext(AccountContext);
  if (!context) throw new Error("useAccounts must be used inside <AccountProvider>");
  return context;
}
