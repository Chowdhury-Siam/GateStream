"""Conservative title matching against AniList, optional MAL and OMDb."""
import asyncio
import logging
import re
import time
import unicodedata
from collections import OrderedDict
from difflib import SequenceMatcher

import aiohttp
from bot.config import Metadata

logger = logging.getLogger('bot')


def title_query(name):
    text = re.sub(r'\.[a-z0-9]{2,4}$', '', name.strip(), flags=re.I)
    text = re.sub(r'\[[^\]]*\]|@[\w]+|https?://\S+', ' ', text)
    text = text.replace('_', ' ').replace('.', ' ')
    year = re.search(r'\b(19\d{2}|20\d{2})\b', text)
    release_year = int(year[1]) if year else None
    text = re.split(r'\b(?:S\d{1,2}(?:E\d{1,3})?|E\d{1,3}|season\s*\d+|episode\s*\d+|\d{3,4}p|2160p|4k|bluray|blu-ray|web-?dl|webrip|hdtv|x26[45]|h26[45]|hevc|av1|dual\s*audio|multi\s*audio)\b', text, maxsplit=1, flags=re.I)[0]
    text = re.sub(r'\b(?:19\d{2}|20\d{2})\b', ' ', text)
    text = re.sub(r'\s+-\s*\d{1,4}\b.*$', '', text)
    text = re.sub(r'[(){}]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip(' -–—|:'), release_year


def normalized(text):
    text = unicodedata.normalize('NFKD', text or '').casefold()
    return ' '.join(re.findall(r'\w+', ''.join(c for c in text if not unicodedata.combining(c))))


def match_score(query, titles, year=None, candidate_year=None):
    query = normalized(query)
    if len(query) < 2:
        return 0
    score = max((SequenceMatcher(None, query, normalized(title)).ratio() for title in titles if title), default=0)
    if year and candidate_year and abs(year - int(candidate_year)) > 1:
        score -= .3
    return score


class Classifier:
    def __init__(self, session):
        self.session = session
        self.cache = OrderedDict()
        self.next_request = {}
        self.unavailable = set()

    async def _json(self, source, method, url, **kwargs):
        now = time.monotonic()
        delay = self.next_request.get(source, 0) - now
        if delay > 10:  # Respect provider cooldown; retry the title on a later scan.
            return {}
        if delay > 0:
            await asyncio.sleep(delay)
        self.next_request[source] = time.monotonic() + (2.1 if source == 'AniList' else .5)
        try:
            async with self.session.request(method, url, timeout=aiohttp.ClientTimeout(total=12), **kwargs) as response:
                if response.status == 429:
                    try: wait = float(response.headers.get('Retry-After', 60))
                    except ValueError: wait = 60
                    self.next_request[source] = time.monotonic() + min(900, max(30, wait))
                if response.status != 200:
                    self.unavailable.add(source)
                    logger.warning('Metadata %s returned HTTP %s.', source, response.status)
                    return {}
                data = await response.content.read(262145)
                if len(data) > 262144:
                    return {}
                import json
                result = json.loads(data)
                return result if isinstance(result, dict) else {}
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            logger.warning('Metadata %s unavailable (%s).', source, type(error).__name__)
            self.unavailable.add(source)
            return {}

    async def classify(self, name):
        title, year = title_query(name)
        key = (normalized(title), year)
        if key in self.cache:
            self.cache.move_to_end(key)
            return dict(self.cache[key])
        result = dict(category='direct_files', title=title or name, status='unmatched', source='')
        if len(title) < 2:
            return result
        candidates = []
        query = '''query ($search: String!) { Page(perPage: 5) { media(search: $search, type: ANIME) {
          id idMal title { romaji english native } synonyms format startDate { year } siteUrl
        } } }'''
        data = await self._json('AniList', 'POST', 'https://graphql.anilist.co', json={'query': query, 'variables': {'search': title}})
        for item in ((data.get('data') or {}).get('Page') or {}).get('media', []) or []:
            titles = list((item.get('title') or {}).values()) + (item.get('synonyms') or [])
            score = match_score(title, titles, year, (item.get('startDate') or {}).get('year'))
            candidates.append(dict(category='anime', title=next((t for t in titles if t), title), source='AniList',
                score=score, anilist_id=item.get('id'), mal_id=item.get('idMal'), year=(item.get('startDate') or {}).get('year')))
        if Metadata.MAL_CLIENT_ID and not any(c['score'] >= .92 for c in candidates):
            data = await self._json('MyAnimeList', 'GET', 'https://api.myanimelist.net/v2/anime',
                headers={'X-MAL-CLIENT-ID': Metadata.MAL_CLIENT_ID}, params={'q': title[:64], 'limit': 5, 'fields': 'alternative_titles,start_date,media_type'})
            for row in data.get('data', []):
                item = row.get('node') or {}
                alt = item.get('alternative_titles') or {}
                titles = [item.get('title'), alt.get('en'), alt.get('ja')] + (alt.get('synonyms') or [])
                candidate_year = (item.get('start_date') or '')[:4]
                score = match_score(title, titles, year, int(candidate_year) if candidate_year.isdigit() else None)
                candidates.append(dict(category='anime',title=item.get('title', title),source='MyAnimeList',score=score,mal_id=item.get('id')))
        if Metadata.OMDB_API_KEY:
            params = {'apikey': Metadata.OMDB_API_KEY, 't': title, 'plot': 'short'}
            if year: params['y'] = str(year)
            data = await self._json('OMDb', 'GET', 'https://www.omdbapi.com/', params=params)
            if data.get('Response') == 'True' and data.get('Type') in ('movie', 'series', 'episode'):
                candidate_year = str(data.get('Year', ''))[:4]
                score = match_score(title, [data.get('Title')], year, int(candidate_year) if candidate_year.isdigit() else None)
                # Exact anime and live-action names can collide. A year-specific match wins;
                # otherwise a cross-category tie remains unmatched instead of guessing.
                candidates.append(dict(category='movies' if data['Type'] == 'movie' else 'webseries',
                    title=data.get('Title', title),source='OMDb',score=score,imdb_id=data.get('imdbID'),
                    ratings=data.get('Ratings', []), year=candidate_year))
        candidates.sort(key=lambda c: c['score'], reverse=True)
        if candidates and candidates[0]['score'] >= .9:
            best = candidates[0]
            conflicting = any(c['category'] != best['category'] and c['score'] >= best['score'] - .05 for c in candidates[1:])
            if not conflicting:
                result = {**best, 'status': 'matched'}
        if len(self.cache) >= 512:
            self.cache.popitem(last=False)
        self.cache[key] = dict(result)
        return result
