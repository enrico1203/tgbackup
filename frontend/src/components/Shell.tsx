import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router";
import {
  Archive,
  CloudDownload,
  CloudUpload,
  Gauge,
  History,
  LogOut,
  Menu,
  Moon,
  PackageOpen,
  RefreshCcw,
  SlidersHorizontal,
  Sun,
  Users,
  Wrench,
  X,
} from "lucide-react";

import { useAuth } from "../lib/auth";
import { useProgress } from "../lib/progress";
import { applyTheme, nextTheme, readTheme, resolveTheme, type Theme } from "../lib/theme";
import { Pill } from "./ui";

const NAV = [
  { to: "/", label: "Dashboard", icon: Gauge, end: true },
  { to: "/jobs", label: "Sync jobs", icon: CloudUpload, end: false },
  { to: "/downloads", label: "Download jobs", icon: CloudDownload, end: false },
  { to: "/accounts", label: "Telegram accounts", icon: Users, end: false },
  { to: "/files", label: "Files and restore", icon: Archive, end: false },
  { to: "/runs", label: "History", icon: History, end: false },
  { to: "/export", label: "Export", icon: PackageOpen, end: false },
  { to: "/maintenance", label: "Maintenance", icon: Wrench, end: false },
  { to: "/settings", label: "Settings", icon: SlidersHorizontal, end: false },
];

const TITLES: Record<string, string> = {
  "/": "Dashboard",
  "/jobs": "Sync jobs",
  "/downloads": "Download jobs",
  "/accounts": "Telegram accounts",
  "/files": "Files and restore",
  "/runs": "Run history",
  "/export": "Export and import",
  "/maintenance": "Channel maintenance",
  "/settings": "Settings",
};

export default function Shell() {
  const { user, logout } = useAuth();
  const { jobs, downloads, connected } = useProgress();
  const location = useLocation();
  const [theme, setTheme] = useState<Theme>(readTheme);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // Changing page closes the menu on its own: on a phone it would otherwise stay open
  // over the content just chosen.
  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    // Blocks scrolling behind the open menu.
    document.body.classList.add("no-scroll");
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.classList.remove("no-scroll");
      window.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  const running = jobs.size;
  const downloading = downloads.size;
  const title =
    TITLES[location.pathname] ??
    (location.pathname.startsWith("/jobs/") ? "Job detail" : "tgbackup");

  return (
    <div className={menuOpen ? "shell menu-open" : "shell"}>
      <button
        type="button"
        className="scrim"
        aria-label="Close the menu"
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
            <div className="brand-sub">Backup to Telegram</div>
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
              {(to === "/jobs" && running > 0) || (to === "/downloads" && downloading > 0) ? (
                <span className="badge">
                  <Pill tone="ok" live>
                    {to === "/jobs" ? running : downloading}
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
            <span>{resolveTheme(theme) === "dark" ? "Light theme" : "Dark theme"}</span>
          </button>
          <button type="button" className="nav-item" onClick={logout}>
            <LogOut size={17} />
            <span>Sign out {user?.username}</span>
          </button>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <button
            type="button"
            className="icon-btn menu-button"
            aria-label={menuOpen ? "Close the menu" : "Open the menu"}
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((open) => !open)}
          >
            {menuOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
          <h1>{title}</h1>
          <div className="topbar-actions">
            {connected ? (
              <Pill tone="ok" live>
                Live
              </Pill>
            ) : (
              <Pill tone="mute">
                <RefreshCcw size={11} />
                Reconnecting
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
