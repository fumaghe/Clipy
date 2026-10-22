from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import DB
from app.services import diagnostics, events

router = APIRouter(prefix="/api/v1/integrations/diagnostics", tags=["diagnostics"])


class TestBody(BaseModel):
    confirm: bool = False


@router.get("")
def list_diagnostics(db: Session = DB) -> dict:
    """Stato per servizio (nessuna chiamata esterna). Gli stati derivano da configurazione + ultimo test valido."""
    return {"items": diagnostics.all_entries(db), "states": [diagnostics.NOT_CONFIGURED, diagnostics.CONFIGURED, diagnostics.CONNECTED, diagnostics.TEST_FAILED, diagnostics.NEEDS_LIVE]}


@router.post("/{service}/test")
def test_service(service: str, body: TestBody | None = None, db: Session = DB) -> dict:
    """Esegue il test del servizio. Non pubblica, non altera la coda, non modifica video. Storage/LLM/Whisper richiedono confirm=true."""
    res = diagnostics.run_test(db, service, confirm=bool(body and body.confirm))
    events.emit(db, "diagnostics", "test", f"Test {res['label']}: {res['state']}", severity="info" if res["state"] != diagnostics.TEST_FAILED else "warning")
    return res
