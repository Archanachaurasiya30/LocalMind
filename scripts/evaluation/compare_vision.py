"""Opt-in, local-only vision checks using generated fixtures, never user screenshots."""
import argparse
import base64
import json
from pathlib import Path
import time
from urllib.request import Request, ProxyHandler, build_opener

from PIL import Image, ImageDraw, ImageFont


def fixtures(folder):
    font_path = Path('C:/Windows/Fonts/arial.ttf')
    font = ImageFont.truetype(str(font_path), 18) if font_path.exists() else ImageFont.load_default(size=18)
    cases = []
    for name, lines, required, question, followup, expected in [
        ('small-text', ['SYNTHETIC SETTINGS', 'Workspace: Cedar', 'Port: 18473', 'Retry delay: 250 ms', 'Status: Paused'],
         ['cedar', '18473', '250', 'paused'], 'Read the workspace, port, retry delay and status exactly.', 'What port did the screenshot show?', ['18473']),
        ('chart', ['SYNTHETIC COUNTS', 'A: 12', 'B: 30', 'C: 18'],
         ['b', '30'], 'Which category has the highest count, and what is its value?', 'How much greater is B than A?', ['18']),
    ]:
        image = Image.new('RGB', (800, 400), 'white')
        draw = ImageDraw.Draw(image)
        for index, line in enumerate(lines):
            draw.text((24, 24 + index * 34), line, fill='black', font=font)
        if name == 'chart':
            for index, value in enumerate([12, 30, 18]):
                x = 380 + index * 110
                draw.rectangle((x, 340-value*7, x+55, 340), fill='#326b66')
                draw.text((x, 350), chr(65+index), fill='black', font=font)
        path = folder / (name + '.png')
        image.save(path)
        cases.append(dict(id=name, image=path, required=required, question=question, followup=followup, expected=expected))
    return cases


def call(path, body=None):
    opener = build_opener(ProxyHandler({}))
    request = Request('http://127.0.0.1:11434' + path,
                      data=json.dumps(body).encode() if body is not None else None,
                      headers={'Content-Type': 'application/json'})
    with opener.open(request, timeout=240) as response:
        return json.loads(response.read(8 * 1024 * 1024))


def run(folder, models):
    folder.mkdir(parents=True, exist_ok=True)
    cases = fixtures(folder)
    report = dict(schema_version=1, scope='Synthetic exact-reading checks, not general vision quality or a human preference study', results=[])
    for model in models:
        for case in cases:
            messages = [dict(role='user', content=case['question'], images=[base64.b64encode(case['image'].read_bytes()).decode()])]
            for stage, expected in [('initial', case['required']), ('followup', case['expected'])]:
                started = time.perf_counter()
                try:
                    response = call('/api/chat', dict(model=model, messages=messages, stream=False, think=False,
                                                     options=dict(temperature=0, num_predict=1024, num_ctx=4096), keep_alive='2m'))
                    answer = response['message']['content']
                    row = dict(model=model, case=case['id'], stage=stage, seconds=round(time.perf_counter()-started, 2), answer=answer,
                               expected=expected, exact_checks_pass=all(value in answer.lower() for value in expected),
                               load_seconds=response.get('load_duration', 0)/1e9, eval_count=response.get('eval_count'), done_reason=response.get('done_reason'), thinking_characters=len(response['message'].get('thinking', '')), loaded=call('/api/ps'))
                    messages += [dict(role='assistant', content=answer), dict(role='user', content=case['followup'])]
                except Exception as error:
                    row = dict(model=model, case=case['id'], stage=stage, seconds=round(time.perf_counter()-started, 2), error=str(error), exact_checks_pass=False)
                report['results'].append(row)
                (folder/'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
                print(model, case['id'], stage, row['seconds'], row['exact_checks_pass'], flush=True)
                if 'error' in row:
                    break
        call('/api/generate', dict(model=model, keep_alive=0))
    return report


async def run_localmind(folder, models):
    """Exercise canonical attachments and history through the actual chat route."""
    import asyncio
    import os
    import tempfile
    from uuid import uuid4
    from app import attachments
    from app.api.routes.chat import ChatRequest, _stream_ollama
    from app.db import conversations

    folder.mkdir(parents=True, exist_ok=True)
    report = dict(schema_version=1, scope='LocalMind route, isolated profile, synthetic images', results=[])
    previous = os.environ.get('LOCALMIND_DATA_DIR')
    try:
        with tempfile.TemporaryDirectory(prefix='localmind-vision-') as profile:
            os.environ['LOCALMIND_DATA_DIR'] = profile
            for model in models:
                for case in fixtures(folder):
                    cid = conversations.create_conversation(case['id'])['id']
                    draft = str(uuid4())
                    item = attachments.create(case['image'].name, base64.b64encode(case['image'].read_bytes()).decode(), draft, cid)
                    for stage, question, expected in [('initial', case['question'], case['required']), ('followup', case['followup'], case['expected'])]:
                        started = time.perf_counter()
                        ids = [item['id']] if stage == 'initial' else []
                        request = ChatRequest(message=question, model=model, conversation_id=cid,
                                              attachment_ids=ids, attachment_draft_id=draft,
                                              connector_context_enabled=False)
                        events = []
                        async def consume():
                            async for line in _stream_ollama(request):
                                events.append(json.loads(line.removeprefix('data: ')))
                        try:
                            await asyncio.wait_for(consume(), 240)
                            answer = ''.join(event.get('content', '') for event in events if event['type'] == 'token')
                            errors = [event for event in events if event['type'] == 'error']
                            passed = bool(events and events[-1]['type'] == 'done' and not errors and all(value in answer.lower() for value in expected))
                            conversations.save_turn(cid, question, answer, attachment_ids=ids)
                            row = dict(model=model, case=case['id'], stage=stage, answer=answer, errors=errors, exact_checks_pass=passed)
                        except Exception as error:
                            row = dict(model=model, case=case['id'], stage=stage, error=str(error), exact_checks_pass=False)
                        row['seconds'] = round(time.perf_counter()-started, 2)
                        report['results'].append(row)
                        (folder/'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
                        print(model, case['id'], stage, row['seconds'], row['exact_checks_pass'], flush=True)
                call('/api/generate', dict(model=model, keep_alive=0))
    finally:
        if previous is None:
            os.environ.pop('LOCALMIND_DATA_DIR', None)
        else:
            os.environ['LOCALMIND_DATA_DIR'] = previous
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Load approved models and run local inference')
    parser.add_argument('--out', type=Path, default=Path('artifacts/visual-batch/vision'))
    parser.add_argument('--models', nargs='+', default=['qwen3-vl:4b', 'gemma3:4b'])
    parser.add_argument('--through-localmind', action='store_true', help='Use the real chat route and an isolated profile; requires services/ai on PYTHONPATH')
    args = parser.parse_args()
    if not args.execute:
        parser.error('Pass --execute only after approving local model loading.')
    if args.through_localmind:
        import asyncio
        asyncio.run(run_localmind(args.out, args.models))
    else:
        run(args.out, args.models)
