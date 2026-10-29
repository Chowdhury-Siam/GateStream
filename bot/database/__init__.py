from motor.motor_asyncio import AsyncIOMotorClient
from bot.config import Database
from logging import getLogger

logger = getLogger('bot')

class MongoDatabase:
    def __init__(self):
        self._client = None
        self.db = None
        self.movies = None
        self.anime = None
        self.webseries = None
        self.direct_files = None
        self._omni_client = None
        self.omni_db = None

    def connect(self):
        try:
            self._client = AsyncIOMotorClient(
                Database.DATABASE_URL,
                maxPoolSize=10,  # Keep connection pool compact for low RAM
                minPoolSize=1,
                serverSelectionTimeoutMS=10000
            )
            self.db = self._client[Database.DATABASE_NAME]
            self.movies = self.db['movies']
            self.anime = self.db['anime']
            self.webseries = self.db['webseries']
            self.direct_files = self.db['direct_files']
            logger.info("Connected to MongoDB database: %s", Database.DATABASE_NAME)
        except Exception as e:
            logger.error("Failed to connect to MongoDB: %s", e)
            raise e

        # Initialize upstream OmniArchiverDB connection (authoritative catalog source)
        try:
            omni_uri = getattr(Database, 'OMNI_MONGO_URI', None)
            if omni_uri:
                self._omni_client = AsyncIOMotorClient(
                    omni_uri,
                    maxPoolSize=5,
                    minPoolSize=1,
                    serverSelectionTimeoutMS=7000
                )
                self.omni_db = self._omni_client['OmniArchiverDB']
            elif self._client is not None:
                self.omni_db = self._client['OmniArchiverDB']
            logger.info("Upstream catalog connection initialized.")
        except Exception as e:
            logger.warning("Could not initialize upstream catalog connection: %s", e)

    async def create_indexes(self):
        """Ensures high-speed O(1) indexes to eliminate query latency and CPU spikes."""
        try:
            if self.movies is not None:
                await self.movies.create_index([("channel_id", 1), ("message_id", 1)])
            if self.anime is not None:
                await self.anime.create_index("episodes.code")
                await self.anime.create_index([("channel_id", 1), ("start_id", 1), ("end_id", 1)])
            if self.webseries is not None:
                await self.webseries.create_index("episodes.code")
                await self.webseries.create_index([("channel_id", 1), ("start_id", 1), ("end_id", 1)])
            for collection in (self.movies, self.anime, self.webseries, self.direct_files):
                if collection is not None:
                    await collection.create_index([('channel_id', 1), ('message_id', 1), ('catalog_section', 1)])
            if self.db is not None:
                await self.db['download_tickets'].create_index('expires_at', expireAfterSeconds=0)
                await self.db['index_channels'].create_index('latest_seen')
            logger.info("MongoDB database indexes ensured.")
        except Exception as e:
            logger.warning("Error creating MongoDB indexes: %s", e)

    def get_collection(self, category: str):
        cat = category.lower()
        if cat in ('movie', 'movies'):
            return self.movies
        elif cat in ('anime', 'animes'):
            return self.anime
        elif cat in ('series', 'webseries', 'tv'):
            return self.webseries
        else:
            return self.direct_files

db = MongoDatabase()
