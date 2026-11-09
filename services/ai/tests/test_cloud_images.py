import asyncio
import base64
from io import BytesIO
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

from app import cloud_images, media, media_store, provider_store


class CloudImageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        env = patch.dict(os.environ, {'LOCALMIND_DATA_DIR': folder.name})
        env.start()
        self.addCleanup(env.stop)
        provider_store.configure_policy({'local_only': False, 'monthly_limit_usd': 1})
        self.jobs = media.MediaJobs()
        key = patch('app.cloud_images._key', return_value='test-key')
        key.start()
        self.addCleanup(key.stop)
        self.real_client = httpx.AsyncClient

    async def asyncTearDown(self):
        await self.jobs.shutdown()

    def prepare(self, **changes):
        return cloud_images.prepare(**({'prompt': 'A blue cup', 'model': 'gpt-image-2', 'allowance_cents': 25} | changes))

    async def run_job(self, job, handler):
        def client(**kwargs):
            return self.real_client(transport=httpx.MockTransport(handler), **kwargs)
        with patch('app.cloud_images.httpx.AsyncClient', side_effect=client):
            await cloud_images.submit(self.jobs, job['id'])
            task = self.jobs.tasks.get(job['id'])
            if task:
                await task
        return media_store.get(job['id'])

    def test_local_only_blocks_preparation(self):
        provider_store.configure_policy({'local_only': True})
        with self.assertRaises(ValueError):
            self.prepare()

    async def test_local_only_rechecked_after_review(self):
        job = self.prepare()
        provider_store.configure_policy({'local_only': True})
        with self.assertRaises(ValueError):
            await cloud_images.submit(self.jobs, job['id'])
        self.assertEqual(media_store.get(job['id'])['state'], 'review')

    async def test_completed_bytes_provider_receipt_and_no_duplicate_submission(self):
        image = BytesIO()
        Image.new('RGB', (16, 16), 'blue').save(image, 'PNG')
        calls = []

        def handler(request):
            calls.append(request)
            self.assertEqual(str(request.url), 'https://api.openai.com/v1/images/generations')
            return httpx.Response(200, headers={'x-request-id': 'request-123'}, json={'data': [{'b64_json': base64.b64encode(image.getvalue()).decode()}]})

        job = self.prepare()
        result = await self.run_job(job, handler)
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(result['provider_id'], 'request-123')
        self.assertTrue((media_store.folder(job['id'])/'output.png').exists())
        await cloud_images.submit(self.jobs, job['id'])
        self.assertEqual(len(calls), 1)
        self.assertEqual(provider_store.usage()['reserved_usd'], .25)
        edited = self.prepare(reference_id=job['id'])
        self.assertEqual((media_store.folder(edited['id'])/'source.png').read_bytes(), (media_store.folder(job['id'])/'output.png').read_bytes())
        self.assertNotEqual(edited['id'], job['id'])
        def edit_handler(request):
            self.assertEqual(str(request.url), 'https://api.openai.com/v1/images/edits')
            self.assertIn('multipart/form-data', request.headers['content-type'])
            self.assertIn(b'filename="reference.png"', request.content)
            self.assertIn((media_store.folder(job['id'])/'output.png').read_bytes(), request.content)
            return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(image.getvalue()).decode()}]})
        edit_result = await self.run_job(edited, edit_handler)
        self.assertEqual(edit_result['state'], 'completed')
        self.assertEqual(media_store.get(job['id'])['state'], 'completed')

    async def test_revocation_before_task_runs_sends_nothing(self):
        job = self.prepare()
        with patch.object(self.jobs, 'launch') as launch:
            await cloud_images.submit(self.jobs, job['id'])
        pending = launch.call_args.args[1]
        provider_store.configure_policy({'local_only': True})
        with patch('app.cloud_images.httpx.AsyncClient') as client:
            await pending
            client.assert_not_called()
        self.assertEqual(media_store.get(job['id'])['state'], 'failed')
        self.assertEqual(provider_store.usage()['reserved_usd'], 0)

    async def test_provider_rejection_releases_reservations(self):
        result = await self.run_job(self.prepare(), lambda request: httpx.Response(401))
        self.assertEqual(result['state'], 'failed')
        self.assertFalse(result['reserved'])
        self.assertEqual(provider_store.usage()['reserved_usd'], 0)

    async def test_cancel_before_coroutine_starts_does_not_leave_running_job(self):
        job = self.prepare()
        with patch('app.cloud_images.httpx.AsyncClient') as client:
            await cloud_images.submit(self.jobs, job['id'])
            result = await self.jobs.cancel(job['id'])
            client.assert_not_called()
        self.assertEqual(result['state'], 'cancelled')
        self.assertEqual(provider_store.usage()['reserved_usd'], .25)

    async def test_ambiguous_failure_and_invalid_bytes_keep_reservation(self):
        for response in [httpx.Response(503), httpx.Response(200, json={'data': [{'b64_json': 'not an image'}]}), httpx.Response(200, json={'data': [{'url': 'https://untrusted.test'}]})]:
            with self.subTest(status=response.status_code):
                result = await self.run_job(self.prepare(), lambda request: response)
                self.assertEqual(result['state'], 'unknown')
                self.assertTrue(result['reserved'])
                self.assertFalse(result['output'])
        self.assertEqual(provider_store.usage()['reserved_usd'], .75)

    async def test_budget_failure_never_contacts_provider(self):
        provider_store.configure_policy({'monthly_limit_usd': .1})
        with patch('app.cloud_images.httpx.AsyncClient') as client:
            with self.assertRaises(ValueError):
                await cloud_images.submit(self.jobs, self.prepare()['id'])
            client.assert_not_called()

    async def test_cancel_is_not_a_refund_and_recovery_does_not_retry(self):
        entered = asyncio.Event()

        async def handler(request):
            entered.set()
            await asyncio.Event().wait()

        def client(**kwargs):
            return self.real_client(transport=httpx.MockTransport(handler), **kwargs)

        job = self.prepare()
        with patch('app.cloud_images.httpx.AsyncClient', side_effect=client):
            await cloud_images.submit(self.jobs, job['id'])
            await asyncio.wait_for(entered.wait(), 3)
            result = await self.jobs.cancel(job['id'])
        self.assertEqual(result['state'], 'cancelled')
        self.assertTrue(result['reserved'])
        media_store.update(job['id'], state='generating')
        self.jobs.recover()
        self.assertEqual(media_store.get(job['id'])['state'], 'unknown')
