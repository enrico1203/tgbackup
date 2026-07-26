import type { ReactNode } from "react";
import { AlertCircle, Loader2, X } from "lucide-react";

import { percent } from "../lib/format";

export function Card({ children }: { children: ReactNode }) {
  return <div className="card">{children}</div>;
}

export function CardHead({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="card-head">
      <h2>{title}</h2>
      {children ? <div className="spacer">{children}</div> : null}
    </div>
  );
}

export function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
}) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {hint ? <span className="stat-hint">{hint}</span> : null}
    </div>
  );
}

type PillTone = "accent" | "ok" | "warn" | "bad" | "mute";

export function Pill({
  tone = "accent",
  live = false,
  children,
}: {
  tone?: PillTone;
  live?: boolean;
  children: ReactNode;
}) {
  const cls = tone === "accent" ? "pill" : `pill ${tone}`;
  return (
    <span className={cls}>
      <span className={live ? "dot live" : "dot"} />
      {children}
    </span>
  );
}

export function ProgressBar({ done, total }: { done: number; total: number }) {
  const value = percent(done, total);
  return (
    <div className="bar">
      <div className="bar-fill" style={{ width: `${value}%` }} />
      {value > 0 && value < 100 ? (
        <div className="bar-cap" style={{ left: `${value}%` }} />
      ) : null}
    </div>
  );
}

export function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) {
    return <svg className="spark" viewBox="0 0 100 34" preserveAspectRatio="none" />;
  }

  const max = Math.max(...values, 1);
  const step = 100 / (values.length - 1);
  const points = values.map((v, i) => [i * step, 34 - (v / max) * 30] as const);
  const line = points.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
  const area = `0,34 ${line} 100,34`;
  const last = points[points.length - 1];

  return (
    <svg className="spark" viewBox="0 0 100 34" preserveAspectRatio="none" aria-hidden="true">
      <polygon points={area} fill="var(--accent)" opacity="0.14" />
      <polyline
        points={line}
        fill="none"
        stroke="var(--accent)"
        strokeWidth="1.6"
        vectorEffect="non-scaling-stroke"
        strokeLinejoin="round"
      />
      <circle cx={last[0]} cy={last[1]} r="2" fill="var(--accent)" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function Alert({ children, tone = "error" }: { children: ReactNode; tone?: "error" | "info" }) {
  return (
    <div className={tone === "info" ? "alert info" : "alert"}>
      <AlertCircle size={16} style={{ flexShrink: 0, marginTop: 1 }} />
      <span>{children}</span>
    </div>
  );
}

export function Empty({ icon, title, hint }: { icon: ReactNode; title: string; hint?: string }) {
  return (
    <div className="empty">
      {icon}
      <div style={{ fontWeight: 600, color: "var(--text)" }}>{title}</div>
      {hint ? <div style={{ maxWidth: "44ch" }}>{hint}</div> : null}
    </div>
  );
}

export function Spinner({ size = 16 }: { size?: number }) {
  return <Loader2 size={size} className="spin" />;
}

export function Modal({
  title,
  onClose,
  children,
  footer,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div
      className="modal-backdrop"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head">
          <h2>{title}</h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer ? <div className="modal-foot">{footer}</div> : null}
      </div>
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="field">
      <label>{label}</label>
      {children}
      {hint ? <span className="hint">{hint}</span> : null}
    </div>
  );
}
