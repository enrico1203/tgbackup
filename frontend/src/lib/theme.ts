const KEY = "tgbackup.theme";

export type Theme = "system" | "light" | "dark";

export function readTheme(): Theme {
  const stored = localStorage.getItem(KEY);
  return stored === "light" || stored === "dark" ? stored : "system";
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") {
    root.removeAttribute("data-theme");
    localStorage.removeItem(KEY);
  } else {
    root.setAttribute("data-theme", theme);
    localStorage.setItem(KEY, theme);
  }
}

export function nextTheme(current: Theme): Theme {
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  if (current === "system") return prefersDark ? "light" : "dark";
  return "system";
}

export function resolveTheme(theme: Theme): "light" | "dark" {
  if (theme !== "system") return theme;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
