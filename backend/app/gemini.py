"""Google Gemini client for the coach (SPEC §7): settings from backend/.env and one HTTP call.

REST API, no SDK: POST {API_ROOT}/{model}:generateContent with the key in the x-goog-api-key header
(never in the URL, so it can't end up in a log or an error text). Every failure is turned into a
GeminiError carrying a clean French message and an HTTP status: no stack trace, no key, no raw
upstream body ever reaches the phone.
"""

import logging
import os
import re
from pathlib import Path

import httpx
from dotenv import dotenv_values

logger = logging.getLogger("coach")

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
DEFAULT_MODEL = "gemini-3.6-flash"  # current stable Flash (GA July 2026); override with GEMINI_MODEL
API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
CONNECT_TIMEOUT_S = 5
REQUEST_TIMEOUT_S = 30  # per HTTP call; the whole chat turn has its own budget (coach.py)

NOT_CONFIGURED = "Coach non configuré : ajoute GEMINI_API_KEY dans backend/.env"

# Replaced in tests with an httpx.MockTransport. None = the real network.
transport: httpx.BaseTransport | None = None


class GeminiError(Exception):
    """A failure the user can be told about: `status` is the HTTP status we answer with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def read_settings() -> tuple[str | None, str]:
    """(api key or None, model name). The file is read at every call: no restart after editing it."""
    values = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}
    key = (values.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY") or "").strip()
    model = (values.get("GEMINI_MODEL") or os.environ.get("GEMINI_MODEL") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,60}", model):  # it goes into the URL path
        model = DEFAULT_MODEL
    return (key or None), model


def generate(payload: dict, api_key: str, model: str, timeout_s: float = REQUEST_TIMEOUT_S) -> dict:
    """Call generateContent and return the parsed JSON answer, or raise GeminiError.

    `timeout_s` lets the caller shrink the wait so a whole chat turn stays inside its time budget.
    """
    url = f"{API_ROOT}/{model}:generateContent"
    timeout_s = max(0.5, min(REQUEST_TIMEOUT_S, timeout_s))
    timeout = httpx.Timeout(timeout_s, connect=min(CONNECT_TIMEOUT_S, timeout_s))
    try:
        with httpx.Client(timeout=timeout, transport=transport) as http:
            response = http.post(url, json=payload, headers={"x-goog-api-key": api_key})
    except httpx.TimeoutException:
        raise GeminiError(504, "Le coach met trop de temps à répondre. Réessaie dans un instant.") from None
    except httpx.HTTPError:
        raise GeminiError(502, "Impossible de joindre Gemini (le PC est-il connecté à internet ?).") from None

    if response.status_code != 200:
        raise map_error(response)
    try:
        data = response.json()
    except ValueError:
        raise GeminiError(502, "Réponse illisible de Gemini. Réessaie.") from None
    if not isinstance(data, dict):
        raise GeminiError(502, "Réponse illisible de Gemini. Réessaie.")
    return data


def map_error(response: httpx.Response) -> GeminiError:
    status = response.status_code
    detail = ""
    try:
        detail = str(response.json().get("error", {}).get("message", ""))
    except (ValueError, AttributeError):
        pass
    logger.warning("Gemini answered HTTP %s", status)  # status only: never the body or the key
    if status in (401, 403) or (status == 400 and "api key" in detail.lower()):
        return GeminiError(502, "Gemini refuse la clé : vérifie GEMINI_API_KEY dans backend/.env.")
    if status == 429:
        return GeminiError(429, "Le quota Gemini est atteint. Réessaie dans quelques minutes.")
    if status == 404:
        return GeminiError(502, "Modèle Gemini introuvable : vérifie GEMINI_MODEL dans backend/.env.")
    if status >= 500:
        return GeminiError(502, "Gemini est indisponible pour le moment. Réessaie dans un instant.")
    return GeminiError(502, "Gemini a refusé la demande. Reformule ou réessaie.")
