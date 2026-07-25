"""Scansione del filesystem a basso impatto.

L'identita di un file e la terna (percorso relativo, dimensione, mtime in nanosecondi),
letta con una sola stat per voce. Il contenuto non viene mai aperto ne letto: niente
hash, niente checksum, nessuna pressione sulla cache del disco.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Ogni quante voci si cede il controllo al loop quando non c'e limite di velocita.
YIELD_EVERY = 500


@dataclass(slots=True)
class ScannedFile:
    rel_path: str
    name: str
    size: int
    mtime_ns: int


def _walk(root: str) -> list[ScannedFile]:
    """Percorso ricorsivo con os.scandir, senza seguire i link simbolici.

    scandir riusa i dati della directory entry, quindi is_dir e is_file spesso non
    costano una stat aggiuntiva. La stat vera serve solo per size e mtime.
    """
    found: list[ScannedFile] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            info = entry.stat(follow_symlinks=False)
                            rel = os.path.relpath(entry.path, root)
                            found.append(
                                ScannedFile(
                                    rel_path=rel,
                                    name=entry.name,
                                    size=info.st_size,
                                    mtime_ns=info.st_mtime_ns,
                                )
                            )
                    except OSError as exc:
                        log.warning("Voce ignorata %s: %s", entry.path, exc)
        except OSError as exc:
            log.warning("Cartella non leggibile %s: %s", current, exc)
    return found


async def scan(root: str, files_per_sec: int = 0) -> list[ScannedFile]:
    """Scansiona `root` in un thread, con throttle opzionale.

    `files_per_sec` a zero significa nessun limite. Sopra zero il tempo totale viene
    diluito per non saturare il disco quando la cartella e su un supporto lento o
    condiviso con altri carichi.
    """
    if not os.path.isdir(root):
        raise FileNotFoundError(f"La cartella {root} non esiste dentro il container")

    files = await asyncio.to_thread(_walk, root)

    if files_per_sec > 0:
        # La camminata e gia finita: si distribuisce la pausa dovuta in blocchi, cosi
        # il resto dell'applicazione resta reattivo e il ritmo richiesto e rispettato
        # sul ciclo completo di scansione.
        total_delay = len(files) / files_per_sec
        step = 0.2
        waited = 0.0
        while waited < total_delay:
            await asyncio.sleep(min(step, total_delay - waited))
            waited += step
    else:
        await asyncio.sleep(0)

    return files
