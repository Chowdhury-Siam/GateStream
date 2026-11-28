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

    def connect(self):
        try:
            self._client = AsyncIOMotorClient(
                Database.DATABASE_URL,
                maxPoolSize=10,  # Keep connection pool compact for low RAM
                minPoolSize=0,
                maxIdleTimeMS=60000,
                serverSelectionTimeoutMS=10000
            )
            self.db = self._client[Database.DATABASE_NAME]
            self.movies = self.db['movies']
            self.anime = self.db['anime']
            self.webseries = self.db['webseries']
            self.direct_files = self.db['direct_files']
            logger.info("MongoDB client configured.")
        except Exception as e:
            logger.error("MongoDB setup failed (%s).", type(e).__name__)
            raise e

    @property
    def omni_db(self):
        """Open the optional legacy catalog only when an integration uses it."""
        if self._client is None:
            return None
        if not Database.OMNI_MONGO_URI:
            return self._client['OmniArchiverDB']
        if self._omni_client is None:
            self._omni_client = AsyncIOMotorClient(
                Database.OMNI_MONGO_URI,
                maxPoolSize=5,
                minPoolSize=0,
                maxIdleTimeMS=60000,
                serverSelectionTimeoutMS=7000,
            )
        return self._omni_client['OmniArchiverDB']

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
                await self.db['direct_files'].create_index('source_key', sparse=True)
            logger.info("MongoDB database indexes ensured.")
        except Exception as e:
            logger.warning("MongoDB index setup failed (%s).", type(e).__name__)

    def close(self):
        for client in (self._omni_client, self._client):
            if client is not None:
                try:
                    client.close()
                except Exception as error:
                    logger.warning('MongoDB close failed (%s).', type(error).__name__)
        self._omni_client = self._client = None
        self.db = None
        self.movies = self.anime = self.webseries = self.direct_files = None

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
