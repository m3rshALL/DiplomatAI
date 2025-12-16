from __future__ import annotations

import time
import uuid
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.core.logging import get_logger, request_id_var, user_id_var


log = get_logger(__name__)


class RequestContextMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        rid = str(uuid.uuid4())
        request_id_var.set(rid)

        uid: int | None = None
        if isinstance(event, Message) and event.from_user:
            uid = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            uid = event.from_user.id
        user_id_var.set(uid)

        start = time.perf_counter()
        try:
            return await handler(event, data)
        finally:
            dur_ms = int((time.perf_counter() - start) * 1000)
            log.info("update_processed", extra={"extra": {"duration_ms": dur_ms}})


