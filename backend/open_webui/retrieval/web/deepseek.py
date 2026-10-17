"""DeepSeek's standalone Search and Fetch tools (not chat completions)."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import aiohttp
import requests
from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL
from open_webui.utils.session_pool import get_session

if TYPE_CHECKING:
    from open_webui.retrieval.web.main import SearchResult

DEEPSEEK_API_BASE_URL = 'https://api.deepseek.com'
_RETRYABLE_STATUS = {408, 429, *range(500, 600)}


class DeepSeekAPIError(ValueError):
    def __init__(self, status: int):
        self.status_code = status
        hint = {
            401: 'Check the DeepSeek API key in Admin Settings > Web Search.',
            402: 'The DeepSeek account has insufficient balance.',
            403: 'DeepSeek refused access to the requested page.',
            404: 'DeepSeek could not retrieve the requested page.',
            429: 'DeepSeek rate limit exceeded. Try again shortly.',
        }.get(status, 'Check the search query, page URL, and DeepSeek service availability.')
        # Never expose upstream response bodies, which may contain credentials.
        super().__init__(f'DeepSeek Search/Fetch failed (HTTP {status}). {hint}')


def _tool_url(api_base_url: str, tool: str, api_key: str) -> str:
    if not api_key or not api_key.strip():
        raise ValueError('DeepSeek Search/Fetch requires a DEEPSEEK_SEARCH_API_KEY')
    base = (api_base_url or DEEPSEEK_API_BASE_URL).rstrip('/')
    if base.endswith('/v1'):
        base = base[:-3]
    parsed = urlsplit(base)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Invalid DeepSeek API Base URL')
    if parsed.query or parsed.fragment:
        raise ValueError('Invalid DeepSeek API Base URL')
    return f'{base}/v1/tools/{tool}'


def _retry_delay(retry_after: str | None, attempt: int) -> float:
    try:
        return min(30, max(1, float(retry_after)))
    except (TypeError, ValueError):
        return 2**attempt


async def search_deepseek(
    api_key: str,
    query: str,
    count: int,
    filter_list: list[str] | None = None,
    api_base_url: str = DEEPSEEK_API_BASE_URL,
    need_content: bool = False,
) -> list[SearchResult]:
    # Import lazily: web.main imports web.utils for URL validation.
    from open_webui.retrieval.web.main import SearchResult, get_filtered_results

    if not query.strip() or len(query) > 128:
        raise ValueError('DeepSeek search queries must contain 1–128 characters and cannot be blank.')
    url = _tool_url(api_base_url, 'search', api_key)
    count = max(1, min(50, count))
    session = await get_session()
    for attempt in range(3):
        async with session.post(
            url,
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            json={'query': query, 'count': count, 'need_content': need_content},
            timeout=aiohttp.ClientTimeout(total=60),
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
            allow_redirects=False,
        ) as response:
            if response.status in _RETRYABLE_STATUS and attempt < 2:
                delay = _retry_delay(response.headers.get('Retry-After'), attempt)
                await response.read()
            else:
                if response.status != 200:
                    raise DeepSeekAPIError(response.status)
                payload = await response.json()
                break
        await asyncio.sleep(delay)

    if not isinstance(payload, dict) or not isinstance(payload.get('results'), list):
        raise ValueError('Invalid DeepSeek Search response')
    results = [
        item
        for item in payload['results']
        if isinstance(item, dict)
        and isinstance(item.get('url'), str)
        and urlsplit(item['url']).scheme in ('http', 'https')
        and urlsplit(item['url']).hostname
    ]
    if filter_list:
        results = get_filtered_results(results, filter_list)

    def text(item, *keys):
        return next((item[key] for key in keys if isinstance(item.get(key), str) and item[key].strip()), '')

    # Preserve the provider's reranking order. Content is useful when bypassing the web loader.
    return [
        SearchResult(
            link=item['url'],
            title=text(item, 'title'),
            snippet=text(item, *(['content', 'summary', 'snippet'] if need_content else ['summary', 'snippet'])),
        )
        for item in results[:count]
    ]


def fetch_deepseek_url(
    session: requests.Session,
    api_key: str,
    url: str,
    api_base_url: str = DEEPSEEK_API_BASE_URL,
    timeout: float = 60,
) -> dict:
    endpoint = _tool_url(api_base_url, 'fetch', api_key)
    parsed = urlsplit(url)
    if (
        len(url) > 8192
        or parsed.scheme not in ('http', 'https')
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError('Invalid DeepSeek Fetch URL')
    for attempt in range(3):
        with session.post(
            endpoint,
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            json={'url': url},
            timeout=timeout,
            allow_redirects=False,
        ) as response:
            if response.status_code in _RETRYABLE_STATUS and attempt < 2:
                delay = _retry_delay(response.headers.get('Retry-After'), attempt)
            else:
                if response.status_code != 200:
                    raise DeepSeekAPIError(response.status_code)
                payload = response.json()
                break
        time.sleep(delay)
    if not isinstance(payload, dict) or not isinstance(payload.get('content'), str):
        raise ValueError('Invalid DeepSeek Fetch response')
    return payload
