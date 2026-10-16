"""Verify remaining provider behavior after removal of the native integration.

Run inside the complete application image:
    python -m unittest discover -s tests -p 'test_provider_dispatch.py' -v
"""

from types import SimpleNamespace
from contextlib import asynccontextmanager
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, Request
from pydantic import ValidationError
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from open_webui.routers.retrieval import EmbeddingModelUpdateForm
from open_webui.utils import chat, embeddings, models
from open_webui.models.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class ProviderDispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.state.MODELS = {'test-model': {'id': 'test-model', 'owned_by': 'openai'}}
        self.request = Request({'type': 'http', 'app': self.app, 'headers': []})
        self.user = SimpleNamespace(id='test-user', role='admin')

    async def test_chat_keeps_tools_and_multimodal_messages(self):
        payload = {
            'model': 'test-model',
            'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': 'Describe the image'}]}],
            'tools': [{'type': 'function', 'function': {'name': 'lookup', 'parameters': {'type': 'object'}}}],
        }
        expected = {'choices': [{'message': {'role': 'assistant', 'content': 'ok'}}]}
        with patch.object(chat, 'generate_openai_chat_completion', AsyncMock(return_value=expected)) as provider:
            result = await chat.generate_chat_completion(self.request, payload, self.user)
        self.assertIs(result, expected)
        self.assertEqual(provider.await_args.kwargs['form_data']['tools'], payload['tools'])
        self.assertEqual(provider.await_args.kwargs['form_data']['messages'], payload['messages'])

    async def test_chat_preserves_stream_and_background_cleanup(self):
        async def stream():
            yield 'data: {"choices":[]}\n\n'
            yield 'data: [DONE]\n\n'

        cleanup = AsyncMock()
        expected = StreamingResponse(stream(), media_type='text/event-stream', background=BackgroundTask(cleanup))
        with patch.object(chat, 'generate_openai_chat_completion', AsyncMock(return_value=expected)):
            result = await chat.generate_chat_completion(
                self.request, {'model': 'test-model', 'messages': [], 'stream': True}, self.user
            )
        self.assertIs(result, expected)
        self.assertEqual([chunk async for chunk in result.body_iterator][-1], 'data: [DONE]\n\n')
        await result.background()
        cleanup.assert_awaited_once()

    async def test_batch_embedding_dispatch(self):
        payload = {'model': 'test-model', 'input': ['one', 'two']}
        expected = {'data': [{'embedding': [1, 0]}, {'embedding': [0, 1]}]}
        with patch.object(embeddings, 'openai_embeddings', AsyncMock(return_value=expected)) as provider:
            result = await embeddings.generate_embeddings(self.request, payload, self.user)
        self.assertIs(result, expected)
        self.assertEqual(provider.await_args.kwargs['form_data']['input'], ['one', 'two'])

    async def test_discovery_keeps_function_models_and_respects_api_disable(self):
        with (
            patch.object(models.Config, 'get_many', AsyncMock(return_value={'openai.enable': False})),
            patch.object(models, 'fetch_openai_models', AsyncMock()) as provider,
            patch.object(models, 'get_function_models', AsyncMock(return_value=[{'id': 'function'}])),
        ):
            result = await models.get_all_base_models(self.request, self.user)
        self.assertEqual(result, [{'id': 'function'}])
        provider.assert_not_awaited()

    def test_retired_embedding_engine_is_rejected(self):
        with self.assertRaises(ValidationError):
            EmbeddingModelUpdateForm(RAG_EMBEDDING_ENGINE='ollama', RAG_EMBEDDING_MODEL='old-model')
        for engine in ('', 'openai', 'azure_openai'):
            self.assertEqual(
                EmbeddingModelUpdateForm(RAG_EMBEDDING_ENGINE=engine, RAG_EMBEDDING_MODEL='model').RAG_EMBEDDING_ENGINE,
                engine,
            )

    async def test_existing_settings_upgrade_is_idempotent_and_preserves_other_providers(self):
        engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Config.__table__.create)
            sessions = async_sessionmaker(engine, expire_on_commit=False)

            @asynccontextmanager
            async def database():
                async with sessions() as session:
                    yield session

            defaults = {
                'rag.embedding_engine': '',
                'rag.embedding_model': 'local-model',
                'web.search.engine': 'searxng',
            }
            with (
                patch('open_webui.models.config.get_async_db', database),
                patch.object(Config, 'DEFAULTS', defaults),
                patch.object(Config, 'PERSISTENT_ENABLED', True),
            ):
                await Config.upsert(
                    {
                        'ollama.enable': True,
                        'rag.ollama.api_key': 'retired-secret',
                        'OLLAMA_API_CONFIGS.0.key': 'retired-secret',
                        'web.search.ollama_cloud_api_key': 'retired-secret',
                        'rag.embedding_engine': 'ollama',
                        'rag.embedding_model': 'old-model',
                        'web.search.engine': 'ollama_cloud',
                        'openai.api_configs': {'0': {'provider': 'llama.cpp'}},
                    }
                )
                await Config.remove_retired_provider_settings()
                await Config.remove_retired_provider_settings()
                self.assertEqual(await Config.get('rag.embedding_engine'), '')
                self.assertEqual(await Config.get('rag.embedding_model'), 'local-model')
                self.assertEqual(await Config.get('web.search.engine'), 'searxng')
                self.assertIsNone(await Config.get('rag.ollama.api_key'))
                self.assertIsNone(await Config.get('OLLAMA_API_CONFIGS.0.key'))
                self.assertEqual(await Config.get('openai.api_configs'), {'0': {'provider': 'llama.cpp'}})
        finally:
            await engine.dispose()


if __name__ == '__main__':
    unittest.main()
