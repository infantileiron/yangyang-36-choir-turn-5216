"""读数分批导入、版本冲突、分析快照的业务规则测试。

运行（仓库根目录）：
    PYTHONPATH=/tmp/pylibs:backend python -I tests/test_readings_flow.py
也可用 unittest 发现：python -m unittest discover -s tests
"""
import os
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix='choir-test-')
os.environ.setdefault('DATABASE_URL', f'sqlite:///{_TMP}/test.db')
os.environ.setdefault('REDIS_URL', 'redis://localhost:6399/0')
os.environ.setdefault('SEED_DEMO', 'false')
os.environ.setdefault('ARTIFACT_ROOT', f'{_TMP}/artifacts')
os.environ.setdefault('JWT_SECRET', 'test-secret')

from fastapi.testclient import TestClient  # noqa: E402

from src.constants.enums import Role, RunStatus, SingerState  # noqa: E402
from src.domain.analysis import analyze  # noqa: E402
from src.main import app  # noqa: E402
from src.models.base import Base, Session, engine  # noqa: E402
from src.services.auth import current_user  # noqa: E402
from src.services.readings import ingest_rows, ledger_states, paired_rows  # noqa: E402
from src.services.runs import create_run, run_pending  # noqa: E402
from src.schemas.requests import CreateRun  # noqa: E402

ANALYST = {'sub': 'analyst@demo.local', 'role': Role.ANALYST}
SUPERVISOR = {'sub': 'supervisor@demo.local', 'role': Role.SUPERVISOR}


def reading(singer, cue, deviation, version=None, part='男低音', sequence='常规提示'):
    row = {'singer': singer, 'part': part, 'cue': cue, 'deviation': deviation,
           'sequence': sequence}
    if version is not None:
        row['version'] = version
    return row


USUAL = '常规提示'
NEW = '新提示'


class ServiceFlowTest(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)

    def test_pair_needs_both_cues(self):
        with Session.begin() as db:
            ingest_rows(db, [reading('XA-001', USUAL, 1.4)], 't')
            states = ledger_states(db)
            self.assertEqual(states['XA-001']['state'], SingerState.MISSING)
            pairs, exclusions = paired_rows(db)
            self.assertEqual(pairs, [])
            self.assertEqual(len(exclusions), 1)
            self.assertIn('新提示', exclusions[0]['reason'])

            ingest_rows(db, [reading('XA-001', NEW, 0.6)], 't')
            pairs, exclusions = paired_rows(db)
            self.assertEqual(len(pairs), 1)
            self.assertEqual(exclusions, [])
            self.assertEqual(pairs[0]['usual'], 1.4)
            self.assertEqual(pairs[0]['new'], 0.6)

    def test_auto_version_and_idempotent_retry(self):
        rows = [reading('XA-001', USUAL, 1.4), reading('XA-001', NEW, 0.6)]
        with Session.begin() as db:
            first = ingest_rows(db, rows, 't')
            self.assertEqual(first['added'], 2)
        with Session.begin() as db:
            replay = ingest_rows(db, rows, 't')
            self.assertTrue(replay['replayed'])
            self.assertEqual(replay['added'], 0)
        with Session() as db:
            from sqlalchemy import func, select
            from src.models.workflow import Reading
            self.assertEqual(db.scalar(select(func.count(Reading.id))), 2)

    def test_late_lower_version_never_overwrites(self):
        with Session.begin() as db:
            ingest_rows(db, [reading('XA-001', USUAL, 1.0, version=2)], 't')
            ingest_rows(db, [reading('XA-001', USUAL, 2.0, version=1)], 't')  # 迟到
        with Session() as db:
            states = ledger_states(db)
            self.assertEqual(states['XA-001']['active']['usual']['deviation'], 1.0)
            self.assertEqual(states['XA-001']['active']['usual']['version'], 2)

    def test_conflict_blocks_singer_until_higher_version(self):
        with Session.begin() as db:
            ingest_rows(db, [reading('XA-001', USUAL, 1.0), reading('XA-001', NEW, 0.5)], 't')
            # 同版本 v1 新提示出现不同读数 -> 冲突，整人搁置
            ingest_rows(db, [reading('XA-001', NEW, 0.9, version=1)], 't')
            states = ledger_states(db)
            self.assertEqual(states['XA-001']['state'], SingerState.BLOCKED)
            pairs, exclusions = paired_rows(db)
            self.assertEqual(pairs, [])
            self.assertEqual(exclusions[0]['state'], SingerState.BLOCKED)

            # 更高版本 v2 解开冲突
            ingest_rows(db, [reading('XA-001', NEW, 0.4, version=2)], 't')
            states = ledger_states(db)
            self.assertEqual(states['XA-001']['state'], SingerState.PAIRED)
            self.assertEqual(states['XA-001']['active']['new']['deviation'], 0.4)
            pairs, _ = paired_rows(db)
            self.assertEqual(len(pairs), 1)

    def test_other_singers_still_counted_during_conflict(self):
        with Session.begin() as db:
            ingest_rows(db, [
                reading('XA-001', USUAL, 1.0), reading('XA-001', NEW, 0.5),
                reading('XA-002', USUAL, 1.2), reading('XA-002', NEW, 0.7),
            ], 't')
            ingest_rows(db, [reading('XA-001', NEW, 0.9, version=1)], 't')
            pairs, exclusions = paired_rows(db)
            self.assertEqual([p['singer'] for p in pairs], ['XA-002'])
            self.assertEqual([e['singer'] for e in exclusions], ['XA-001'])

    def test_conflict_arrives_inside_single_batch(self):
        with Session.begin() as db:
            report = ingest_rows(db, [
                reading('XA-001', USUAL, 1.0, version=1),
                reading('XA-001', NEW, 0.5, version=1),
                reading('XA-001', NEW, 0.9, version=1),
            ], 't')
            self.assertEqual(report['conflict'], 1)
            states = ledger_states(db)
            self.assertEqual(states['XA-001']['state'], SingerState.BLOCKED)
            self.assertEqual(states['XA-001']['conflict_cues'], ['新提示'])

    def test_auto_version_increments_within_batch(self):
        with Session.begin() as db:
            report = ingest_rows(db, [
                reading('XA-001', USUAL, 1.0),
                reading('XA-001', USUAL, 0.8),
            ], 't')
            # 第二条应自动成为 v2 并取代 v1，而不是在 v1 上制造冲突
            self.assertEqual(report['supersede'], 2)
            states = ledger_states(db)
            self.assertEqual(states['XA-001']['active']['usual']['version'], 2)
            self.assertEqual(states['XA-001']['active']['usual']['deviation'], 0.8)

    def test_analysis_freezes_snapshot_and_queued_edits_are_isolated(self):
        with Session.begin() as db:
            ingest_rows(db, [
                reading('XA-001', USUAL, 1.0), reading('XA-001', NEW, 0.5),
                reading('XA-002', USUAL, 1.2), reading('XA-002', NEW, 0.7),
            ], 't')
            run = create_run(db, CreateRun(title='首次分析', parameters={}), ANALYST['sub'])
            run_id = run.id
            self.assertEqual(run.sample_size, 2)
            frozen_ids = list(run.reading_ids)

        # 排队期间补录新演唱者，只应影响之后的新分析
        with Session.begin() as db:
            ingest_rows(db, [
                reading('XA-003', USUAL, 0.9), reading('XA-003', NEW, 0.3),
            ], 't')

        with Session.begin() as db:
            self.assertTrue(run_pending(db))
            run = db.get(type(run), run_id)
            self.assertEqual(run.status, RunStatus.SUCCEEDED)
            self.assertEqual(run.sample_size, 2)
            self.assertEqual(list(run.reading_ids), frozen_ids)
            self.assertEqual(len(run.result['pair_rows']), 2)

        # 新分析纳入补录的第三人
        with Session.begin() as db:
            run2 = create_run(db, CreateRun(title='二次分析', parameters={}), ANALYST['sub'])
            self.assertEqual(run2.sample_size, 3)

    def test_part_consistency_enforced(self):
        with Session.begin() as db:
            ingest_rows(db, [reading('XA-001', USUAL, 1.0, part='男低音')], 't')
            from src.services.errors import BusinessError
            with self.assertRaises(BusinessError):
                ingest_rows(db, [reading('XA-001', NEW, 0.5, part='女高音')], 't')

    def test_analyze_rejects_empty_and_duplicate(self):
        with self.assertRaises(ValueError):
            analyze([], {})
        pair = {'singer': 'X', 'part': 'p', 'sequence': '常规提示', 'usual': 1.0, 'new': 0.5}
        with self.assertRaises(ValueError):
            analyze([pair, dict(pair)], {})


class ApiFlowTest(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        self.client = TestClient(app)
        app.dependency_overrides[current_user] = lambda: ANALYST

    def tearDown(self):
        app.dependency_overrides.clear()

    def _csv(self, rows):
        header = 'singer,part,cue,version,deviation,sequence\n'
        body = ''.join(
            f"{r['singer']},{r['part']},{r['cue']},{r.get('version','')},"
            f"{r['deviation']},{r['sequence']}\n" for r in rows)
        return (header + body).encode('utf-8')

    def test_ledger_summary_and_import_idempotency(self):
        payload = self._csv([
            reading('XA-001', USUAL, 1.4), reading('XA-001', NEW, 0.6),
            reading('XA-002', USUAL, 1.2),
        ])
        r1 = self.client.post('/api/readings/import', content=payload,
                              headers={'Content-Type': 'text/csv'})
        self.assertEqual(r1.status_code, 200, r1.text)
        self.assertEqual(r1.json()['added'], 3)
        r2 = self.client.post('/api/readings/import', content=payload,
                              headers={'Content-Type': 'text/csv'})
        self.assertTrue(r2.json()['replayed'])
        self.assertEqual(r2.json()['added'], 0)

        summary = self.client.get('/api/ledger/summary').json()
        self.assertEqual(summary['singers'], 2)
        self.assertEqual(summary['paired'], 1)
        self.assertEqual(summary['missing'], 1)
        self.assertEqual(summary['blocked'], 0)

        ledger = self.client.get('/api/ledger?state=missing').json()
        self.assertEqual([row['singer'] for row in ledger], ['XA-002'])

    def test_conflict_visible_in_ledger_and_run_excludes(self):
        self.client.post('/api/readings/import', content=self._csv([
            reading('XA-001', USUAL, 1.0), reading('XA-001', NEW, 0.5),
            reading('XA-002', USUAL, 1.2), reading('XA-002', NEW, 0.7)]),
            headers={'Content-Type': 'text/csv'})
        self.client.post('/api/readings/import', content=self._csv([
            reading('XA-001', NEW, 0.9, version=1)]),
            headers={'Content-Type': 'text/csv'})
        blocked = self.client.get('/api/ledger?state=blocked').json()
        self.assertEqual(blocked[0]['singer'], 'XA-001')
        self.assertIn('新提示', blocked[0]['conflict_cues'])

        run = self.client.post('/api/runs', json={'title': '含冲突', 'parameters': {}})
        self.assertEqual(run.status_code, 201, run.text)
        detail = self.client.get(f"/api/runs/{run.json()['id']}").json()
        self.assertEqual(detail['sample_size'], 1)
        self.assertEqual(detail['excluded_count'], 1)

    def test_run_rejected_when_no_valid_pair(self):
        self.client.post('/api/readings/import', content=self._csv([
            reading('XA-001', USUAL, 1.0), reading('XA-001', NEW, 0.5)]),
            headers={'Content-Type': 'text/csv'})
        self.client.post('/api/readings/import', content=self._csv([
            reading('XA-001', NEW, 0.9, version=1)]),
            headers={'Content-Type': 'text/csv'})
        run = self.client.post('/api/runs', json={'title': '无人可入样', 'parameters': {}})
        self.assertEqual(run.status_code, 422)


if __name__ == '__main__':
    unittest.main(verbosity=2)
