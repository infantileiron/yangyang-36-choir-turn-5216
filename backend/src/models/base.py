from datetime import datetime, timezone

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from src.config.runtime import DSN


class Base(DeclarativeBase):
    pass


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


engine = create_engine(DSN, pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)

# 上一版以整行配对的 observations 表存储数据；本版改为按提示条件与版本管理的 readings。
# 演示环境复用命名卷，若检测到旧表且新表尚未建立，则丢弃遗留表后重建。
_LEGACY_TABLES = ('observations',)


def reconcile_schema():
    inspector = inspect(engine)
    present = set(inspector.get_table_names())
    if 'readings' not in present and present.intersection(_LEGACY_TABLES):
        with engine.begin() as connection:
            for name in _LEGACY_TABLES:
                if name in present:
                    connection.exec_driver_sql(f'DROP TABLE IF EXISTS {name}')
