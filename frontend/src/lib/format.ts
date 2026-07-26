const UNITS = ["B", "KB", "MB", "GB", "TB", "PB"];

export function formatBytes(value: number, digits = 1): string {
  if (!value || value < 0) return "0 B";
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < UNITS.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit === 0 ? 0 : digits)} ${UNITS[unit]}`;
}

export function formatSpeed(bytesPerSecond: number): string {
  if (!bytesPerSecond || bytesPerSecond < 1) return "0 B/s";
  return `${formatBytes(bytesPerSecond)}/s`;
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return "unknown";
  const total = Math.round(seconds);
  if (total < 60) return `${total}s`;

  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);

  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m ${total % 60}s`;
}

export function formatDateTime(value: string | null): string {
  if (!value) return "never";
  return new Date(value).toLocaleString("en-US", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatRelative(value: string | null): string {
  if (!value) return "never";
  const delta = (new Date(value).getTime() - Date.now()) / 1000;
  const absolute = Math.abs(delta);
  const suffix = delta < 0 ? "ago" : "";
  const prefix = delta >= 0 ? "in" : "";
  const text = formatDuration(absolute);
  return `${prefix} ${text} ${suffix}`.trim();
}

export function formatInterval(hours: number): string {
  if (hours < 1) return `${Math.round(hours * 60)} minutes`;
  if (hours === 1) return "1 hour";
  if (hours % 24 === 0) {
    const days = hours / 24;
    return days === 1 ? "1 day" : `${days} days`;
  }
  return `${hours} hours`;
}

export function percent(done: number, total: number): number {
  if (!total) return 0;
  return Math.min(100, Math.max(0, (done / total) * 100));
}
