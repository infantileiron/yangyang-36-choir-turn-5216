# 数据表初始化

本版通过 `src.models.base.Base.metadata.create_all` 创建运行所需的七张表：`import_batches`、`readings`、`analysis_runs`、`reviews`、`schedules`、`deliveries`、`audit_events`。模型在 `src/models/workflow.py`。

## 从整行配对迁移到读数版本

上一版把整行配对存于 `observations` 表；本版改为按「演唱者 + 提示条件 + 版本」管理的 `readings` 表，并由 `import_batches` 记录每次分批导入。应用启动时 `reconcile_schema()` 会检测：若新表 `readings` 尚不存在而旧表 `observations` 仍在（演示环境复用命名卷时会遇到），先丢弃遗留旧表再建新表。演示数据请用 `python -m scripts.seed_demo` 重新生成；该脚本按固定批次键幂等执行，只在没有任何读数时写入，重复运行不覆盖已录入数据。

生产环境如保留旧 `observations` 数据，需先把每行配对拆成 `usual`、`new` 两条版本 1 读数并保留声部与先唱条件，再切换到新结构。
