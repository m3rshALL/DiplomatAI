from __future__ import annotations

import asyncio
import socket
import sys
import uuid
from urllib.parse import urlparse


from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import MenuButtonWebApp, WebAppInfo
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app.bot.handlers import router as bot_router
from app.bot.middlewares import RequestContextMiddleware
from app.core.config import get_settings
from app.core.logging import configure_logging, request_id_var
from app.services.openai_client import OpenAIClient
from app.services.perplexity_client import PerplexityClient
from app.services.report_pipeline import ReportPipeline
from app.storage.db import create_engine_and_sessionmaker, init_db
from app.storage.redis import create_redis
from app.storage.repo import ReportRepo


async def main() -> None:
    settings = get_settings()
    configure_logging()

    # request_id for startup logs
    request_id_var.set(str(uuid.uuid4()))

    engine, session_factory = create_engine_and_sessionmaker(settings.database_url)
    await init_db(engine)

    redis = create_redis(settings.redis_url)

    repo = ReportRepo(session_factory=session_factory)
    perplexity = PerplexityClient(
        api_key=settings.perplexity_api_key,
        endpoint=settings.perplexity_endpoint,
        model=settings.perplexity_model,
        timeout_seconds=settings.perplexity_timeout_seconds,
        retry_max_attempts=settings.retry_max_attempts,
        retry_base_delay_seconds=settings.retry_base_delay_seconds,
        retry_max_delay_seconds=settings.retry_max_delay_seconds,
        use_response_format=settings.perplexity_use_response_format,
    )
    openai = OpenAIClient(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        timeout_seconds=settings.openai_timeout_seconds,
        retry_max_attempts=settings.retry_max_attempts,
        retry_base_delay_seconds=settings.retry_base_delay_seconds,
        retry_max_delay_seconds=settings.retry_max_delay_seconds,
    )
    pipeline = ReportPipeline(
        settings=settings,
        redis=redis,
        repo=repo,
        perplexity=perplexity,
        openai=openai,
    )

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # Telegram Mini App требует HTTPS, а trycloudflare URL "протухает" после перезапуска tunnel.
    # Чтобы не закреплять мёртвую ссылку в меню бота, ставим кнопку только если домен резолвится.
    if settings.webapp_url:
        host = urlparse(settings.webapp_url).hostname or ""
        should_set_menu = True
        if host.endswith("trycloudflare.com"):
            loop = asyncio.get_running_loop()
            try:
                await asyncio.wait_for(loop.run_in_executor(None, socket.getaddrinfo, host, None), timeout=0.8)
            except (asyncio.TimeoutError, OSError):
                should_set_menu = False
        if should_set_menu:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="DiplomatAI", web_app=WebAppInfo(url=settings.webapp_url))
            )

    dp = Dispatcher()
    dp.message.middleware(RequestContextMiddleware())
    dp.callback_query.middleware(RequestContextMiddleware())

    dp["pipeline"] = pipeline
    dp.include_router(bot_router)
    
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
        asyncio.run(main())
    except ValidationError as exc:
        # Конфиг/ENV ошибки должны быть понятными и без большого traceback.
        msg = "Ошибка конфигурации."
        errors = exc.errors()
        if errors:
            raw_msg = errors[0].get("msg")
            if isinstance(raw_msg, str) and raw_msg:
                msg = raw_msg.removeprefix("Value error, ").strip()
        print(msg, file=sys.stderr)
        raise SystemExit(1) from None
    except (OperationalError, OSError) as exc:
        # Подключение к БД/Redis при локальном запуске часто падает, если сервисы не подняты.
        # Делаем понятное сообщение вместо огромного traceback.
        msg = (
            "Не удалось подключиться к инфраструктуре (Postgres/Redis).\n"
            "Проверьте, что сервисы запущены и доступны.\n"
            "Если вы запускаете локально, выполните: docker-compose up -d postgres redis\n"
            "Если вы запускаете в Docker, используйте: docker-compose up --build\n"
        )
        print(msg, file=sys.stderr)
        print(f"Технические детали: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except ValueError as exc:
        # Конфиг/ENV ошибки должны быть понятными и без большого traceback.
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None


