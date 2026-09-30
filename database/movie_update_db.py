"""
Database functions for movie update notification status tracking.
"""
from config import MOVIE_UPDATE_NOTIFICATION
from database.client import db
import logging

logger = logging.getLogger(__name__)

class MovieUpdateDB:
    def __init__(self):
        self.movie_updates = db.movie_updates

    async def ensure_indexes(self):
        """Create necessary indexes for movie updates collection."""
        try:
            # NOTE: MongoDB always creates a unique index on _id automatically.
            # Trying to create it again with unique=True raised
            # InvalidIndexSpecificationOption and aborted the whole ensure step.
            await self.movie_updates.create_index([("message_id", 1)])
            logger.info("Movie update indexes ensured")
        except Exception as e:
            logger.error(f"Error creating movie update indexes: {e}")

    async def movie_update_status(self, bot_id: int) -> bool:
        """
        Check if movie update notification is enabled for this bot.
        Falls back to config default if not set in database.
        """
        try:
            from database.settings_db import get_settings
            settings = await get_settings()
            # Check if there's a movie_update setting in settings
            movie_update_enabled = settings.get("movie_update_notification", MOVIE_UPDATE_NOTIFICATION)
            return movie_update_enabled
        except Exception as e:
            logger.error(f"Error checking movie update status: {e}")
            return MOVIE_UPDATE_NOTIFICATION

    async def update_movie_update_status(self, bot_id: int, enable: bool):
        """
        Enable or disable movie update notification for this bot.
        """
        try:
            from database.settings_db import update_settings
            await update_settings({"movie_update_notification": enable})
            logger.info(f"Movie update notification {'enabled' if enable else 'disabled'} for bot {bot_id}")
        except Exception as e:
            logger.error(f"Error updating movie update status: {e}")

    async def get_movie_doc(self, base_name: str):
        """Get a movie document by base name."""
        try:
            return await self.movie_updates.find_one({"_id": base_name})
        except Exception as e:
            logger.error(f"Error getting movie doc: {e}")
            return None

    async def insert_movie_doc(self, movie_doc: dict):
        """Insert a new movie document."""
        try:
            await self.movie_updates.insert_one(movie_doc)
            return True
        except Exception as e:
            logger.error(f"Error inserting movie doc: {e}")
            return False

    async def update_movie_doc(self, base_name: str, update: dict):
        """Update a movie document."""
        try:
            result = await self.movie_updates.update_one({"_id": base_name}, update)
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error updating movie doc: {e}")
            return False

    async def add_file_to_movie(self, base_name: str, file_data: dict):
        """Add a file to an existing movie document."""
        try:
            result = await self.movie_updates.update_one(
                {"_id": base_name},
                {"$push": {"files": file_data}}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error adding file to movie: {e}")
            return False

    async def update_message_id(self, base_name: str, message_id: int, is_photo: bool = False):
        """Update the message ID for a movie document."""
        try:
            await self.movie_updates.update_one(
                {"_id": base_name},
                {"$set": {"message_id": message_id, "is_photo": is_photo}}
            )
        except Exception as e:
            logger.error(f"Error updating message ID: {e}")

# Global instance
movie_update_db = MovieUpdateDB()