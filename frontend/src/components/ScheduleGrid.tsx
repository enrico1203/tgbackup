/* The weekly window as a 7 by 24 grid: one cell per hour, painted by dragging.
   The value travels as the same 168 character string the backend stores, Monday 00:00
   first, so nothing has to be translated on the way in or out. An hour is closed "0",
   open "1", or open at the reduced speed set on the job "2": what is painted is whatever
   the brush above the grid is set to, which is the only way three states fit in a drag.

   Painting is driven from the container rather than from each cell: a touch pointer
   keeps firing on the element it started on, so pointerenter on the cells would only
   ever work with a mouse. elementFromPoint gives the cell under the finger instead. */

import { useCallback, useEffect, useRef, useState } from "react";

export const HOURS = 168;
export const ALWAYS = "1".repeat(HOURS);

export type Slot = "0" | "1" | "2";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const BRUSHES: { value: Slot; label: string; hint: string }[] = [
  { value: "1", label: "Full speed", hint: "The job runs with no limit" },
  { value: "2", label: "Limited", hint: "The job runs at the speed set below" },
  { value: "0", label: "Closed", hint: "The job does not start" },
];

type Preset = { label: string; spec: string };

function build(slot: (day: number, hour: number) => Slot): string {
  let out = "";
  for (let day = 0; day < 7; day += 1) {
    for (let hour = 0; hour < 24; hour += 1) out += slot(day, hour);
  }
  return out;
}

const PRESETS: Preset[] = [
  { label: "Always", spec: ALWAYS },
  { label: "Nights, 22 to 07", spec: build((_d, h) => (h >= 22 || h < 7 ? "1" : "0")) },
  {
    label: "Outside office hours",
    spec: build((d, h) => (d >= 5 || h >= 19 || h < 8 ? "1" : "0")),
  },
  { label: "Weekend only", spec: build((d) => (d >= 5 ? "1" : "0")) },
  {
    label: "Slow by day, free at night",
    spec: build((_d, h) => (h >= 8 && h < 20 ? "2" : "1")),
  },
];

export function openHours(spec: string): number {
  return spec.split("").filter((char) => char === "1" || char === "2").length;
}

export function limitedHours(spec: string): number {
  return spec.split("").filter((char) => char === "2").length;
}

/** "Always", or the counts, which is the only honest summary of an arbitrary window. */
export function describeSchedule(spec: string): string {
  const open = openHours(spec);
  const limited = limitedHours(spec);
  const tail = limited > 0 ? `, ${limited} limited` : "";
  if (open === HOURS && limited === 0) return "always";
  if (open === 0) return "never";
  return `${open} h/week${tail}`;
}

export default function ScheduleGrid({
  value,
  onChange,
}: {
  value: string;
  onChange: (next: string) => void;
}) {
  const spec = value.length === HOURS ? value : ALWAYS;
  // With three states a stroke can no longer decide for itself what it writes: the
  // brush says it, and a drag paints that one state everywhere it passes.
  const [brush, setBrush] = useState<Slot>("0");
  const painting = useRef(false);
  const [dragging, setDragging] = useState(false);

  const write = useCallback(
    (indexes: number[], char: Slot) => {
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
      write([Number(raw)], brush);
    },
    [brush, write],
  );

  useEffect(() => {
    if (!dragging) return undefined;
    const move = (event: PointerEvent) => paintAt(event.clientX, event.clientY);
    const up = () => {
      painting.current = false;
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

  // A whole line takes the brush, unless it already is the brush everywhere, in which
  // case it goes back to full speed: clicking twice undoes, which is what a header that
  // used to toggle led people to expect.
  const paintLine = (indexes: number[]) => {
    const uniform = indexes.every((index) => spec[index] === brush);
    write(indexes, uniform && brush !== "1" ? "1" : brush);
  };

  const toggleDay = (day: number) =>
    paintLine(Array.from({ length: 24 }, (_, hour) => day * 24 + hour));

  const toggleHour = (hour: number) =>
    paintLine(Array.from({ length: 7 }, (_, day) => day * 24 + hour));

  return (
    <div className="schedule">
      <div className="row wrap" style={{ gap: 8 }}>
        <span style={{ fontSize: 12.5, color: "var(--muted)" }}>Paint with</span>
        {BRUSHES.map((item) => (
          <button
            key={item.value}
            type="button"
            className={brush === item.value ? "btn small" : "btn ghost small"}
            title={item.hint}
            onClick={() => setBrush(item.value)}
          >
            <span className={`schedule-swatch s${item.value}`} />
            {item.label}
          </button>
        ))}
      </div>

      <div
        className="schedule-grid"
        onPointerDown={(event) => {
          event.preventDefault();
          painting.current = true;
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
                  className={
                    spec[index] === "1"
                      ? "schedule-cell on"
                      : spec[index] === "2"
                        ? "schedule-cell slow"
                        : "schedule-cell"
                  }
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
          onClick={() =>
            onChange(
              spec
                .split("")
                .map((char) => (char === "0" ? "1" : "0"))
                .join(""),
            )
          }
        >
          Invert
        </button>
      </div>
    </div>
  );
}
