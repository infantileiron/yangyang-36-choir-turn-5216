import json

from sqlalchemy import func, select

from src.domain.analysis import seed
from src.models.base import Base, Session, engine
from src.models.workflow import AnalysisRun, ImportBatch, Reading, Review, Schedule
from src.services.readings import clean_reading, ingest_rows

SEED_BATCH_KEY = 'batch:seed-demo-v2'


def seed_data():
    Base.metadata.create_all(engine)
    rows = [clean_reading(row) for row in seed()]
    with Session.begin() as db:
        existing_seed = db.scalar(select(ImportBatch).where(ImportBatch.batch_key == SEED_BATCH_KEY))
        if existing_seed is None and db.scalar(select(func.count(Reading.id))) == 0:
            report = ingest_rows(db, rows, 'seed', action='reading.imported',
                                 label='演示种子读数', explicit_key=SEED_BATCH_KEY)
        else:
            report = {'replayed': True, 'added': 0}
        db.flush()
        counts = {model.__tablename__: db.scalar(select(func.count(model.id)))
                  for model in (Reading, ImportBatch, AnalysisRun, Review, Schedule)}
        counts['seed'] = report
        return counts


if __name__ == '__main__':
    print(json.dumps(seed_data(), sort_keys=True, ensure_ascii=False, default=str))
