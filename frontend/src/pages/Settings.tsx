import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, FileText, HardDrive, PencilLine, Trash2 } from "lucide-react";

import { api } from "../lib/api";
import { formatDateTime } from "../lib/format";
import type { RcloneStatus } from "../lib/types";
import RemoteBrowser from "../components/RemoteBrowser";
import { Alert, Card, CardHead, Field, Pill, Spinner } from "../components/ui";

const PLACEHOLDER = `[miocloud]
type = drive
client_id = ...
token = {"access_token":"..."}

[miocloud-crypt]
type = crypt
remote = miocloud:cartella
password = ...`;

export default function Settings() {
  const queryClient = useQueryClient();
  const [content, setContent] = useState("");
  const [editing, setEditing] = useState(false);
  const [browsing, setBrowsing] = useState<string | null>(null);
  // Distingue "sto correggendo quella esistente" da "sto scrivendone una nuova":
  // cambia solo il testo dei pulsanti, ma evita di far credere che si stia
  // modificando quando invece si sta per sostituire tutto.
  const [mode, setMode] = useState<"edit" | "replace">("edit");

  const { data, isLoading } = useQuery({
    queryKey: ["rclone"],
    queryFn: () => api.get<RcloneStatus>("/api/rclone"),
  });

  const save = useMutation({
    mutationFn: () => api.put<RcloneStatus>("/api/rclone", { content }),
    onSuccess: (status) => {
      queryClient.setQueryData(["rclone"], status);
      // I remote possono essere cambiati: il form del job li rilegge.
      void queryClient.invalidateQueries({ queryKey: ["rclone"] });
      setContent("");
      setEditing(false);
    },
  });

  const remove = useMutation({
    mutationFn: () => api.del("/api/rclone"),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["rclone"] }),
  });

  // La configurazione in chiaro si chiede solo qui, non al caricamento della pagina.
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
        <CardHead title="Remote rclone">
          {data?.configured ? (
            <Pill tone="ok">
              <CheckCircle2 size={11} />
              {data.remotes.length} remote
            </Pill>
          ) : (
            <Pill tone="mute">Non configurato</Pill>
          )}
        </CardHead>

        <div className="card-body">
          <p style={{ margin: 0, color: "var(--muted)", maxWidth: "70ch" }}>
            Incollando qui il tuo <span className="mono">rclone.conf</span> puoi usare un
            remote come sorgente di un sync job, senza montarlo. I file vengono elencati via
            API e letti a intervalli di byte: niente mount, niente copie su disco.
          </p>

          {isLoading ? (
            <div className="row" style={{ color: "var(--muted)" }}>
              <Spinner /> Caricamento
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
                      {data.config_lines} righe di configurazione
                    </span>
                    <span style={{ color: "var(--muted)" }}>
                      aggiornata {formatDateTime(data.updated_at)}
                    </span>
                  </>
                ) : null}
              </div>

              {data?.error ? <Alert>{data.error}</Alert> : null}
              {save.isError ? <Alert>{(save.error as Error).message}</Alert> : null}

              {data?.configured && data.remotes.length > 0 ? (
                <div>
                  <span className="section-label">
                    Remote disponibili, premi per vedere cosa contengono
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
                        ? "Modifica rclone.conf"
                        : "Contenuto di rclone.conf"
                    }
                    hint={
                      mode === "edit" && data?.configured
                        ? "Aggiungi o correggi le sezioni che ti servono, il resto resta com'e. Al salvataggio rclone rilegge tutto."
                        : "Viene salvato cifrato nel database."
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
                    Questo file contiene le credenziali dei tuoi cloud. Cifrato a riposo, ma
                    chiunque acceda a questa interfaccia potra usare quei remote.
                  </Alert>

                  <div className="row">
                    <button
                      type="button"
                      className="btn"
                      disabled={!content.trim() || save.isPending}
                      onClick={() => save.mutate()}
                    >
                      {save.isPending ? <Spinner /> : null}
                      Salva e verifica
                    </button>
                    {data?.configured ? (
                      <button type="button" className="btn ghost" onClick={cancel}>
                        Annulla
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
                      Modifica la configurazione
                    </button>
                    <button type="button" className="btn ghost" onClick={startFresh}>
                      <FileText size={14} />
                      Riscrivi da zero
                    </button>
                    <button
                      type="button"
                      className="btn danger"
                      onClick={() => {
                        if (
                          window.confirm(
                            "Rimuovere la configurazione rclone? I job che usano un remote smetteranno di funzionare.",
                          )
                        ) {
                          remove.mutate();
                        }
                      }}
                    >
                      <Trash2 size={14} />
                      Rimuovi
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
