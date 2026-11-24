import json
import logging

from sqlalchemy import func, select

from src.config.project import SPEC
from src.constants.enums import Decision, Role, RunStatus
from src.domain.analysis import PAIR_COLUMNS, analyze, pair_detail_rows
from src.models.base import utc_now
from src.models.workflow import AnalysisRun, AuditEvent, Reading, Review
from src.services.errors import BusinessError
from src.services.readings import paired_rows
from src.services.validation import validated

logger = logging.getLogger(__name__)


def create_run(db, request, actor):
    parameters = validated(SPEC['parameters'], request.parameters, defaults=True)
    pairs, exclusions = paired_rows(db)
    if not pairs:
        raise BusinessError('还没有两种提示读数齐备且无冲突的配对，请先在数据台账补齐读数')
    last_reading_id = db.scalar(select(func.max(Reading.id))) or 0
    reading_ids = sorted(reading_id for pair in pairs for reading_id in pair['reading_ids'])
    item = AnalysisRun(
        title=request.title, owner=actor, parameters=parameters,
        pairs=pair_detail_rows(pairs), exclusions=exclusions,
        reading_ids=reading_ids, last_reading_id=last_reading_id, sample_size=len(pairs))
    db.add(item)
    db.flush()
    db.add(AuditEvent(
        actor=actor, action='run.created',
        detail=(f'分析 #{item.id}，冻结 {item.sample_size} 个入样配对，'
                f'排除 {len(exclusions)} 名演唱者（快照末号 {last_reading_id}）')))
    return item


def visible_runs(db, user, run_id=None):
    query = select(AnalysisRun, Review).outerjoin(Review, Review.run_id == AnalysisRun.id)
    if user['role'] == Role.VIEWER:
        query = query.where(Review.decision == Decision.APPROVED)
    if run_id is not None:
        pair = db.execute(query.where(AnalysisRun.id == run_id)).first()
        if pair is None:
            raise BusinessError('没有可查看的分析结果', 404)
        return pair
    return db.execute(query.order_by(AnalysisRun.id.desc()).limit(100)).all()


def submit_review(db, run_id, request, actor):
    row = db.get(AnalysisRun, run_id, with_for_update=True)
    if row is None:
        raise BusinessError('分析不存在', 404)
    if row.status != RunStatus.SUCCEEDED:
        raise BusinessError('分析算完后才能复核', 409)
    if db.scalar(select(Review).where(Review.run_id == run_id)):
        raise BusinessError('这份分析已完成复核。请发起新分析并重新复核。', 409)
    if row.owner == actor:
        raise BusinessError('分析须由另一位负责人复核', 409)
    review = Review(run_id=run_id, decision=request.decision, note=request.note, reviewer=actor)
    db.add(review)
    db.add(AuditEvent(actor=actor, action=f'review.{request.decision}', detail=f'分析 #{run_id}'))
    db.flush()
    return review


def run_pending(db):
    item = db.scalar(select(AnalysisRun).where(AnalysisRun.status == RunStatus.QUEUED)
                     .order_by(AnalysisRun.id).limit(1).with_for_update(skip_locked=True))
    if item is None:
        return False
    try:
        # 只使用发起时冻结的配对，排队期间补录或更正不会改动本分析
        result = analyze(item.pairs, item.parameters)
        # 附带冻结的逐行配对明细与被排除演唱者，保证 CSV 与台账口径一致
        result['pair_columns'] = [{'key': key, 'label': label} for key, label in PAIR_COLUMNS]
        result['pair_rows'] = item.pairs
        result['exclusions'] = item.exclusions
        if not {'headline', 'summary', 'metrics', 'columns', 'rows'} <= set(result):
            raise RuntimeError('invalid result schema')
        json.dumps(result, allow_nan=False, ensure_ascii=False)
        item.result = result
        item.status = RunStatus.SUCCEEDED
    except ValueError as exc:
        item.error = str(exc)[:600] or '本次观测数据不足以完成计算'
        item.status = RunStatus.FAILED
    except Exception:
        logger.exception('Analysis %s raised unexpectedly', item.id)
        item.error = '分析计算失败，请检查记录和参数后重试'
        item.status = RunStatus.FAILED
    item.finished_at = utc_now()
    return True
