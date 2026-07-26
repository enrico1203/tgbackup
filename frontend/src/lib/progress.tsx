import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { getToken } from "./api";
import type { JobProgress, ProgressFrame, RestoreProgress } from "./types";

interface ProgressState {
  jobs: Map<number, JobProgress>;
  restores: Map<string, RestoreProgress>;
  history: Map<number, number[]>;
  connected: boolean;
}

const ProgressContext = createContext<ProgressState>({
  jobs: new Map(),
  restores: new Map(),
  history: new Map(),
  connected: false,
});

const HISTORY_LENGTH = 40;

export function ProgressProvider({ children }: { children: ReactNode }) {
  const [jobs, setJobs] = useState<Map<number, JobProgress>>(new Map());
  const [restores, setRestores] = useState<Map<string, RestoreProgress>>(new Map());
  const [connected, setConnected] = useState(false);
  const history = useRef<Map<number, number[]>>(new Map());

  useEffect(() => {
    const token = getToken();
    if (!token) return;

    let socket: WebSocket | null = null;
    let retry: number | undefined;
    let closed = false;

    const connect = () => {
      const scheme = window.location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(
        `${scheme}://${window.location.host}/ws/progress?token=${encodeURIComponent(token)}`,
      );

      socket.onopen = () => setConnected(true);

      socket.onmessage = (event) => {
        const frame = JSON.parse(event.data) as ProgressFrame;
        const nextJobs = new Map<number, JobProgress>();
        for (const job of frame.jobs) {
          nextJobs.set(job.job_id, job);
          const series = history.current.get(job.job_id) ?? [];
          series.push(job.speed_bps);
          if (series.length > HISTORY_LENGTH) series.shift();
          history.current.set(job.job_id, series);
        }
        for (const key of history.current.keys()) {
          if (!nextJobs.has(key)) history.current.delete(key);
        }
        setJobs(nextJobs);
        setRestores(new Map(frame.restores.map((r) => [r.restore_id, r])));
      };

      socket.onclose = () => {
        setConnected(false);
        // The tunnel or a backend restart close the socket: retry without hammering,
        // the interface stays usable with the REST data anyway.
        if (!closed) retry = window.setTimeout(connect, 3000);
      };

      socket.onerror = () => socket?.close();
    };

    connect();

    return () => {
      closed = true;
      if (retry) window.clearTimeout(retry);
      socket?.close();
    };
  }, []);

  const value = useMemo(
    () => ({ jobs, restores, history: history.current, connected }),
    [jobs, restores, connected],
  );

  return <ProgressContext.Provider value={value}>{children}</ProgressContext.Provider>;
}

export function useProgress(): ProgressState {
  return useContext(ProgressContext);
}

export function useJobProgress(jobId: number): JobProgress | undefined {
  return useProgress().jobs.get(jobId);
}
