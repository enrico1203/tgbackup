/* The weekly window as a 7 by 24 grid: one cell per hour, painted by dragging.
   The value travels as the same 168 character string the backend stores, Monday 00:00
   first, so nothing has to be translated on the way in or out.

   Painting is driven from the container rather than from each cell: a touch pointer
   keeps firing on the element it started on, so pointerenter on the cells would only
   ever work with a mouse. elementFromPoint gives the cell under the finger instead. */

import { useCallback, useEffect, useRef, useState } from "react";

export const HOURS = 168;
export const ALWAYS = "1".repeat(HOURS);

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

type Preset = { label: string; spec: string };

function build(open: (day: number, hour: number) => boolean): string {
  let out = "";
  for (let day = 0; day < 7; day += 1) {
    for (let hour = 0; hour < 24; hour += 1) out += open(day, hour) ? "1" : "0";
  }
  return out;
}

const PRESETS: Preset[] = [
  { label: "Always", spec: ALWAYS },
  { label: "Nights, 22 to 07", spec: build((_d, h) => h >= 22 || h < 7) },
  { label: "Outside office hours", spec: build((d, h) => d >= 5 || h >= 19 || h < 8) },
  { label: "Weekend only", spec: build((d) => d >= 5) },
];

export function openHours(spec: string): number {
  return spec.split("").filter((char) => char === "1").length;
}

/** "Always", or the count, which is the only honest summary of an arbitrary window. */
export function describeSchedule(spec: string): string {
  const open = openHours(spec);
  if (open === HOURS) return "always";
  if (open === 0) return "never";
  return `${open} h/week`;
}

export default function ScheduleGrid({
  value,
  onChange,
}: {
  value: string;
  onChange: (next: string) => void;
}) {
  const spec = value.length === HOURS ? value : ALWAYS;
  // What a drag writes, decided by the first cell it touches: dragging over an open
  // hour closes the whole stroke, and the other way round.
  const painting = useRef<"0" | "1" | null>(null);
  const [dragging, setDragging] = useState(false);

  const write = useCallback(
    (indexes: number[], char: "0" | "1") => {
      const chars = spec.split("");
      let changed = false;
      for (const index of indexes) {
        if (chars[index] !== char) {
          chars[index] = char;
          changed = true;
        }
      }
      if (changed) onChange(chars.join(""));
    },
    [spec, onChange],
  );

  const paintAt = useCallback(
    (x: number, y: number) => {
      const element = document.elementFromPoint(x, y);
      const raw = element?.getAttribute("data-slot");
      if (raw === null || raw === undefined) return;
      const index = Number(raw);
      if (painting.current === null) painting.current = spec[index] === "1" ? "0" : "1";
      write([index], painting.current);
    },
    [spec, write],
  );

  useEffect(() => {
    if (!dragging) return undefined;
    const move = (event: PointerEvent) => paintAt(event.clientX, event.clientY);
    const up = () => {
      painting.current = null;
      setDragging(false);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
    };
  }, [dragging, paintAt]);

  const toggleDay = (day: number) => {
    const indexes = Array.from({ length: 24 }, (_, hour) => day * 24 + hour);
    const allOpen = indexes.every((index) => spec[index] === "1");
    write(indexes, allOpen ? "0" : "1");
  };

  const toggleHour = (hour: number) => {
    const indexes = Array.from({ length: 7 }, (_, day) => day * 24 + hour);
    const allOpen = indexes.every((index) => spec[index] === "1");
    write(indexes, allOpen ? "0" : "1");
  };

  return (
    <div className="schedule">
      <div
        className="schedule-grid"
        onPointerDown={(event) => {
          event.preventDefault();
          painting.current = null;
          setDragging(true);
          paintAt(event.clientX, event.clientY);
        }}
      >
        <div className="schedule-corner" />
        {Array.from({ length: 24 }, (_, hour) => (
          <button
            key={`h${hour}`}
            type="button"
            className="schedule-head"
            title={`Toggle ${String(hour).padStart(2, "0")}:00 on every day`}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={() => toggleHour(hour)}
          >
            {hour % 3 === 0 ? String(hour).padStart(2, "0") : ""}
          </button>
        ))}

        {DAYS.map((label, day) => (
          <div className="schedule-row" key={label} style={{ display: "contents" }}>
            <button
              type="button"
              className="schedule-day"
              title={`Toggle the whole of ${label}`}
              onPointerDown={(event) => event.stopPropagation()}
              onClick={() => toggleDay(day)}
            >
              {label}
            </button>
            {Array.from({ length: 24 }, (_, hour) => {
              const index = day * 24 + hour;
              return (
                <div
                  key={index}
                  data-slot={index}
                  className={spec[index] === "1" ? "schedule-cell on" : "schedule-cell"}
                  title={`${label} ${String(hour).padStart(2, "0")}:00`}
                />
              );
            })}
          </div>
        ))}
      </div>

      <div className="row wrap" style={{ gap: 8 }}>
        {PRESETS.map((preset) => (
          <button
            key={preset.label}
            type="button"
            className={spec === preset.spec ? "btn small" : "btn ghost small"}
            onClick={() => onChange(preset.spec)}
          >
            {preset.label}
          </button>
        ))}
        <button
          type="button"
          className="btn ghost small"
          onClick={() => onChange("0".repeat(HOURS))}
        >
          Clear
        </button>
        <button
          type="button"
          className="btn ghost small"
          onClick={() => onChange(spec.split("").map((c) => (c === "1" ? "0" : "1")).join(""))}
        >
          Invert
        </button>
      </div>
    </div>
  );
}
