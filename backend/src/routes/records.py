from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from src.config.project import SPEC
from src.constants.enums import Role, SingerState
from src.controllers.views import ledger_view
from src.models.base import Session
from src.models.workflow import ImportBatch, Reading
from src.services.auth import current_user, roles
from src.services.errors import BusinessError
from src.services.readings import (
    ingest_rows,
    ledger_states,
    parse_csv,
    clean_reading,
    reading_view,
)
from src.utils.formatting import csv_text, timestamp

router = APIRouter(prefix='/api')
editor = roles(Role.ANALYST, Role.SUPERVISOR)


@router.get('/spec')
def spec():
    return SPEC


@router.get('/ledger/summary')
def summary(user=Depends(current_user)):
    with Session() as db:
        states = ledger_states(db)
        counts = {state: 0 for state in SingerState}
        for entry in states.values():
            counts[entry['state']] += 1
        total_readings = db.scalar(select(func.count(Reading.id)))
        return {'singers': len(states), 'readings': total_readings,
                'paired': counts[SingerState.PAIRED], 'missing': counts[SingerState.MISSING],
                'blocked': counts[SingerState.BLOCKED]}


@router.get('/ledger')
def ledger(state: str | None = Query(None), q: str | None = Query(None),
           user=Depends(current_user)):
    with Session() as db:
        entries = list(ledger_states(db).values())
    if state and state != 'all':
        try:
            wanted = SingerState(state)
        except ValueError as exc:
            raise BusinessError('不支持的台账筛选条件') from exc
        entries = [entry for entry in entries if entry['state'] == wanted]
    if q:
        needle = q.strip()
        entries = [entry for entry in entries if needle in entry['singer'] or needle in entry['part']]
    entries.sort(key=lambda entry: entry['singer'])
    return [ledger_view(entry) for entry in entries]


@router.get('/readings')
def readings(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
             singer: str | None = Query(None), user=Depends(current_user)):
    with Session() as db:
        query = select(Reading)
        count_query = select(func.count(Reading.id))
        if singer:
            query = query.where(Reading.singer == singer)
            count_query = count_query.where(Reading.singer == singer)
        total = db.scalar(count_query)
        items = db.scalars(query.order_by(Reading.id.desc()).offset(offset).limit(limit))
        return {'total': total, 'offset': offset,
                'items': [reading_view(item) for item in items]}


@router.post('/readings', status_code=201)
def create(payload: dict, user=Depends(editor)):
    row = clean_reading(payload)
    with Session.begin() as db:
        report = ingest_rows(db, [row], user['sub'], action='reading.created',
                             label=f'单条登记 · {row["singer"]}')
        return {'report': report}


@router.get('/readings/template')
def template(user=Depends(current_user)):
    fields = SPEC['fields']
    payload = csv_text([{'key': field['key'], 'label': field['key']} for field in fields],
                       [{field['key']: field.get('example') for field in fields}])
    return Response(payload, media_type='text/csv',
                    headers={'Content-Disposition': f'attachment; filename={SPEC["slug"]}-template.csv'})


@router.get('/batches')
def batches(user=Depends(current_user)):
    with Session() as db:
        rows = db.scalars(select(ImportBatch).order_by(ImportBatch.id.desc()).limit(100))
        return [{'id': item.id, 'label': item.label, 'actor': item.actor,
                 'batch_key': item.batch_key, 'received_at': timestamp(item.received_at)}
                for item in rows]


def import_payload(payload, actor):
    rows = parse_csv(payload)
    with Session.begin() as db:
        return ingest_rows(db, rows, actor, action='reading.imported', label='CSV 分批导入')


@router.post('/readings/import')
async def import_csv(request: Request, user=Depends(editor)):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 512 * 1024:
            raise BusinessError('CSV 文件上限 512 KB', 413)
        raw.extend(chunk)
    return await run_in_threadpool(import_payload, bytes(raw), user['sub'])
