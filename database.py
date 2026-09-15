"""Database helpers for Session-gen bot using MongoDB."""
import datetime
import logging
from typing import List

import certifi
from pymongo import AsyncMongoClient

from config import MONGO_URI

log = logging.getLogger(__name__)

mongo_client = None
db = None
users_col = None

if MONGO_URI:
    try:
        mongo_client = AsyncMongoClient(MONGO_URI, tlsCAFile=certifi.where())
        db = mongo_client["session"]
        users_col = db["users"]
        log.info("MongoDB client connected to session database.")
    except Exception as e:
        log.error("Failed to initialize MongoDB client: %s", e)


async def save_user(user_id: int):
    """Save or update a user ID in the database."""
    if not user_id or users_col is None:
        return
    try:
        await users_col.update_one(
            {"user_id": int(user_id)},
            {
                "$set": {
                    "user_id": int(user_id),
                    "last_seen": datetime.datetime.now(datetime.timezone.utc),
                }
            },
            upsert=True,
        )
    except Exception as e:
        log.warning("Could not save user %s: %s", user_id, e)


async def get_all_users() -> List[int]:
    """Retrieve all unique user IDs from the database."""
    if users_col is None:
        return []
    try:
        raw_ids = await users_col.distinct("user_id")
        user_ids = []
        for uid in raw_ids:
            if uid is not None:
                try:
                    user_ids.append(int(uid))
                except (ValueError, TypeError):
                    pass
        return list(dict.fromkeys(user_ids))
    except Exception as e:
        log.error("Failed to fetch user IDs from MongoDB: %s", e)
        return []


async def get_users_count() -> int:
    """Return the total number of registered users."""
    if users_col is None:
        return 0
    try:
        return await users_col.count_documents({})
    except Exception as e:
        log.error("Failed to count users in MongoDB: %s", e)
        return 0
