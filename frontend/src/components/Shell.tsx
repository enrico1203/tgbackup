import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router";
import {
  Archive,
  CloudUpload,
  Gauge,
  History,
  LogOut,
  Moon,
  RefreshCcw,
  Sun,
  Users,
} from "lucide-react";

import { useAuth } from "../lib/auth";
import { useProgress } from "../lib/progress";
import { applyTheme, nextTheme, readTheme, resolveTheme, type Theme } from "../lib/theme";
import { Pill } from "./ui";

const NAV = [
  { to: "/", label: "Dashboard", icon: Gauge, end: true },
  { to: "/jobs", label: "Sync job", icon: CloudUpload, end: false },
  { to: "/accounts", label: "Account Telegram", icon: Users, end: false },
  { to: "/files", label: "File e restore", icon: Archive, end: false },
  { to: "/runs", label: "Storico", icon: History, end: false },
];

const TITLES: Record<string, string> = {
  "/": "Dashboard",
  "/jobs": "Sync job",
  "/accounts": "Account Telegram",
  "/files": "File e restore",
  "/runs": "Storico esecuzioni",
};

export default function Shell() {
  const { user, logout } = useAuth();
  const { jobs, connected } = useProgress();
  const location = useLocation();
  const [theme, setTheme] = useState<Theme>(readTheme);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const running = jobs.size;
  const title =
    TITLES[location.pathname] ??
    (location.pathname.startsWith("/jobs/") ? "Dettaglio job" : "tgbackup");

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <CloudUpload size={18} />
          </div>
          <div>
            <div className="brand-name">tgbackup</div>
            <div className="brand-sub">Backup su Telegram</div>
          </div>
        </div>

        <nav className="nav">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) => (isActive ? "nav-item active" : "nav-item")}
            >
              <Icon size={17} />
              <span>{label}</span>
              {to === "/jobs" && running > 0 ? (
                <span className="badge">
                  <Pill tone="ok" live>
                    {running}
                  </Pill>
                </span>
              ) : null}
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-foot">
          <button
            type="button"
            className="nav-item"
            onClick={() => setTheme((current) => nextTheme(current))}
          >
            {resolveTheme(theme) === "dark" ? <Sun size={17} /> : <Moon size={17} />}
            <span>{resolveTheme(theme) === "dark" ? "Tema chiaro" : "Tema scuro"}</span>
          </button>
          <button type="button" className="nav-item" onClick={logout}>
            <LogOut size={17} />
            <span>Esci da {user?.username}</span>
          </button>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <h1>{title}</h1>
          <div className="topbar-actions">
            {connected ? (
              <Pill tone="ok" live>
                Tempo reale
              </Pill>
            ) : (
              <Pill tone="mute">
                <RefreshCcw size={11} />
                Riconnessione
              </Pill>
            )}
          </div>
        </header>
        <div className="content">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
