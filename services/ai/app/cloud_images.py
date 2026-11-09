"""Reviewed image requests. No retries after a provider may have accepted payment."""
import asyncio
import hashlib
import json

import httpx

from app import media_store as store, provider_store
from app.attachments import normalize_image
from app.providers import _key
from app.media_files import MAX_IMAGE

MODELS = ('gpt-image-2', 'gpt-image-1.5', 'gpt-image-1-mini')


def prepare(prompt, model, allowance_cents, reference_id=None):
    provider_store.ensure_remote_allowed()
    if model not in MODELS or not prompt.strip() or len(prompt) > 1000:
        raise ValueError('Choose a supported image model and a prompt of 1–1000 characters.')
    if type(allowance_cents) is not int or not 1 <= allowance_cents <= 500:
        raise ValueError('Choose a reservation between 1 and 500 cents. It is not a provider billing cap.')
    source = None
    if reference_id:
        from app.api.routes.media import checked_file
        original = store.get(reference_id)
        if original['kind'] not in ('image', 'cloud-image'):
            raise ValueError('Select a completed image as the reference.')
        _, _, source = checked_file(reference_id)
    job = store.create('cloud-image', prompt.strip(), model=model, provider='openai',
                       location='OpenAI API', license='Provider terms', reference_id=reference_id,
                       source_hash=hashlib.sha256(source).hexdigest() if source else None,
                       allowance_cents=allowance_cents, size='1024x1024', quality='low')
    if source:
        (store.folder(job['id'])/'source.png').write_bytes(source)
    return store.update(job['id'], cost_cents=allowance_cents)


def reserve(job):
    # Share the cloud ledger with chat: image reservations must not bypass the user's limit.
    with provider_store.database() as db:
        policy = provider_store._get(db, 'policy', {'local_only': True, 'monthly_limit_usd': None})
        if policy['local_only']:
            raise ValueError('Local-only mode is enabled.')
        usage = provider_store._usage(db)
        amount = job['allowance_cents']/100
        limit = policy.get('monthly_limit_usd')
        if limit is not None and (usage['spent_usd'] + usage['reserved_usd'] + amount > limit or db.execute("SELECT 1 FROM requests WHERE month=? AND state IN ('pending','uncertain') AND input_price IS NULL", (provider_store._month(),)).fetchone()):
            raise ValueError('The cloud monthly limit cannot cover this reservation.')
        db.execute('INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (job['id'], 'openai', job['model'], provider_store._month(), amount, None, 'pending', store.now(), 0, 0))


async def submit(manager, job_id):
    provider_store.ensure_remote_allowed()
    job = store.get(job_id)
    if job['kind'] != 'cloud-image':
        raise ValueError('This is not an image API job.')
    if job['state'] != 'review':
        return job
    token = _key('openai')
    source = None
    if job['reference_id']:
        source = (store.folder(job_id)/'source.png').read_bytes()
        if hashlib.sha256(source).hexdigest() != job['source_hash']:
            raise ValueError('The reviewed reference changed. Prepare a new request.')
    job, claimed = store.claim(job_id)
    if not claimed:
        return job
    try:
        reserve(job)
    except Exception:
        store.update(job_id, state='failed', reserved=False, message='No request sent. The cloud allowance or policy blocked submission.')
        raise
    manager.launch(job_id, generate(job, token, source))
    return job


async def generate(job, token, source):
    identifier = job['id']
    try:
        provider_store.ensure_remote_allowed()
        if _key('openai') != token:
            raise ValueError('The reviewed credential changed.')
    except Exception:
        provider_store.settle(identifier, rejected=True)
        store.update(identifier, state='failed', reserved=False, message='Cloud access changed before submission. Nothing was sent; prepare a new review.')
        return
    try:
        store.update(identifier, state='generating', message='Waiting for OpenAI. This may incur a charge.')
        values = dict(model=job['model'], prompt=job['prompt'], size=job['size'], quality=job['quality'], n=1, output_format='png')
        arguments = {'json': values} if source is None else {'data': {key: str(value) for key, value in values.items()}, 'files': {'image': ('reference.png', source, 'image/png')}}
        endpoint = 'generations' if source is None else 'edits'
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=httpx.Timeout(180, connect=10)) as client:
            async with client.stream('POST', f'https://api.openai.com/v1/images/{endpoint}', headers={'Authorization': f'Bearer {token}'}, **arguments) as response:
                provider_id = response.headers.get('x-request-id', '')[:200]
                store.update(identifier, provider_id=provider_id or None)
                if response.status_code in (400, 401, 403, 404, 413, 422, 429):
                    provider_store.settle(identifier, rejected=True)
                    store.update(identifier, state='failed', reserved=False, message='OpenAI rejected this request. Check the key, account access and prompt before preparing another request.')
                    return
                response.raise_for_status()
                output = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(output) + len(chunk) > 16*1024*1024:
                        raise ValueError('Provider response too large')
                    output.extend(chunk)
                result = json.loads(output)
        data, _, width, height = normalize_image(result['data'][0]['b64_json'])
        if len(data) > MAX_IMAGE:
            raise ValueError('Image exceeds the local media limit')
        # Only verified bytes become a completed result. Provider URLs are never fetched.
        folder = store.folder(identifier)
        temporary = folder/'output.partial'
        temporary.write_bytes(data)
        temporary.replace(folder/'output.png')
        provider_store.settle(identifier)  # Usage cannot be inferred from image count; retain reservation.
        store.update(identifier, state='completed', output=True, progress=100, width=width, height=height,
                     message='Image saved on this device. The allowance remains reserved; check actual provider billing.')
    except asyncio.CancelledError:
        provider_store.settle(identifier)
        store.update(identifier, state='cancelled', message='Stopped waiting locally. OpenAI may finish and charge; this is not a refund or provider cancellation.')
        raise
    except Exception:
        provider_store.settle(identifier)
        store.update(identifier, state='unknown', message='The image request could not be verified. It may have been charged. Check provider billing before preparing another request; no automatic retry was sent.')
