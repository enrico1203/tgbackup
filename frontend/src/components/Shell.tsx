import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router";
import {
  Archive,
  CloudUpload,
  Gauge,
  History,
  LogOut,
  Menu,
  Moon,
  RefreshCcw,
  SlidersHorizontal,
  Sun,
  Users,
  X,
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
  { to: "/settings", label: "Impostazioni", icon: SlidersHorizontal, end: false },
];

const TITLES: Record<string, string> = {
  "/": "Dashboard",
  "/jobs": "Sync job",
  "/accounts": "Account Telegram",
  "/files": "File e restore",
  "/runs": "Storico esecuzioni",
  "/settings": "Impostazioni",
};

export default function Shell() {
  const { user, logout } = useAuth();
  const { jobs, connected } = useProgress();
  const location = useLocation();
  const [theme, setTheme] = useState<Theme>(readTheme);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // Cambiando pagina il menu si richiude da solo: su telefono resterebbe aperto
  // sopra il contenuto appena scelto.
  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    // Blocca lo scorrimento dietro al menu aperto.
    document.body.classList.add("no-scroll");
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.classList.remove("no-scroll");
      window.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  const running = jobs.size;
  const title =
    TITLES[location.pathname] ??
    (location.pathname.startsWith("/jobs/") ? "Dettaglio job" : "tgbackup");

  return (
    <div className={menuOpen ? "shell menu-open" : "shell"}>
      <button
        type="button"
        className="scrim"
        aria-label="Chiudi il menu"
        tabIndex={menuOpen ? 0 : -1}
        onClick={() => setMenuOpen(false)}
      />

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
          <button
            type="button"
            className="icon-btn menu-button"
            aria-label={menuOpen ? "Chiudi il menu" : "Apri il menu"}
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((open) => !open)}
          >
            {menuOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
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
