"""Exercise the DeepSeek wire contract against a local HTTP server, without paid requests."""

import json
import threading
import unittest
from collections import deque
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import requests
from fastapi import FastAPI, Request
from open_webui.config import DEFAULT_CONFIG
from open_webui.models.config import Config
from open_webui.retrieval.utils import get_content_from_url
from open_webui.retrieval.web import deepseek
from open_webui.retrieval.web.utils import SafeDeepSeekLoader, get_web_loader
from open_webui.routers import retrieval
from open_webui.utils.session_pool import close_session
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        self.server.calls.append(
            (
                self.path,
                self.headers.get('Authorization'),
                json.loads(self.rfile.read(int(self.headers['Content-Length']))),
            )
        )
        status, payload = self.server.responses.popleft()
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class DeepSeekTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.server.calls = []
        self.server.responses = deque()
        self.key = 'test-deepseek-key'
        self.result = {
            'url': 'https://example.com/article',
            'title': '搜索结果',
            'snippet': 'short',
            'summary': 'long summary',
        }

    async def asyncTearDown(self):
        await close_session()

    def respond(self, payload, status=200):
        self.server.responses.append((status, payload))

    async def search(self, query='site:example.com 搜索', count=5, **kwargs):
        return await deepseek.search_deepseek(self.key, query, count, api_base_url=self.base + '/v1/', **kwargs)

    async def test_search_wire_contract_content_and_provider_order(self):
        self.respond(
            {
                'results': [
                    {**self.result, 'content': '# Full page', 'rerank_score': 1},
                    {**self.result, 'rerank_score': 9},
                ]
            }
        )
        results = await self.search(count=100, need_content=True)
        self.assertEqual(
            self.server.calls,
            [
                (
                    '/v1/tools/search',
                    f'Bearer {self.key}',
                    {'query': 'site:example.com 搜索', 'count': 50, 'need_content': True},
                )
            ],
        )
        self.assertEqual([item.snippet for item in results], ['# Full page', 'long summary'])
        self.assertEqual(results[0].title, '搜索结果')

    async def test_domain_filter_and_summary_fallback(self):
        self.respond(
            {
                'results': [
                    self.result,
                    {**self.result, 'url': 'https://excluded.example.org'},
                    {**self.result, 'summary': ''},
                    {'url': 'javascript:alert(1)'},
                    None,
                ]
            }
        )
        results = await self.search(count=2, filter_list=['example.com'])
        self.assertEqual([item.snippet for item in results], ['long summary', 'short'])
        self.assertTrue(all(item.link == self.result['url'] for item in results))

    async def test_empty_results_and_malformed_response(self):
        self.respond({'results': []})
        self.assertEqual(await self.search(), [])
        self.respond({'results': 'invalid'})
        with self.assertRaisesRegex(ValueError, 'Invalid DeepSeek Search response'):
            await self.search()

    async def test_invalid_query_and_missing_key_never_send_request(self):
        for query in ('   ', '中' * 129):
            with self.assertRaisesRegex(ValueError, '1–128'):
                await self.search(query=query)
        with self.assertRaisesRegex(ValueError, 'DEEPSEEK_SEARCH_API_KEY'):
            await deepseek.search_deepseek('', 'valid', 5)
        self.assertEqual(self.server.calls, [])

    async def test_transient_search_failures_retry_and_succeed(self):
        self.respond({}, 429)
        self.respond({}, 503)
        self.respond({'results': [self.result]})
        with patch.object(deepseek.asyncio, 'sleep', AsyncMock()) as wait:
            self.assertEqual(len(await self.search()), 1)
        self.assertEqual(len(self.server.calls), 3)
        self.assertEqual(wait.await_count, 2)

    async def test_permanent_errors_do_not_retry_or_leak_provider_body(self):
        for status in (400, 401, 402, 403, 404):
            with self.subTest(status=status):
                self.respond({'error': {'message': self.key}}, status)
                with self.assertRaises(deepseek.DeepSeekAPIError) as error:
                    await self.search()
                self.assertEqual(error.exception.status_code, status)
                self.assertNotIn(self.key, str(error.exception))
        self.assertEqual(len(self.server.calls), 5)

    async def test_fetch_wire_contract_and_retry(self):
        self.respond({}, 408)
        self.respond({'title': '标题', 'content': '# Markdown 正文'})
        with requests.Session() as session, patch.object(deepseek.time, 'sleep'):
            result = deepseek.fetch_deepseek_url(session, self.key, self.result['url'], self.base + '/v1')
        self.assertEqual(result['content'], '# Markdown 正文')
        self.assertEqual(self.server.calls[-1], ('/v1/tools/fetch', f'Bearer {self.key}', {'url': self.result['url']}))
        self.assertEqual(len(self.server.calls), 2)

    async def test_fetch_rejects_invalid_url_and_response(self):
        with requests.Session() as session:
            for url in ('file:///etc/passwd', 'https://user:password@example.com', 'https://example.com/' + 'a' * 8192):
                with self.assertRaisesRegex(ValueError, 'Invalid DeepSeek Fetch URL'):
                    deepseek.fetch_deepseek_url(session, self.key, url, self.base)
            self.assertEqual(self.server.calls, [])
            self.respond({'content': None})
            with self.assertRaisesRegex(ValueError, 'Invalid DeepSeek Fetch response'):
                deepseek.fetch_deepseek_url(session, self.key, self.result['url'], self.base)

    async def test_fetch_loader_skips_unavailable_pages_and_preserves_source(self):
        self.respond({}, 404)
        self.respond({'title': 'Fetched title', 'content': 'Fetched Markdown'})
        urls = ['https://example.com/missing', self.result['url']]
        loader = SafeDeepSeekLoader(urls, self.key, api_base_url=self.base)
        docs = await loader.aload()
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].page_content, 'Fetched Markdown')
        self.assertEqual(docs[0].metadata, {'source': self.result['url'], 'title': 'Fetched title'})

    async def test_fetch_loader_surfaces_account_errors_and_empty_content(self):
        for status in (401, 402):
            self.respond({}, status)
            loader = SafeDeepSeekLoader([self.result['url']], self.key, api_base_url=self.base)
            with self.assertRaises(deepseek.DeepSeekAPIError):
                await loader.aload()
        self.respond({'content': ' '})
        loader = SafeDeepSeekLoader([self.result['url']], self.key, api_base_url=self.base)
        with self.assertRaisesRegex(ValueError, 'could not load any page content'):
            await loader.aload()

    async def test_page_fetch_uses_saved_credentials_without_a_local_http_probe(self):
        config = {
            **DEFAULT_CONFIG,
            'web.loader.engine': 'deepseek',
            'web.search.deepseek_api_key': self.key,
            'web.search.deepseek_api_base_url': self.base,
        }
        self.respond({'title': 'Delegated page', 'content': 'Fetched through DeepSeek'})
        request = Request({'type': 'http', 'app': FastAPI(), 'headers': []})
        with (
            patch.object(Config, 'get_many', AsyncMock(return_value=config)) as settings,
            patch('open_webui.retrieval.web.utils.get_ssrf_safe_requests_session') as local_http,
        ):
            content, docs = await get_content_from_url(request, 'https://1.1.1.1/article')
        self.assertEqual(content, 'Fetched through DeepSeek')
        self.assertEqual(docs[0].metadata['source'], 'https://1.1.1.1/article')
        self.assertIn('web.search.deepseek_api_key', settings.await_args.args)
        self.assertIn('web.search.deepseek_api_base_url', settings.await_args.args)
        local_http.assert_not_called()

    async def test_loader_factory_retains_private_network_url_policy(self):
        config = {
            **DEFAULT_CONFIG,
            'web.loader.engine': 'deepseek',
            'web.search.deepseek_api_key': self.key,
            'web.search.deepseek_api_base_url': self.base,
        }
        loader = get_web_loader('https://1.1.1.1/article', config)
        self.assertIsInstance(loader, SafeDeepSeekLoader)
        with self.assertRaises(ValueError):
            get_web_loader('http://127.0.0.1/private', config)
        self.assertEqual(self.server.calls, [])

    async def test_saved_admin_settings_dispatch_to_search_and_read_back(self):
        engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Config.__table__.create)
            sessions = async_sessionmaker(engine, expire_on_commit=False)

            @asynccontextmanager
            async def database():
                async with sessions() as session:
                    yield session

            request = Request({'type': 'http', 'app': FastAPI(), 'headers': []})
            request.app.state.YOUTUBE_LOADER_TRANSLATION = None
            user = SimpleNamespace(id='test-user', role='admin')
            form = retrieval.WebConfig(
                WEB_SEARCH_ENGINE='deepseek',
                DEEPSEEK_SEARCH_API_KEY=self.key,
                DEEPSEEK_SEARCH_API_BASE_URL=self.base,
                DEEPSEEK_SEARCH_NEED_CONTENT=True,
            )
            updates = {
                retrieval.RETRIEVAL_CONFIG_KEYS[key]: value
                for key, value in form.model_dump(exclude_unset=True).items()
            }
            with (
                patch('open_webui.models.config.get_async_db', database),
                patch.object(Config, 'DEFAULTS', DEFAULT_CONFIG),
                patch.object(Config, 'PERSISTENT_ENABLED', True),
            ):
                await Config.upsert(updates)
                config = await Config.get_many(*retrieval.RETRIEVAL_CONFIG_KEYS.values())
                self.respond({'results': [self.result]})
                results = await retrieval.search_web(
                    request, config['web.search.engine'], 'saved settings', user, config=config
                )
                self.assertEqual(results[0].link, self.result['url'])
                returned = await retrieval.get_rag_config(request, user)
                self.assertEqual(returned['web']['DEEPSEEK_SEARCH_API_KEY'], self.key)
                self.assertEqual(returned['web']['DEEPSEEK_SEARCH_API_BASE_URL'], self.base)
                self.assertIs(returned['web']['DEEPSEEK_SEARCH_NEED_CONTENT'], True)
        finally:
            await engine.dispose()


if __name__ == '__main__':
    unittest.main()
