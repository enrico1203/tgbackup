# tgbackup

Tiene una o piu cartelle locali specchiate dentro canali Telegram privati.
Carica i file nuovi, cancella dal canale quelli spariti in locale, ricarica quelli
modificati, e sa ricomporre i file spezzati in piu parti.

## Cosa fa

- **Scansione a basso impatto**: l'identita di un file e `(percorso, dimensione, mtime)`,
  letta con una sola `stat`. Il contenuto non viene mai aperto, nessun hash.
- **Upload veloce**: fino a 20 connessioni MTProto in parallelo sullo stesso account.
- **Split automatico**: i file oltre la soglia (3.9 GB con Telegram Premium, 1.9 GB senza)
  vengono divisi in parti, ognuna con il proprio message id salvato nel database.
- **Specchio reale**: file rinominato o modificato significa cancellazione dal canale
  e nuovo upload, cosi il canale corrisponde sempre alla cartella.
- **Job paralleli**: piu cartelle, piu canali, piu account Telegram insieme. Lo stesso job
  non parte mai due volte in contemporanea, anche se una corsa dura giorni.
- **Tempo reale**: velocita di upload, file rimanenti e tempo stimato via WebSocket.
- **Restore**: riscarica tutte le parti e ricompone il file originale.

Foto e video sono sempre inviati come documento, mai come media: nessuna ricompressione.

## Avvio

```bash
cp .env.example .env
```

Compila `.env`:

- `CLOUDFLARE_TUNNEL_TOKEN`: da Cloudflare Zero Trust, Networks, Tunnels.
  Il tunnel deve puntare a `http://frontend:80`.
- `APP_SECRET`: genera con `openssl rand -hex 32`. Firma i token e cifra le sessioni
  Telegram nel database. Se lo cambi, gli account vanno ricollegati.

Poi aggiungi le cartelle da salvare in `docker-compose.yml`, servizio `backend`, come
volumi in sola lettura:

```yaml
      - /mnt/documenti:/mnt/documenti:ro
```

Infine:

```bash
docker compose up -d --build
```

L'interfaccia risponde su `http://127.0.0.1:8081` e sull'hostname del tunnel Cloudflare.
Credenziali iniziali `admin` / `admin`, il cambio password e obbligatorio al primo accesso.

## Uso

1. **Account Telegram**: collega un account con api_id e api_hash presi da
   `my.telegram.org`, poi il numero, il codice ricevuto e, se attiva, la password a due
   fattori. La sessione resta valida finche non disconnetti l'account.
2. **Canali**: dopo il collegamento compaiono i canali privati dell'account.
3. **Sync job**: scegli cartella locale, account, canale, ogni quante ore eseguire e la
   velocita di scansione. Il job parte da solo, oppure con Avvia ora.
4. **File e restore**: elenco di tutto cio che e tracciato, con le parti e i message id.
   Il restore ricompone il file in `data/restore/` dentro il progetto.

## Documentazione tecnica

Scelte implementative, vincoli di protocollo e regole del progetto sono in `CLAUDE.md`.
