from . import SessionBackend, SessionDict
import redis.asyncio as aredis
from typing import override
from loguru import logger


class RedisSessionBackend(SessionBackend):
    """
    A Redis-based session backend.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        prefix: str = "session:",
    ):
        self.redis_url = redis_url
        self.prefix = prefix
        self.client: aredis.Redis = aredis.from_url(
            redis_url, encoding="utf-8", decode_responses=True
        )

    def _redis_key(self, session_id: str) -> str:
        return f"{self.prefix}{session_id}"

    @override
    async def save_session(self, session: SessionDict) -> None:
        redis_key = self._redis_key(session.session_id)
        await self.client.hset(
            redis_key, mapping=session.dict(exclude={"session_id"})
            ex=self.max_age
        )

    @override
    async def get_session(
        self, session_id: str, max_age: int
    ) -> SessionDict | None:
        session_data = await self.client.hgetall(
            self._redis_key(session_id)
        )
        if not session_data:
            return None
        session = SessionDict(session_id=session_id, **session_data)
        return session
    
    @override
    async def touch_session(self, session: SessionDict) -> None:
        await self.client.expire(
            self._redis_key(session.session_id), self.max_age
        )

    @override
    async def delete_session(self, session_id: str) -> None:
        await self.redis.delete(self._redis_key(session_id))
