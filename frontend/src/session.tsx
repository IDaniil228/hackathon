import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { clearSession, refresh, request, send, setToken } from "./api/client";
import type { Row } from "./ui";
import { clearActions, setIdentity } from "./api/actions";

const Context = createContext<{
  user: Row | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  reload: () => Promise<void>;
}>(null!);
export const useSession = () => useContext(Context);
export function Session({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Row | null>(null);
  const [loading, setLoading] = useState(true);
  const queries = useQueryClient();
  const reload = async () => {
    const me = await request("/me");
    setIdentity(me.id);
    setUser(me);
  };
  useEffect(() => {
    let active = true;
    refresh()
      .then(() => request("/me"))
      .then((value) => {
        if (active) {
          setIdentity(value.id);
          setUser(value);
        }
      })
      .catch(() => clearSession())
      .finally(() => {
        if (active) setLoading(false);
      });
    const channel = new BroadcastChannel("scepka-session");
    channel.onmessage = () => {
      clearSession();
      clearActions();
      queries.clear();
      setUser(null);
    };
    return () => {
      active = false;
      channel.close();
    };
  }, [queries]);
  async function login(email: string, password: string) {
    const result = await send("/auth/login", { email, password });
    queries.clear();
    setToken(result.access_token);
    await reload();
  }
  async function logout() {
    await send("/auth/logout");
    clearSession();
    clearActions();
    queries.clear();
    setUser(null);
    const channel = new BroadcastChannel("scepka-session");
    channel.postMessage("logout");
    channel.close();
  }
  return (
    <Context.Provider value={{ user, loading, login, logout, reload }}>
      {children}
    </Context.Provider>
  );
}
