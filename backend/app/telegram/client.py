"""A small Telegram Bot API client.

The bot token is part of every request URL, so two things are handled here
that are easy to get wrong:

- httpx logs each request URL at INFO, which would write the token to the
  logs. Its loggers are raised to WARNING when this module is imported.
- A failed request must not surface the URL in an exception message, so
  transport errors are re-raised as `TelegramError` with the URL stripped.
"""
import logging
from typing import Any, Optional

import httpx

for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)

API_BASE = "https://api.telegram.org"


class TelegramError(Exception):
    """The Bot API refused a call, or could not be reached."""

    def __init__(self, message: str, *, code: Optional[int] = None):
        super().__init__(message)
        self.code = code

    @property
    def is_auth_error(self) -> bool:
        return self.code in (401, 404)


class TelegramClient:
    def __init__(self, token: str, *, timeout: float = 15.0):
        self._token = token
        self._timeout = timeout

    async def _call(
        self, method: str, payload: Optional[dict] = None, *, timeout: Optional[float] = None
    ) -> Any:
        url = f"{API_BASE}/bot{self._token}/{method}"
        try:
            async with httpx.AsyncClient(timeout=timeout or self._timeout) as client:
                resp = await client.post(url, json=payload or {})
        except httpx.HTTPError as exc:
            # type(exc).__name__ only: str(exc) can carry the request URL.
            raise TelegramError(f"Telegram request failed ({type(exc).__name__})") from None
        try:
            data = resp.json()
        except ValueError:
            raise TelegramError(
                f"Telegram answered HTTP {resp.status_code}", code=resp.status_code
            ) from None
        if not data.get("ok"):
            raise TelegramError(
                str(data.get("description") or "Telegram refused the request"),
                code=data.get("error_code") or resp.status_code,
            )
        return data.get("result")

    async def get_me(self) -> dict:
        return await self._call("getMe")

    async def get_updates(self, offset: Optional[int] = None, *, timeout: int = 25) -> list[dict]:
        payload: dict = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        # The long poll holds the connection open for `timeout` seconds.
        return await self._call("getUpdates", payload, timeout=timeout + 10)

    async def send_message(
        self, chat_id: int, text: str, *, reply_markup: Optional[dict] = None
    ) -> dict:
        payload: dict = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self._call("sendMessage", payload)

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: Optional[dict] = None,
    ) -> dict:
        payload: dict = {"chat_id": chat_id, "message_id": message_id, "text": text}
        # An empty keyboard removes the buttons; omitting it would keep them.
        payload["reply_markup"] = reply_markup if reply_markup is not None else {"inline_keyboard": []}
        return await self._call("editMessageText", payload)

    async def answer_callback_query(self, callback_id: str, text: Optional[str] = None) -> None:
        payload: dict = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        await self._call("answerCallbackQuery", payload)
