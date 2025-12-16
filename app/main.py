from __future__ import annotations

import asyncio
import uuid

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode

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

    bot = Bot(token=settings.telegram_bot_token, parse_mode=ParseMode.HTML)
    dp = Dispatcher()
    dp.message.middleware(RequestContextMiddleware())
    dp.callback_query.middleware(RequestContextMiddleware())

    dp["pipeline"] = pipeline
    dp.include_router(bot_router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())


