from __future__ import annotations


class ClipyError(Exception):
    status_code = 400
    code = "clipy_error"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class NotFound(ClipyError):
    status_code = 404
    code = "not_found"


class Conflict(ClipyError):
    status_code = 409
    code = "conflict"


class PermanentError(ClipyError):
    """Errore non ritentabile (es. token invalido, formato non supportato)."""
    code = "permanent_error"


class TemporaryError(ClipyError):
    """Errore ritentabile con backoff (rete, 5xx, rate limit)."""
    code = "temporary_error"
    status_code = 503
