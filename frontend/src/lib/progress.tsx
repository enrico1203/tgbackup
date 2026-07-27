import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { getToken } from "./api";
import type { DownloadProgress, JobProgress, ProgressFrame, RestoreProgress } from "./types";

interface ProgressState {
  jobs: Map<number, JobProgress>;
  downloads: Map<number, DownloadProgress>;
  restores: Map<string, RestoreProgress>;
  history: Map<number, number[]>;
  downloadHistory: Map<number, number[]>;
  connected: boolean;
}

const ProgressContext = createContext<ProgressState>({
  jobs: new Map(),
  downloads: new Map(),
  restores: new Map(),
  history: new Map(),
  downloadHistory: new Map(),
  connected: false,
});

const HISTORY_LENGTH = 40;

/** Keeps the last speeds of every running job, for the sparkline. */
function record(
  history: Map<number, number[]>,
  entries: { job_id: number; speed_bps: number }[],
): void {
  const seen = new Set<number>();
  for (const entry of entries) {
    seen.add(entry.job_id);
    const series = history.get(entry.job_id) ?? [];
    series.push(entry.speed_bps);
    if (series.length > HISTORY_LENGTH) series.shift();
    history.set(entry.job_id, series);
  }
  for (const key of history.keys()) {
    if (!seen.has(key)) history.delete(key);
  }
}

export function ProgressProvider({ children }: { children: ReactNode }) {
  const [jobs, setJobs] = useState<Map<number, JobProgress>>(new Map());
  const [downloads, setDownloads] = useState<Map<number, DownloadProgress>>(new Map());
  const [restores, setRestores] = useState<Map<string, RestoreProgress>>(new Map());
  const [connected, setConnected] = useState(false);
  const history = useRef<Map<number, number[]>>(new Map());
  // Kept apart from the sync jobs: the two are numbered independently and one map would
  // have the speed of job 3 land on the sparkline of download job 3.
  const downloadHistory = useRef<Map<number, number[]>>(new Map());

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
        // An older backend sends no downloads: the empty list keeps the rest working.
        const running = frame.downloads ?? [];
        record(history.current, frame.jobs);
        record(downloadHistory.current, running);
        setJobs(new Map(frame.jobs.map((job) => [job.job_id, job])));
        setDownloads(new Map(running.map((job) => [job.job_id, job])));
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
    () => ({
      jobs,
      downloads,
      restores,
      history: history.current,
      downloadHistory: downloadHistory.current,
      connected,
    }),
    [jobs, downloads, restores, connected],
  );

  return <ProgressContext.Provider value={value}>{children}</ProgressContext.Provider>;
}

export function useProgress(): ProgressState {
  return useContext(ProgressContext);
}

export function useJobProgress(jobId: number): JobProgress | undefined {
  return useProgress().jobs.get(jobId);
}
