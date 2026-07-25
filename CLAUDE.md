# tgbackup

Applicazione self-hosted che tiene specchiate cartelle locali dentro canali Telegram privati.

## Regole operative (vincolanti)

**Niente ambiente di test.** Nessuna suite di test, nessuno staging, nessun ambiente di prova.
Si scrive direttamente il codice di produzione e si mette in esecuzione con `docker compose up -d --build`.
Non perdere tempo a provare: scrivilo e basta.

**Niente emoji.** In nessun punto del progetto: interfaccia, codice, commenti, log, messaggi di commit,
documentazione. Per le icone si usa `lucide-react` (SVG).

**Niente scrittura sui dati dell'utente.** Le cartelle da salvare sono montate `:ro` nel compose.

## Stack

Backend: Python 3.14 (Alpine), FastAPI, SQLAlchemy 2.0 async su SQLite (aiosqlite), Telethon 1.44, cryptg.
Frontend: React 19, Vite 8, TypeScript 7, react-router 8, TanStack Query 5, lucide-react.
Infra: docker compose con `backend`, `frontend` (nginx), `cloudflared`.

`cryptg` non pubblica wheel musllinux: il Dockerfile del backend ha uno stage builder con Rust che lo
compila. Senza cryptg l'AES-IGE gira in Python puro e l'upload diventa CPU-bound.

**Perche Telethon 1.44 e non v2** (verificato il 2026-07-25, non riaprire la questione senza ricontrollare):
v2 non e mai stato rilasciato. Su PyPI ci sono 242 release e nessuna 2.x. Il branch `v2` su Codeberg
dichiara `2.0.0a0`, il branch di default del repo resta `v1`, e l'ultimo commit su v2 (18-06-2026)
sostituisce l'intero sender MTProto con un'implementazione Rust: rewrite in corso, da compilare da
sorgente. Inoltre l'upload di v2 non e parallelo (nessun gather o worker in `files.py`), quindi non
darebbe alcun vantaggio di velocita. Il parallelismo lo mette `fast_transfer.py` usando internals di
v1 che sono stabili da anni.

## Struttura

```
backend/app/
  config.py db.py models.py schemas.py security.py deps.py main.py
  api/       auth accounts channels jobs files dashboard ws
  telegram/  manager.py (client registry) fast_transfer.py (upload/download paralleli)
  sync/      scanner.py runner.py scheduler.py progress.py
frontend/src/
  pages/ components/ lib/
```

## Concetti chiave

**Identita di un file**: `(rel_path, size, mtime_ns)`. La scansione fa una sola `stat` per file e non
legge mai il contenuto. Se cambia size o mtime il file viene cancellato da Telegram e ricaricato.
Un rinominato e un `to_delete` piu un `pending`.

**Split**: 8000 parti da 512KB e il tetto MTProto, cioe 3.90625 GiB. Default 3.9e9 byte per account
Premium, 1.9e9 per gli altri (rilevato da `get_me().premium`).

**Upload parallelo**: `fast_transfer.py` apre fino a 20 `MTProtoSender` sulla stessa auth key.
Oltre 20 connessioni per DC Telegram le blocca tutte. Il lettore e sequenziale, i sender lavorano in
parallelo, quindi si puo caricare una fetta di file grande senza file temporanei.

**Scheduler**: supervisor asyncio, tick da 10s. Lo stato `running` sta in DB, quindi lo stesso job non
si sovrappone mai a se stesso anche se dura giorni. `next_run_at` viene calcolato dalla fine del run.
Job sullo stesso account Telegram condividono un semaforo per non contendersi le connessioni.

## Comandi

```
cp .env.example .env      # poi compila CLOUDFLARE_TUNNEL_TOKEN e APP_SECRET
docker compose up -d --build
docker compose logs -f backend
docker compose restart backend
```

Accesso: `http://127.0.0.1:8080` oppure l'hostname del tunnel Cloudflare.
Credenziali iniziali `admin` / `admin`, il cambio password e obbligatorio al primo accesso.

## Aggiungere una cartella al backup

In `docker-compose.yml`, servizio `backend`, aggiungere il volume:

```yaml
      - /percorso/host:/percorso/host:ro
```

poi `docker compose up -d backend`. Nella UI il path del sync job e quello interno al container.
