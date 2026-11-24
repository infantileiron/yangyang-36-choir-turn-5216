# 箫岸合唱·谱架翻页提示试唱

保留翻页处节拍偏差的量级，对同一演唱者的双条件偏差做精确符号翻转检验。

## 模拟场景

[业务剧本](yangyang-business.md)记录团队、上游资料、计算口径和交接去向。业务模块包括同人同谱双测、配对节拍偏差与有符号秩、分声部试唱复核。

观测以 CSV 分批导入或逐行登记，最小单位是「演唱者 + 提示条件 + 版本」的一条节拍偏差读数。两种提示条件的读数齐备才登记为一个配对；同一提示同版本读数冲突时整人搁置，待更高版本解开，迟到的低版本只归档不覆盖。后台按发起分析时冻结的入样配对与排除清单运行 Python 算法；负责人签核后，使用方可查看结果、下载 CSV 或按计划接收报告。

## 启动

```sh
cp .env.example .env
docker compose up -d --build --wait
docker compose exec -T backend python -m scripts.seed_demo
```

浏览器打开 `http://127.0.0.1:18415`。API 健康检查位于 `http://127.0.0.1:19415/api/health`，数据库本地端口 `17915`。停机用 `docker compose down`。只有要重做演示数据时才加 `-v` 清除命名卷。

## 演示账号

| 角色 | 邮箱 | 演示密码 | 可执行动作 |
|---|---|---|---|
| 业务分析员 | `analyst@demo.local` | `Analyst-2026!` | 登记或分批导入读数、提交分析、下载内部结果 |
| 复核负责人 | `supervisor@demo.local` | `Supervisor-2026!` | 导入资料、签核或退回、安排或停止分发、查审计 |
| 只读查看 | `viewer@demo.local` | `Viewer-2026!` | 查看台账、已签核结果和本地收件箱 |

`.env` 以 `.env.example` 为模板。`FRONTEND_PORT`、`BACKEND_PORT`、`DB_PORT` 可做本机端口调整。Docker 私有子网可通过 `NETWORK_SUBNET` 覆盖。`JWT_SECRET` 和 `DB_PASSWORD` 为本地演示配置。`SEED_DEMO=false` 可跳过自动种子。所有公开端口绑定在回环地址。首次执行种子后，后续种子按固定批次键只填缺失读数，不覆盖已录入数据。演示种子包含一名缺读数演唱者（`XA-061`）和一名同版本冲突待解开的演唱者（`XA-062`），用于展示台账的待处理状态。

## 模块和本地开发

| 服务 | 技术 | 用途 |
|---|---|---|
| frontend | HTML、CSS、ES Modules、Nginx | 工作台、录入与报告 |
| backend | Python 3.12、FastAPI、SQLAlchemy | 校验、快照、业务 API 和 JWT |
| worker | Python 3.12、Redis | 计算排队、心跳和到时导出 |
| db | MySQL 8.0 | 观测、快照、签核、分发计划和审计 |
| redis | Redis 7 | 唤醒、心跳和登录限流 |

在 `backend` 中可用 `pip install -r requirements.txt` 安装本地依赖，设置 `DATABASE_URL`、`REDIS_URL` 和可写 `ARTIFACT_ROOT` 后运行 `uvicorn src.main:app --port 8000`，计算进程运行 `python -m src.worker`。前端修改在 `frontend/src`，用 `docker compose up -d --build frontend` 通过 Nginx 检查同源代理。读数与参数定义在 `backend/src/domain/spec.json`，算法在同目录的 `analysis.py`，分批导入、版本与冲突规则在 `backend/src/services/readings.py`。角色、任务状态、签核状态和台账状态在后端 `backend/src/constants/enums.py` 与前端 `frontend/src/constants/enums.js` 共同定义。数据库表在 `backend/src/models/workflow.py`，后端单元测试在 `backend/tests/test_readings_flow.py`。

分发计划锁定特定已签核快照，按北京时间显示。收件箱可下载真实 CSV，当前收件组是本地交接标签。停机跨过多个周期时补送最早未投递的一期，然后追到下一个将来的周期。新快照需重新签核并建新计划。每次发起分析都会冻结当时的入样配对与排除清单，排队期间的补录或更正只影响之后发起的新分析，不改变已入队或已完成的结果。

许可证为 [MIT](LICENSE)。
