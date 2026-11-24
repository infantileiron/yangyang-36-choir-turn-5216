"""读数导入与试唱台账。

读数按「演唱者 + 提示条件 + 版本」管理：
- 两种提示条件的读数都到位才构成一个可入样配对；
- 同一提示同一版本读数冲突（偏差不同）时整人搁置，待更高版本解开；
- 迟到的低版本读数只归档，不会盖过更新版本；
- 导入以批次内容指纹幂等，重试同一批不新增读数。
"""
import csv
import hashlib
import io
import json
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.config.project import SPEC
from src.constants.enums import Cue, ReadingStatus, SingerState
from src.models.workflow import AuditEvent, ImportBatch, Reading
from src.services.errors import BusinessError
from src.services.validation import validated
from src.utils.formatting import timestamp

MAX_READINGS = 5000
MAX_BATCH_ROWS = 500
CSV_LIMIT = 512 * 1024
CUE_BY_LABEL = {label: key for key, label in SPEC['cue_label'].items()}
LABEL_BY_CUE = dict(SPEC['cue_label'])


def cue_of(label):
    if label not in CUE_BY_LABEL:
        raise BusinessError('提示条件只能是「常规提示」或「新提示」')
    return Cue(CUE_BY_LABEL[label])


def normalize_cue(value):
    """接受枚举键（usual/new）或中文标签，统一返回 Cue 枚举。"""
    if isinstance(value, Cue):
        return value
    if value in Cue._value2member_map_:
        return Cue(value)
    return cue_of(value)


def clean_reading(raw):
    row = validated(SPEC['fields'], raw)
    if 'cue' not in row:
        raise BusinessError('请选择提示条件')
    row['cue'] = normalize_cue(row['cue'])
    deviation = round(float(row['deviation']), 6)
    if not 0 <= deviation <= 10:
        raise BusinessError('节拍偏差以绝对拍数计且在 0–10 之间')
    row['deviation'] = deviation
    row.setdefault('version', None)
    row.setdefault('sequence', None)
    return row


def parse_csv(raw):
    if len(raw) > CSV_LIMIT:
        raise BusinessError('CSV 文件上限 512 KB', 413)
    try:
        reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')), strict=True)
        header = reader.fieldnames or []
        known = {item['key'] for item in SPEC['fields']}
        required = {item['key'] for item in SPEC['fields'] if item.get('required', True)}
        if len(set(header)) != len(header) or not required.issubset(header) or not set(header).issubset(known):
            raise BusinessError('CSV 表头与导入模板不匹配')
        rows = []
        for raw_row in reader:
            try:
                if None in raw_row or any(value is None for value in raw_row.values()):
                    raise BusinessError('列数与表头不匹配')
                rows.append(clean_reading(raw_row))
            except BusinessError as exc:
                raise BusinessError(f'CSV 第 {reader.line_num} 行，{exc}') from exc
            if len(rows) > MAX_BATCH_ROWS:
                raise BusinessError('单次导入上限 500 条读数')
        if not rows:
            raise BusinessError('CSV 里没有读数记录')
        return rows
    except (UnicodeDecodeError, csv.Error) as exc:
        raise BusinessError('请上传 UTF-8 编码的有效 CSV') from exc


def content_hash(rows):
    body = json.dumps(rows, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()


def _check_part_consistency(db, rows):
    parts = dict(db.execute(select(Reading.singer, Reading.part).distinct()).all())
    seen = dict(parts)
    for row in rows:
        prior = seen.get(row['singer'])
        if prior and prior != row['part']:
            raise BusinessError(f'演唱者 {row["singer"]} 的声部与既有记录（{prior}）不一致')
        seen[row['singer']] = row['part']


def _resolve_versions(db, rows):
    """写入前解析每条读数的版本号，并在批次内对完全相同的读数去重。"""
    maxima = {}
    for singer, cue, version in db.execute(
            select(Reading.singer, Reading.cue, func.max(Reading.version)).group_by(
                Reading.singer, Reading.cue)):
        maxima[(singer, cue)] = version
    resolved, seen_in_batch = [], set()
    for row in rows:
        key = (row['singer'], row['cue'])
        next_version = maxima.get(key, 0) + 1
        version = row.get('version') or next_version
        maxima[key] = max(maxima.get(key, 0), version)
        fingerprint = (key[0], key[1], version, row['deviation'])
        if fingerprint in seen_in_batch:
            continue
        seen_in_batch.add(fingerprint)
        resolved.append((row, version))
    return resolved


def _apply_reading(db, row, version, batch_id):
    """把一条读数并入台账，返回动作（duplicate / conflict / supersede / late）。"""
    singer, cue, deviation = row['singer'], row['cue'], row['deviation']
    same_pair = list(db.scalars(
        select(Reading).where(Reading.singer == singer, Reading.cue == cue)))

    identical = next((item for item in same_pair
                      if item.version == version and item.deviation == deviation), None)
    if identical is not None:
        return 'duplicate'

    clash = next((item for item in same_pair if item.version == version), None)
    if clash is not None:
        # 同版本不同偏差：双方搁置，整人等待更高版本
        clash.status = ReadingStatus.CONFLICT
        record = Reading(status=ReadingStatus.CONFLICT, version=version,
                         batch_id=batch_id, **_columns(row))
        db.add(record)
        return 'conflict'

    max_version = max((item.version for item in same_pair), default=0)
    record = Reading(version=version, batch_id=batch_id, **_columns(row))
    if version <= max_version:
        # 迟到的旧版本：只归档，不触碰当前生效读数
        record.status = ReadingStatus.SUPERSEDED
        db.add(record)
        return 'late'

    # 更高版本：取代此前所有生效读数，并解开该提示下被搁置的冲突
    for item in same_pair:
        if item.status != ReadingStatus.SUPERSEDED:
            item.status = ReadingStatus.SUPERSEDED
    record.status = ReadingStatus.ACTIVE
    db.add(record)
    return 'supersede'


def _columns(row):
    return {key: row[key] for key in ('singer', 'part', 'cue', 'deviation', 'sequence')}


def _get_or_create_batch(db, rows, actor, label, explicit_key=None):
    digest = content_hash(rows)
    key = explicit_key or f'batch:{digest}'
    existing = db.scalar(select(ImportBatch).where(ImportBatch.batch_key == key))
    if existing is not None:
        return existing, True
    if db.scalar(select(func.count(Reading.id))) + len(rows) > MAX_READINGS:
        raise BusinessError('当前项目最多保留 5000 条读数', 409)
    batch = ImportBatch(batch_key=key, label=label or f'批次 {digest[:8]}',
                        actor=actor, content_hash=digest)
    db.add(batch)
    db.flush()
    return batch, False


def ingest_rows(db, rows, actor, *, action='reading.imported', label='', explicit_key=None):
    """按批次写入读数，返回导入小结。重试同一批次内容时直接幂等返回。"""
    if not rows:
        raise BusinessError('没有可导入的读数')
    for row in rows:
        row['cue'] = normalize_cue(row['cue'])
    _check_part_consistency(db, rows)
    batch, reused = _get_or_create_batch(db, rows, actor, label, explicit_key)
    if reused:
        return {'batch_id': batch.id, 'replayed': True, 'added': 0, 'duplicate': len(rows),
                'conflict': 0, 'late': 0, 'supersede': 0, 'unblocked': 0}

    tally = defaultdict(int)
    before_blocked = {singer for singer, state in ledger_states(db).items()
                      if state['state'] == SingerState.BLOCKED}
    for row, version in _resolve_versions(db, rows):
        tally[_apply_reading(db, row, version, batch.id)] += 1
    db.flush()
    after_blocked = {singer for singer, state in ledger_states(db).items()
                     if state['state'] == SingerState.BLOCKED}
    unblocked = len(before_blocked - after_blocked)
    added = tally['conflict'] + tally['late'] + tally['supersede']
    db.add(AuditEvent(
        actor=actor, action=action,
        detail=(f'批次 #{batch.id}：新增 {added} 条，重复 {tally["duplicate"]} 条，'
                f'冲突 {tally["conflict"]} 条，迟到归档 {tally["late"]} 条，'
                f'更高版本取代 {tally["supersede"]} 条，解开冲突 {unblocked} 人')))
    try:
        db.flush()
    except IntegrityError as exc:
        raise BusinessError('有相同读数刚刚写入，请刷新后重试', 409) from exc
    return {'batch_id': batch.id, 'replayed': False, 'added': added,
            'duplicate': tally['duplicate'], 'conflict': tally['conflict'],
            'late': tally['late'], 'supersede': tally['supersede'], 'unblocked': unblocked}


def ledger_states(db):
    """按演唱者推导当前配对状态。状态由各提示的最高版本组实时计算。"""
    readings = list(db.scalars(select(Reading).order_by(Reading.singer, Reading.id)))
    grouped = defaultdict(lambda: defaultdict(list))
    for item in readings:
        grouped[item.singer][item.cue].append(item)
    ledger = {}
    for singer, by_cue in grouped.items():
        active = {}
        conflict_cues = []
        for cue, items in by_cue.items():
            top = max(item.version for item in items)
            winners = [item for item in items if item.version == top]
            if len({item.deviation for item in winners}) > 1:
                conflict_cues.append(cue)
            else:
                active[cue] = max(winners, key=lambda item: item.id)
        part = next(iter(by_cue.values()))[0].part
        if conflict_cues:
            state = SingerState.BLOCKED
        elif all(cue in active for cue in (Cue.USUAL, Cue.NEW)):
            state = SingerState.PAIRED
        else:
            state = SingerState.MISSING
        ledger[singer] = {
            'singer': singer, 'part': part, 'state': state,
            'conflict_cues': [LABEL_BY_CUE[cue] for cue in conflict_cues],
            'missing_cues': [LABEL_BY_CUE[cue] for cue in (Cue.USUAL, Cue.NEW) if cue not in active],
            'active': {cue: _reading_brief(item) for cue, item in active.items()},
        }
    return ledger


def _reading_brief(item):
    return {'id': item.id, 'cue': item.cue, 'cue_label': LABEL_BY_CUE[item.cue],
            'version': item.version, 'deviation': item.deviation,
            'sequence': item.sequence, 'status': item.status}


def paired_rows(db):
    """返回 (可入样配对, 被排除演唱者)。配对只取无冲突且两条件齐备的人。"""
    states = ledger_states(db)
    pairs, exclusions = [], []
    for singer in sorted(states):
        entry = states[singer]
        if entry['state'] != SingerState.PAIRED:
            reason = ('读数冲突待更高版本解开：' + '、'.join(entry['conflict_cues'])
                      if entry['state'] == SingerState.BLOCKED
                      else '缺少读数：' + '、'.join(entry['missing_cues']))
            exclusions.append({'singer': singer, 'part': entry['part'],
                               'state': entry['state'], 'reason': reason})
            continue
        usual = entry['active'][Cue.USUAL]
        new = entry['active'][Cue.NEW]
        pairs.append({
            'singer': singer, 'part': entry['part'],
            'sequence': usual['sequence'] or new['sequence'] or '未标注',
            'usual': usual['deviation'], 'new': new['deviation'],
            'usual_version': usual['version'], 'new_version': new['version'],
            'reading_ids': [usual['id'], new['id']],
        })
    return pairs, exclusions


def reading_view(item):
    return {'id': item.id, 'singer': item.singer, 'part': item.part,
            'cue': item.cue, 'cue_label': LABEL_BY_CUE[item.cue],
            'version': item.version, 'deviation': item.deviation,
            'sequence': item.sequence, 'status': item.status,
            'batch_id': item.batch_id, 'created_at': timestamp(item.created_at)}
