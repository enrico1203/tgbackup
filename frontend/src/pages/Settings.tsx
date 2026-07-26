import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, FileText, HardDrive, PencilLine, Trash2 } from "lucide-react";

import { api } from "../lib/api";
import { formatDateTime } from "../lib/format";
import type { RcloneStatus } from "../lib/types";
import RemoteBrowser from "../components/RemoteBrowser";
import { Alert, Card, CardHead, Field, Pill, Spinner } from "../components/ui";

const PLACEHOLDER = `[mycloud]
type = drive
client_id = ...
token = {"access_token":"..."}

[mycloud-crypt]
type = crypt
remote = mycloud:folder
password = ...`;

export default function Settings() {
  const queryClient = useQueryClient();
  const [content, setContent] = useState("");
  const [editing, setEditing] = useState(false);
  const [browsing, setBrowsing] = useState<string | null>(null);
  // Tells "I am fixing the existing one" apart from "I am writing a new one": it only
  // changes the button text, but it avoids suggesting an edit when everything is about to
  // be replaced.
  const [mode, setMode] = useState<"edit" | "replace">("edit");

  const { data, isLoading } = useQuery({
    queryKey: ["rclone"],
    queryFn: () => api.get<RcloneStatus>("/api/rclone"),
  });

  const save = useMutation({
    mutationFn: () => api.put<RcloneStatus>("/api/rclone", { content }),
    onSuccess: (status) => {
      queryClient.setQueryData(["rclone"], status);
      // The remotes may have changed: the job form reloads them.
      void queryClient.invalidateQueries({ queryKey: ["rclone"] });
      setContent("");
      setEditing(false);
    },
  });

  const remove = useMutation({
    mutationFn: () => api.del("/api/rclone"),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["rclone"] }),
  });

  // The configuration in clear is only requested here, not on page load.
  const load = useMutation({
    mutationFn: () => api.get<{ content: string }>("/api/rclone/content"),
    onSuccess: (result) => {
      setContent(result.content);
      setMode("edit");
      setEditing(true);
    },
  });

  const startFresh = () => {
    setContent("");
    setMode("replace");
    setEditing(true);
  };

  const cancel = () => {
    setEditing(false);
    setContent("");
  };

  return (
    <>
      <Card>
        <CardHead title="Rclone remotes">
          {data?.configured ? (
            <Pill tone="ok">
              <CheckCircle2 size={11} />
              {data.remotes.length} remote
            </Pill>
          ) : (
            <Pill tone="mute">Not configured</Pill>
          )}
        </CardHead>

        <div className="card-body">
          <p style={{ margin: 0, color: "var(--muted)", maxWidth: "70ch" }}>
            Pasting your <span className="mono">rclone.conf</span> here lets you use a remote
            as the source of a sync job, without mounting it. Files are listed through the API
            and read in byte ranges: no mount, no copies on disk.
          </p>

          {isLoading ? (
            <div className="row" style={{ color: "var(--muted)" }}>
              <Spinner /> Loading
            </div>
          ) : (
            <>
              <div className="row wrap" style={{ gap: 24, fontSize: 12.5 }}>
                <span style={{ color: "var(--muted)" }}>
                  rclone <span className="mono">{data?.version}</span>
                </span>
                {data?.configured ? (
                  <>
                    <span style={{ color: "var(--muted)" }} className="num">
                      {data.config_lines} configuration lines
                    </span>
                    <span style={{ color: "var(--muted)" }}>
                      updated {formatDateTime(data.updated_at)}
                    </span>
                  </>
                ) : null}
              </div>

              {data?.error ? <Alert>{data.error}</Alert> : null}
              {save.isError ? <Alert>{(save.error as Error).message}</Alert> : null}

              {data?.configured && data.remotes.length > 0 ? (
                <div>
                  <span className="section-label">
                    Available remotes, press one to see what it holds
                  </span>
                  <div className="row wrap" style={{ gap: 8, marginTop: 8 }}>
                    {data.remotes.map((remote) => (
                      <button
                        key={remote}
                        type="button"
                        className="pill mono remote-pill"
                        onClick={() => setBrowsing(remote)}
                      >
                        <HardDrive size={11} />
                        {remote}
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}

              {editing || !data?.configured ? (
                <>
                  <Field
                    label={
                      mode === "edit" && data?.configured
                        ? "Edit rclone.conf"
                        : "Contents of rclone.conf"
                    }
                    hint={
                      mode === "edit" && data?.configured
                        ? "Add or fix the sections you need, the rest stays as it is. On save rclone reloads everything."
                        : "It is stored encrypted in the database."
                    }
                  >
                    <textarea
                      className="mono config-box"
                      value={content}
                      placeholder={PLACEHOLDER}
                      spellCheck={false}
                      onChange={(event) => setContent(event.target.value)}
                    />
                  </Field>

                  <Alert tone="info">
                    This file holds your cloud credentials. Encrypted at rest, but anyone who
                    reaches this interface will be able to use those remotes.
                  </Alert>

                  <div className="row">
                    <button
                      type="button"
                      className="btn"
                      disabled={!content.trim() || save.isPending}
                      onClick={() => save.mutate()}
                    >
                      {save.isPending ? <Spinner /> : null}
                      Save and verify
                    </button>
                    {data?.configured ? (
                      <button type="button" className="btn ghost" onClick={cancel}>
                        Cancel
                      </button>
                    ) : null}
                  </div>
                </>
              ) : (
                <>
                  {load.isError ? <Alert>{(load.error as Error).message}</Alert> : null}
                  <div className="row wrap">
                    <button
                      type="button"
                      className="btn"
                      disabled={load.isPending}
                      onClick={() => load.mutate()}
                    >
                      {load.isPending ? <Spinner /> : <PencilLine size={14} />}
                      Edit the configuration
                    </button>
                    <button type="button" className="btn ghost" onClick={startFresh}>
                      <FileText size={14} />
                      Rewrite from scratch
                    </button>
                    <button
                      type="button"
                      className="btn danger"
                      onClick={() => {
                        if (
                          window.confirm(
                            "Remove the rclone configuration? Jobs using a remote will stop working.",
                          )
                        ) {
                          remove.mutate();
                        }
                      }}
                    >
                      <Trash2 size={14} />
                      Remove
                    </button>
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </Card>

      {browsing ? (
        <RemoteBrowser remote={browsing} onClose={() => setBrowsing(null)} />
      ) : null}
    </>
  );
}
