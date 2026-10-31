import json
import re

from .common import InfoExtractor
from ..utils import (
    ExtractorError,
    int_or_none,
    parse_iso8601,
    url_or_none,
)


class CeskaTelevizeIE(InfoExtractor):
    _VALID_URL = r'https?://(?:www\.)?ceskatelevize\.cz/porady/(?P<id>[^?#]+)'
    _TESTS = [{
        'url': 'https://www.ceskatelevize.cz/porady/1098674900-cerveny-trpaslik/299381425020001-konec/',
        'info_dict': {
            'id': '299381425020001',
            'ext': 'mp4',
            'title': '1/6 Konec',
            'series': 'Červený trpaslík',
            'duration': 1799,
            'description': 'md5:d99042cfa5905d26ff8c40f8bd0a2ec7',
        },
    }]

    def _real_extract(self, url):
        playlist_id = self._match_id(url)
        webpage = self._download_webpage(url, playlist_id)

        # 1. Získání __NEXT_DATA__ JSON objektu ze stránky
        next_data_json = self._search_regex(
            r'<script\s+id="__NEXT_DATA__"\s+type="application/json">(?P<json>.+?)</script>',
            webpage,
            'next data',
            default=None
        )

        idec = None
        title = None
        description = None

        if next_data_json:
            try:
                data = json.loads(next_data_json)
                props = data.get('props', {}).get('pageProps', {})
                media_data = props.get('mediaData', {}) or props.get('data', {})

                idec = media_data.get('idec') or props.get('idec')
                title = media_data.get('title') or props.get('title')
                description = media_data.get('description') or props.get('synopsis')
            except Exception:
                pass

        # 2. Bezpečné vytažení 15místného IDEC z URL nebo fallback
        if not idec or not str(idec).isdigit() or len(str(idec)) != 15:
            idec_match = re.search(r'/(\d{15})(?:/|$)', url)
            if idec_match:
                idec = idec_match.group(1)
            else:
                idec = self._search_regex(
                    r'["\']IDEC["\']\s*:\s*["\'](\d{15})["\']',
                    webpage,
                    'idec fallback',
                    default=None
                )

        if not idec:
            raise ExtractorError('Nepodařilo se nalézt platný IDEC epizody.', expected=True)

        # 3. Volání nového REST API ČT pro stream-data
        api_url = f'https://api.ceskatelevize.cz/video/v1/playlist-vod/v1/stream-data/media/external/{idec}'
        headers = {
            'Accept': 'application/json',
            'Referer': url,
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        }
        params = {
            'canPlayDrm': 'false',
            'origin': 'ivysilani',
            'client': 'ivysilaniweb',
        }

        stream_data = self._download_json(
            api_url, str(idec), query=params, headers=headers, fatal=False
        )

        if not stream_data:
            raise ExtractorError(f'Nepodařilo se načíst stream data pro IDEC {idec}.', expected=True)

        # API může vracet streamy pod různými klíči (např. 'playlist', 'streams', nebo přímo v kořenu)
        playlists = stream_data.get('streams') or stream_data.get('playlist') or stream_data.get('data', {}).get('streams', [])
        
        # Pokud je to slovník s HLS/DASH klíči, převedeme na seznam
        if isinstance(playlists, dict):
            temp_list = []
            for k, v in playlists.items():
                if isinstance(v, str):
                    temp_list.append({'format': k, 'url': v})
                elif isinstance(v, dict):
                    v['format'] = v.get('format', k)
                    temp_list.append(v)
            playlists = temp_list

        formats = []
        subtitles = {}

        for stream in playlists:
            if not isinstance(stream, dict):
                continue
            stream_url = stream.get('url') or stream.get('streamUrl')
            if not stream_url:
                continue

            stream_type = str(stream.get('format', '')).lower()
            if 'hls' in stream_type or stream_url.endswith('.m3u8'):
                fmts = self._extract_m3u8_formats(
                    stream_url, str(idec), ext='mp4', entry_protocol='m3u8_native', m3u8_id='hls', fatal=False
                )
                formats.extend(fmts)
            elif 'dash' in stream_type or stream_url.endswith('.mpd'):
                fmts = self._extract_mpd_formats(
                    stream_url, str(idec), mpd_id='dash', fatal=False
                )
                formats.extend(fmts)
            else:
                # Obecný fallback pro přímé URL soubory
                formats.append({
                    'url': stream_url,
                    'format_id': stream_type or 'direct',
                })

        # 4. Zpracování titulků
        sub_list = stream_data.get('subtitles', [])
        for sub in sub_list:
            sub_url = sub.get('url')
            if not sub_url:
                continue
            lang = sub.get('language', 'cs')
            subtitles.setdefault(lang, []).append({
                'url': sub_url,
                'ext': sub.get('format', 'srt').lower(),
            })

        return {
            'id': str(idec),
            'title': title or self._og_search_title(webpage, default=playlist_id),
            'description': description or self._og_search_description(webpage, default=None),
            'formats': formats,
            'subtitles': subtitles,
            'thumbnail': self._og_search_thumbnail(webpage, default=None),
        }