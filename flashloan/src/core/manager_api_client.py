"""HTTP client for the local Rust manager boundary.

The web compatibility layer uses this module during the M9 migration. It never
queries manager business tables directly. Reads and writes are delegated to
manager_api and its owning manager crates.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


DEFAULT_MANAGER_API_BASE_URL = "http://127.0.0.1:8091"
DEFAULT_MANAGER_API_TIMEOUT_SECONDS = 3.0
VALID_UI_DATA_SOURCES = frozenset({"legacy", "manager"})


class ManagerApiClientError(RuntimeError):
    """Structured manager API transport or response error."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int | None = None,
        response_payload: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code or "MANAGER_API_ERROR")
        self.message = str(message or self.code)
        self.status = int(status) if status is not None else None
        self.response_payload = dict(response_payload or {})


@dataclass(frozen=True)
class ManagerApiClient:
    base_url: str = DEFAULT_MANAGER_API_BASE_URL
    timeout_seconds: float = DEFAULT_MANAGER_API_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls) -> "ManagerApiClient":
        base_url = os.environ.get("MANAGER_API_BASE_URL", "").strip() or DEFAULT_MANAGER_API_BASE_URL
        raw_timeout = os.environ.get("MANAGER_API_TIMEOUT_SECONDS", "").strip()
        timeout_seconds = DEFAULT_MANAGER_API_TIMEOUT_SECONDS
        if raw_timeout:
            try:
                timeout_seconds = float(raw_timeout)
            except ValueError as exc:
                raise ManagerApiClientError(
                    "MANAGER_API_CONFIG_INVALID",
                    "MANAGER_API_TIMEOUT_SECONDS must be numeric",
                ) from exc
        if not 0.1 <= timeout_seconds <= 30.0:
            raise ManagerApiClientError(
                "MANAGER_API_CONFIG_INVALID",
                "MANAGER_API_TIMEOUT_SECONDS must be between 0.1 and 30 seconds",
            )
        return cls(base_url=base_url, timeout_seconds=timeout_seconds)

    def get_json(
        self,
        path: str,
        *,
        query: Mapping[str, object] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        url = self._url(path, query=query)
        headers = {
            "Accept": "application/json",
            "User-Agent": "flashloan-manager-web-compat/1",
        }
        if request_id:
            headers["X-Request-Id"] = str(request_id)
        request = Request(url, headers=headers, method="GET")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read()
                status = int(getattr(response, "status", 200))
        except HTTPError as exc:
            body = exc.read()
            payload = _decode_response_payload(body)
            error = payload.get("error") if isinstance(payload, dict) else None
            message = (
                str(error.get("message") or error.get("code") or "manager_api request failed")
                if isinstance(error, dict)
                else f"manager_api returned HTTP {exc.code}"
            )
            raise ManagerApiClientError(
                "MANAGER_API_HTTP_ERROR",
                message,
                status=int(exc.code),
                response_payload=payload if isinstance(payload, dict) else None,
            ) from exc
        except (TimeoutError, URLError, OSError) as exc:
            raise ManagerApiClientError(
                "MANAGER_API_UNAVAILABLE",
                str(exc)[:500] or "manager_api is unavailable",
            ) from exc
        payload = _decode_response_payload(body)
        if not isinstance(payload, dict):
            raise ManagerApiClientError(
                "MANAGER_API_INVALID_RESPONSE",
                "manager_api response must be a JSON object",
                status=status,
            )
        return payload

    def post_json(
        self,
        path: str,
        *,
        payload: Mapping[str, object],
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        url = self._url(path, query=None)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "flashloan-manager-web-compat/1",
        }
        if request_id:
            headers["X-Request-Id"] = str(request_id)
        if idempotency_key:
            headers["Idempotency-Key"] = str(idempotency_key)
        body = json.dumps(dict(payload), separators=(",", ":"), ensure_ascii=True).encode(
            "utf-8"
        )
        request = Request(url, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                response_body = response.read()
                status = int(getattr(response, "status", 200))
        except HTTPError as exc:
            response_body = exc.read()
            decoded = _decode_response_payload(response_body)
            error = decoded.get("error") if isinstance(decoded, dict) else None
            message = (
                str(error.get("message") or error.get("code") or "manager_api request failed")
                if isinstance(error, dict)
                else f"manager_api returned HTTP {exc.code}"
            )
            raise ManagerApiClientError(
                "MANAGER_API_HTTP_ERROR",
                message,
                status=int(exc.code),
                response_payload=decoded if isinstance(decoded, dict) else None,
            ) from exc
        except (TimeoutError, URLError, OSError) as exc:
            raise ManagerApiClientError(
                "MANAGER_API_UNAVAILABLE",
                str(exc)[:500] or "manager_api is unavailable",
            ) from exc
        decoded = _decode_response_payload(response_body)
        if not isinstance(decoded, dict):
            raise ManagerApiClientError(
                "MANAGER_API_INVALID_RESPONSE",
                "manager_api response must be a JSON object",
                status=status,
            )
        return decoded

    def _url(self, path: str, *, query: Mapping[str, object] | None) -> str:
        base = _validate_base_url(self.base_url)
        normalized_path = "/" + str(path or "").strip().lstrip("/")
        if normalized_path == "/":
            raise ManagerApiClientError(
                "MANAGER_API_PATH_INVALID",
                "manager_api path must not be empty",
            )
        query_pairs = [
            (str(key), str(value))
            for key, value in (query or {}).items()
            if value is not None and str(value).strip() != ""
        ]
        query_string = urlencode(query_pairs)
        return f"{base}{normalized_path}{'?' + query_string if query_string else ''}"


def manager_ui_data_source() -> str:
    value = os.environ.get("MANAGER_UI_DATA_SOURCE", "").strip().lower() or "legacy"
    if value not in VALID_UI_DATA_SOURCES:
        raise ManagerApiClientError(
            "MANAGER_UI_DATA_SOURCE_INVALID",
            "MANAGER_UI_DATA_SOURCE must be legacy or manager",
        )
    return value


def _validate_base_url(value: str) -> str:
    candidate = str(value or "").strip().rstrip("/")
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ManagerApiClientError(
            "MANAGER_API_CONFIG_INVALID",
            "MANAGER_API_BASE_URL must be an absolute http or https URL",
        )
    if parsed.username or parsed.password:
        raise ManagerApiClientError(
            "MANAGER_API_CONFIG_INVALID",
            "MANAGER_API_BASE_URL must not contain credentials",
        )
    return candidate


def _decode_response_payload(body: bytes) -> object:
    if not body:
        return {}
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManagerApiClientError(
            "MANAGER_API_INVALID_RESPONSE",
            "manager_api response is not valid UTF-8 JSON",
        ) from exc


__all__ = [
    "DEFAULT_MANAGER_API_BASE_URL",
    "DEFAULT_MANAGER_API_TIMEOUT_SECONDS",
    "ManagerApiClient",
    "ManagerApiClientError",
    "manager_ui_data_source",
]
