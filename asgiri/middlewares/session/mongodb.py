from . import SessionBackend, SessionDict
import datetime
from pymongo import AsyncMongoClient

class MongoDBSessionBackend(SessionBackend):
    def __init__(self, mongo_uri: str, db_name: str = "asgiri", collection_name: str = "sessions"):
        self.client = AsyncMongoClient(mongo_uri)
        self.db = self.client[db_name]
        self.collection = self.db[collection_name]

    async def save_session(self, session: SessionDict) -> None:
        session.last_touched = datetime.datetime.now(tz=datetime.timezone.utc)
        await self.collection.update_one(
            {"session_id": session.session_id},
            {"$set": session},
            upsert=True
        )
    async def touch_session(self, session: SessionDict) -> None:
        session.last_touched = datetime.datetime.now(tz=datetime.timezone.utc)
        await self.collection.update_one(
            {"session_id": session.session_id},
            {"$set": {"last_touched": session.last_touched}}
        )

    async def delete_session(self, session: SessionDict) -> None:
        await self.collection.delete_one({"session_id": session.session_id})

    async def get_session(self, session_id: str) -> SessionDict | None:
        data = await self.collection.find_one({"session_id": session_id})
        if data:
            return SessionDict(**data)
        return None
