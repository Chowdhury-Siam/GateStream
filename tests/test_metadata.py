import os
import unittest
from unittest.mock import AsyncMock, patch

with patch.dict(os.environ, {'TELEGRAM_API_ID':'12345','TELEGRAM_API_HASH':'test','TELEGRAM_BOT_TOKEN':'12345:test','OWNER_ID':'12345','DATABASE_URL':'mongodb://localhost:27017'}, clear=True):
    from bot.modules.metadata import Classifier, Metadata, title_query


def anime(title='Cowboy Bebop', year=1998):
    return {'data':{'Page':{'media':[dict(id=1,idMal=1,title={'english':title,'romaji':title},synonyms=[],startDate={'year':year})]}}}


class MetadataTests(unittest.IsolatedAsyncioTestCase):
    def test_release_names_are_cleaned_and_year_retained(self):
        self.assertEqual(title_query('[Group] Cowboy.Bebop.1998.S01E02.1080p.WEB-DL.mkv'), ('Cowboy Bebop',1998))
        self.assertEqual(title_query('Frieren - 03 [1080p].mkv'), ('Frieren',None))
        self.assertEqual(title_query('The.Matrix.1999.1080p.BluRay.mp4'), ('The Matrix',1999))

    async def test_anime_uses_authoritative_match_and_reuses_episode_cache(self):
        classifier=Classifier(None)
        classifier._json=AsyncMock(return_value=anime())
        with patch.object(Metadata,'OMDB_API_KEY',''),patch.object(Metadata,'MAL_CLIENT_ID',''):
            result=await classifier.classify('Cowboy Bebop S01E01.mkv')
            self.assertEqual(result['category'],'anime')
            self.assertEqual(result['mal_id'],1)
            self.assertEqual(result['source'],'AniList')
            await classifier.classify('Cowboy Bebop S01E02.mkv')
            self.assertEqual(classifier._json.await_count,1)

    async def test_omdb_movie_series_and_rotten_tomatoes_metadata(self):
        classifier=Classifier(None)
        classifier._json=AsyncMock(side_effect=[{},dict(Response='True',Title='The Matrix',Year='1999',Type='movie',imdbID='tt0133093',Ratings=[{'Source':'Rotten Tomatoes','Value':'83%'}])])
        with patch.object(Metadata,'OMDB_API_KEY','test-key'),patch.object(Metadata,'MAL_CLIENT_ID',''):
            result=await classifier.classify('The.Matrix.1999.1080p.mp4')
        self.assertEqual(result['category'],'movies')
        self.assertEqual(result['imdb_id'],'tt0133093')
        self.assertEqual(result['ratings'][0]['Source'],'Rotten Tomatoes')
        classifier._json=AsyncMock(side_effect=[{},dict(Response='True',Title='Dark',Year='2017',Type='series')])
        with patch.object(Metadata,'OMDB_API_KEY','key'),patch.object(Metadata,'MAL_CLIENT_ID',''):
            self.assertEqual((await classifier.classify('Dark.2017.S01E01.mkv'))['category'],'webseries')

    async def test_ambiguous_or_unrelated_matches_stay_unclassified(self):
        classifier=Classifier(None)
        classifier._json=AsyncMock(side_effect=[anime('Death Note',2006),dict(Response='True',Title='Death Note',Year='2017',Type='movie')])
        with patch.object(Metadata,'OMDB_API_KEY','key'),patch.object(Metadata,'MAL_CLIENT_ID',''):
            self.assertEqual((await classifier.classify('Death Note.mkv'))['status'],'unmatched')
        classifier._json=AsyncMock(return_value=anime('Unrelated Title',1990))
        with patch.object(Metadata,'OMDB_API_KEY',''),patch.object(Metadata,'MAL_CLIENT_ID',''):
            self.assertEqual((await classifier.classify('Some Completely Other Film.mp4'))['category'],'direct_files')

    async def test_release_year_resolves_anime_live_action_collision(self):
        classifier=Classifier(None)
        classifier._json=AsyncMock(side_effect=[anime('Death Note',2006),dict(Response='True',Title='Death Note',Year='2017',Type='movie')])
        with patch.object(Metadata,'OMDB_API_KEY','key'),patch.object(Metadata,'MAL_CLIENT_ID',''):
            self.assertEqual((await classifier.classify('Death.Note.2017.1080p.mp4'))['category'],'movies')

    async def test_mal_fallback_uses_own_client_id(self):
        classifier=Classifier(None)
        classifier._json=AsyncMock(side_effect=[{}, {'data':[{'node':{'id':1,'title':'Cowboy Bebop','start_date':'1998-04-03'}}]}])
        with patch.object(Metadata,'OMDB_API_KEY',''),patch.object(Metadata,'MAL_CLIENT_ID','owner-client-id'):
            result=await classifier.classify('Cowboy Bebop.mkv')
        self.assertEqual(result['source'],'MyAnimeList')
        self.assertEqual(classifier._json.call_args.kwargs['headers']['X-MAL-CLIENT-ID'],'owner-client-id')
