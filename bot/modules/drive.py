"""Bounded public-URL downloads and Google Drive resumable uploads."""
import base64
import ipaddress
import mimetypes
import pickle
import re
import shutil
import socket
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import aiofiles
import aiohttp
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from yarl import URL


class DriveTransferError(Exception):
    """A safe, user-facing failure that contains no URLs or credentials."""


def _public_ip(value):
    ip = ipaddress.ip_address(value)
    mapped = getattr(ip, 'ipv4_mapped', None) or getattr(ip, 'sixtofour', None)
    return ip.is_global and not ip.is_multicast and (not mapped or _public_ip(str(mapped)))


def validate_url(value):
    try:
        url = urlsplit(value)
        if (any(c.isspace() or ord(c) < 32 for c in value) or
                url.scheme not in ('http', 'https') or not url.hostname or
                url.username is not None or url.password is not None or
                url.port not in (None, 80, 443, 8080)):
            raise ValueError()
        try:
            ipaddress.ip_address(url.hostname)
        except ValueError:
            pass  # Hostname IPs are checked by the connector's resolver below.
        else:
            if not _public_ip(url.hostname):
                raise ValueError()
    except ValueError:
        raise DriveTransferError('Use a public HTTP/HTTPS direct file URL on port 80, 443 or 8080.') from None


class PublicResolver(aiohttp.resolver.ThreadedResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        addresses = await super().resolve(host, port, family)
        if not addresses or any(not _public_ip(item['host']) for item in addresses):
            raise DriveTransferError('Downloads from private or local networks are blocked.')
        # aiohttp connects to these checked addresses, without resolving a second time.
        return addresses


def load_credentials(token_path, token_b64=''):
    try:
        if token_b64:
            raw = base64.b64decode(''.join(token_b64.split()), validate=True)
        else:
            with open(token_path, 'rb') as source:
                raw = source.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError()
        # Owner-provided credentials only. Never unpickle a Telegram attachment.
        credentials = pickle.loads(raw)
        if not isinstance(credentials, Credentials) or not (credentials.token or credentials.refresh_token):
            raise ValueError()
        if credentials.expired and not credentials.refresh_token:
            raise ValueError()
        return credentials
    except FileNotFoundError:
        raise DriveTransferError('Drive token is missing. Configure DRIVE_TOKEN_PATH or DRIVE_TOKEN_PICKLE_B64.') from None
    except Exception:
        raise DriveTransferError('Cannot load the Drive token. Use a trusted token.pickle containing google.oauth2.credentials.Credentials with Drive upload access.') from None


async def download_file(url, directory, max_bytes, progress=None):
    if max_bytes <= 0:
        raise DriveTransferError('DRIVE_MAX_MB must be positive.')
    timeout = aiohttp.ClientTimeout(total=3600, sock_connect=30, sock_read=60)
    connector = aiohttp.TCPConnector(resolver=PublicResolver(), use_dns_cache=False)
    async with aiohttp.ClientSession(
        connector=connector, timeout=timeout, trust_env=False,
        cookie_jar=aiohttp.DummyCookieJar(), auto_decompress=False,
        headers={'Accept-Encoding': 'identity', 'User-Agent': 'GateStream/1.0'},
    ) as session:
        for _ in range(6):
            validate_url(url)
            async with session.get(URL(url, encoded=True), allow_redirects=False) as response:
                if response.status in (301, 302, 303, 307, 308):
                    location = response.headers.get('Location')
                    if not location:
                        raise DriveTransferError('The download server returned an invalid redirect.')
                    url = urljoin(url, location)
                    continue
                if response.status != 200:
                    raise DriveTransferError(f'Download server returned HTTP {response.status}. Use a direct file link.')
                if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                    raise DriveTransferError('The server compressed the HTTP response. Use an uncompressed direct download endpoint.')
                if response.content_type in ('text/html', 'application/xhtml+xml'):
                    raise DriveTransferError('This URL returned a web page. Use a direct download link, not a player or login page.')
                size = response.content_length
                if size is not None and size > max_bytes:
                    raise DriveTransferError('The file exceeds DRIVE_MAX_MB.')
                reserve = 64 * 1024 * 1024
                if size is not None and size + reserve > shutil.disk_usage(directory).free:
                    raise DriveTransferError('Not enough temporary disk space for this file.')
                disposition = response.content_disposition
                name = (disposition.filename if disposition else None) or unquote(Path(urlsplit(url).path).name) or 'download.bin'
                name = re.sub(r'[\\/\x00-\x1f\x7f]', '_', name).strip(' .')[:180] or 'download.bin'
                path = Path(directory) / 'payload'  # Remote names never become local paths.
                written = 0
                if progress:
                    progress(0, size)
                async with aiofiles.open(path, 'wb') as output:
                    async for chunk in response.content.iter_chunked(1024 * 1024):
                        written += len(chunk)
                        if written > max_bytes:
                            raise DriveTransferError('The file exceeds DRIVE_MAX_MB.')
                        if len(chunk) + reserve > shutil.disk_usage(directory).free:
                            raise DriveTransferError('Temporary disk space is low; the download was stopped.')
                        await output.write(chunk)
                        if progress:
                            progress(written, size)
                if size is not None and written != size:
                    raise DriveTransferError('The download was incomplete. Retry with a working direct link.')
                return path, name
        raise DriveTransferError('The download URL redirected too many times.')


def upload_file(path, name, credentials, folder_id='', progress=None):
    metadata = {'name': name}
    if folder_id:
        metadata['parents'] = [folder_id]
    media = MediaFileUpload(str(path), mimetype=mimetypes.guess_type(name)[0] or 'application/octet-stream',
                            chunksize=8 * 1024 * 1024, resumable=True)
    try:
        with build('drive', 'v3', credentials=credentials, cache_discovery=False) as service:
            request = service.files().create(body=metadata, media_body=media,
                fields='id,name,size,webViewLink', supportsAllDrives=True)
            result = None
            while result is None:
                status, result = request.next_chunk(num_retries=3)
                if progress and status is not None:
                    progress(status.resumable_progress, path.stat().st_size)
        if int(result.get('size', -1)) != path.stat().st_size:
            raise DriveTransferError('Drive returned an unexpected file size. Check your Drive before retrying to avoid a duplicate.')
        if progress:
            progress(path.stat().st_size, path.stat().st_size)
        return result
    except RefreshError:
        raise DriveTransferError('Google authorization expired or was revoked. Regenerate your Drive token.') from None
    except HttpError as error:
        raise DriveTransferError(f'Drive upload failed (HTTP {error.resp.status}). Check the token scope, Drive API, folder access and storage quota.') from None
    finally:
        media.stream().close()
