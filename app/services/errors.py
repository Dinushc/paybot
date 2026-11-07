"""Domain errors translated to HTTP responses by the API layer."""
from __future__ import annotations

from typing import Any


class DomainError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}

    def as_dict(self) -> dict:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return {"error": payload}


class ValidationError(DomainError):
    def __init__(self, message: str, code: str = "validation_error", **kwargs) -> None:
        super().__init__(code, message, status_code=422, **kwargs)


class NotFoundError(DomainError):
    def __init__(self, message: str, code: str = "not_found") -> None:
        super().__init__(code, message, status_code=404)


class ConflictError(DomainError):
    def __init__(self, message: str, code: str = "conflict") -> None:
        super().__init__(code, message, status_code=409)
