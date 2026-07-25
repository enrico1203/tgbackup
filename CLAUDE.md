# tgbackup

Applicazione self-hosted che tiene specchiate cartelle locali o remote rclone dentro canali
Telegram privati. Carica i file nuovi, cancella dal canale quelli spariti, ricarica quelli
modificati, spezza i file oltre la soglia e sa ricomporli.

## Regole operative (vincolanti)

**Niente ambiente di test.** Nessuna suite di test, nessuno staging. Si scrive direttamente il
codice di produzione e si mette in esecuzione. Non perdere tempo a provare: scrivilo e basta.

**Niente emoji.** In nessun punto: interfaccia, codice, commenti, log, commit, documentazione.
Per le icone si usa `lucide-react` (SVG).

**Niente scrittura sui dati dell'utente.** Le cartelle da salvare sono montate `:ro`.

**Nessun test distruttivo contro la produzione.** Non esiste un ambiente separato, quindi prima di
scrivere o cancellare tramite le API (configurazione rclone, account, job) si controlla se c'e gia
un valore reale. Una configurazione rclone dell'utente e stata persa proprio cosi. Le verifiche che
scrivono si fanno in un container usa e getta: `docker run --rm ... tgbackup-backend python ...`
con un `DATA_DIR` temporaneo.

**Attenzione a `docker compose up -d <servizio>`**: per via di `depends_on` riavvia anche il
backend e interrompe i job in corso. Usare `--no-deps`.

## Stack

Backend: Python 3.14 Alpine, FastAPI, SQLAlchemy 2.0 async su SQLite, Telethon 1.44, cryptg,
rclone 1.74 (binario statico ufficiale).
Frontend: React 19, Vite 8, TypeScript 7, react-router 8, TanStack Query 5, lucide-react.
Infra: docker compose con `backend`, `frontend` (nginx), `cloudflared`.

## Struttura

```
backend/app/
  config.py db.py models.py schemas.py security.py deps.py migrate.py main.py
  api/       auth accounts jobs files dashboard rclone ws
  telegram/  manager.py (registry client, peer) fast_transfer.py (upload/download paralleli)
  rclone/    client.py (lsjson, cat a intervalli, anteprima)
  sync/      source.py scanner.py runner.py scheduler.py restore.py progress.py
frontend/src/
  pages/     Login ChangePassword Dashboard Jobs Accounts Files Runs Settings
  components/ Shell ui JobActivity RemoteBrowser
  lib/       api auth progress types format theme
```

## Concetti chiave

**Identita di un file**: `(rel_path, size, mtime_ns)`. Mai letto il contenuto per capire cosa e
cambiato: una sola `stat` in locale, il campo `ModTime` di `lsjson` sui remote. Se cambia size o
mtime il file viene cancellato da Telegram e ricaricato. Un rinominato e `to_delete` piu `pending`.

**Split**: 8000 parti da 512KB e il tetto MTProto, cioe 3.90625 GiB. Default 3.9e9 byte per account
Premium, 1.9e9 per gli altri, rilevato da `get_me().premium`.

**Upload parallelo**: `fast_transfer.py` apre fino a 20 `MTProtoSender` sulla stessa auth key; oltre
20 per DC Telegram le blocca tutte. Il lettore e sequenziale e i sender lavorano in parallelo,
quindi si carica una fetta di file grande senza file temporanei. Foto e video sempre come documento
(`force_document=True`), mai ricompressi.

**Sorgenti**: `sync/source.py` astrae cartella locale e remote rclone dietro la stessa interfaccia
(`list_files`, `reader`). L'uploader non sa da dove arrivano i byte.

**rclone senza mount**: `lsjson -R` per elencare, `cat --offset --count` per le fette. Misurato su
un remote da 12 TB e 16.709 file: elenco 11,6 s contro circa 5 minuti camminando il mount FUSE,
lettura 43,5 MB/s contro 22 MB/s. Nessuno staging su disco. Verificato che i byte letti via `cat`
sono identici a quelli letti dal mount.

**Peer Telegram**: costruito da `tg_id` piu `access_hash` salvati, mai risolto con `get_entity` su
un id numerico. Un intero positivo nudo viene interpretato come utente, e `StringSession` non
conserva la cache delle entita, quindi dopo un riavvio la risoluzione fallirebbe.

**Scheduler**: supervisor asyncio con tick da 10 s. Lo stato `running` sta in DB, quindi lo stesso
job non si sovrappone mai a se stesso anche se dura giorni. `next_run_at` si calcola dalla fine del
run. Se a fermare un job e lo spegnimento del processo, `next_run_at` resta invariato e il job
riprende al riavvio invece di slittare di un intervallo intero. Job sullo stesso account condividono
un semaforo per non contendersi le connessioni.

**Durabilita**: ogni parte viene registrata in DB appena il messaggio e inviato. Un arresto a meta di
un file grande non lascia messaggi orfani: alla riscansione il file passa da `stale`, le parti gia
inviate vengono cancellate dal canale e il file riparte pulito.

**Segreti**: `api_hash`, sessioni Telegram e `rclone.conf` sono cifrati con Fernet derivato da
`APP_SECRET`. Cambiare `APP_SECRET` li rende illeggibili. Il `rclone.conf` torna in chiaro al
browser solo da `GET /api/rclone/content`, cioe solo premendo Modifica.

## Decisioni verificate, non riaprire senza ricontrollare

**Telethon 1.44, non v2** (verificato 2026-07-25). v2 non e mai stato rilasciato: 242 release su
PyPI, nessuna 2.x. Il branch `v2` dichiara `2.0.0a0`, il default del repo resta `v1`, e l'ultimo
commit su v2 sostituisce il sender MTProto con un'implementazione Rust. In piu l'upload di v2 non e
parallelo, quindi non darebbe vantaggi: il parallelismo lo mette `fast_transfer.py` con internals di
v1 stabili da anni.

**cryptg va compilato**: non pubblica wheel musllinux, quindi il Dockerfile ha uno stage builder con
Rust. Senza cryptg l'AES-IGE gira in Python puro e l'upload diventa CPU-bound.

**nginx ascolta su 80 e 8081**: l'ingress del tunnel Cloudflare punta a `frontend:8081`, mentre la
8081 pubblicata sull'host mappa sulla 80 interna.

**Il build controlla i tipi**: `vite build` traspila senza type check, e cosi era finito in
produzione un `api.put` mai definito. Ora `build` esegue prima `tsc --noEmit`.

**Migrazione all'avvio**: `create_all` non aggiunge colonne a tabelle esistenti, quindi `migrate.py`
confronta modelli e schema e fa gli `ALTER TABLE` mancanti.

## Comandi

```
cp .env.example .env      # poi compila CLOUDFLARE_TUNNEL_TOKEN e APP_SECRET
docker compose up -d --build
docker compose logs -f backend
docker compose up -d --no-deps backend    # senza toccare gli altri servizi
```

Accesso: `http://127.0.0.1:8081` o l'hostname del tunnel Cloudflare.
Credenziali iniziali `admin` / `admin`, cambio password obbligatorio al primo accesso.

## Aggiungere una sorgente

**Cartella locale**: volume in `docker-compose.yml`, servizio `backend`, come
`- /percorso/host:/percorso/host:ro`, poi `docker compose up -d --no-deps backend`. Nella UI il path
del job e quello interno al container.

**Remote rclone**: si incolla il `rclone.conf` in Impostazioni, poi nel job si sceglie Remote rclone
e si indica `nome-remote:` o `nome-remote:sottocartella`. Il pulsante Sfoglia apre il browser dei
remote, da cui si copia o si applica direttamente il percorso.
