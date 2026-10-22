"""Gestione della protezione dei segreti nel DB locale.

    python -m app.tools.secrets_cli status      # quanti segreti sono cifrati / in chiaro / non leggibili (mai i valori)
    python -m app.tools.secrets_cli protect     # cifra con DPAPI i segreti in chiaro (Windows; valori invariati)
    python -m app.tools.secrets_cli unprotect   # decifra (stesso utente Windows): per migrare a un altro host o secret manager
Fare un backup di data/clipy.db prima di `unprotect`: dopo l'operazione i segreti sono in chiaro nel file."""
from __future__ import annotations

import sys

from app.core import secretbox
from app.db.base import run_migrations, session_scope
from app.db.models import Secret
from app.services import settings_service as ss


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    run_migrations()
    with session_scope() as db:
        if cmd == "status":
            rows = db.query(Secret).all()
            enc = sum(1 for r in rows if secretbox.is_protected(r.value))
            bad = [r.name for r in rows if ss.secret_unreadable(db, r.name)]
            print(f"modalità protezione: {secretbox.mode()} · segreti: {len(rows)} (cifrati {enc}, in chiaro {len(rows) - enc}) · non leggibili: {bad or 'nessuno'}")
        elif cmd == "protect":
            print(f"cifrati {ss.protect_all(db)} segreti" if secretbox.mode() == "dpapi" else "protezione non attiva su questo sistema (serve Windows o CLIPY_SECRET_PROTECTION=dpapi)")
        elif cmd == "unprotect":
            print(f"decifrati {ss.unprotect_all(db)} segreti")
        else:
            print(__doc__)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
