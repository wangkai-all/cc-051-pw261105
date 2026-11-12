# 热键分区实验室 · Hot-key Partition Lab

验证「少数重复键把关联分区挤满」这个问题：先找出热键，试算怎么拆更合适，再实际写出分区 Parquet、逐桶做等值内连接，并用 DuckDB 直接内连接逐对校验结果。

## 运行

需要 Python 3.11+ 与 Node.js 20+。

```bash
# 后端（FastAPI + DuckDB + PyArrow），默认 http://127.0.0.1:8000
cd backend
python -m venv ../.venv && ../.venv/bin/pip install -r requirements.txt
../.venv/bin/python run.py

# 前端（React + Vite），默认 http://127.0.0.1:5173，/api 代理到 8000
cd frontend
npm install
npm run dev
```

环境变量：`LAB_PORT`（后端端口，默认 8000）、`LAB_HOST`（默认 127.0.0.1）、
`LAB_DATA_DIR`（数据目录，默认仓库根目录下 `data/`）、`LAB_MAX_ROWS`（上传行数上限，默认 2,000,000）。

启动时会自动生成内置样例数据（若不存在）。

## 内置样例

样例是确定性的，覆盖题目要求的四类情况：

| 情况 | 内容 |
| --- | --- |
| 明显热键 | `HOT-VIP` 左 180 / 右 60（10,800 行关联）、`HOT-API` 左 56 / 右 35（1,960 行） |
| 普通键 | `CUST-1001`、`CUST-1002`、`CUST-3001`、`CUST-3002`、`CUST-3003`（1–7 行） |
| 重复业务记录 | `payload` 每侧按模 3 / 模 4 重复，行编号仍唯一 |
| 空键 | 左侧 6 NULL + 4 空字符串，右侧 3 NULL + 2 空字符串 |

左表 260 行、右表 112 行，可关联 12,807 对。普通 hash 分区（P=8）下最大分区 258 行；
把 HOT-VIP 按 4 个盐桶拆开后最大桶 191 行，右表复制 180 行，写入量 ×1.48。

## 规则

- 只做等值内连接。空键（NULL 或空字符串）按 SQL 规则互不匹配，单独统计并在关联前排除。
- 左右两侧各有稳定、唯一的 `row_id`；业务内容可重复，行编号不可重复（上传时会校验）。
- **普通方案**：两侧都按 `hash(join_key) % P` 稳定散列分区。
- **热键方案**：左侧热键行按 `hash(row_id) % S` 分到 S 个盐桶；右侧同键行复制到每个桶；
  桶 b 只与桶 b 关联，因此每一对原本可关联的记录恰好出现一次。
  非热键键先按 `hash(key) % S` 选一个盐批次，再按 `hash(key) % ceil(P/S)` 落到该批次的普通分区，
  保证同键两侧仍落在同一分区。
- 试算给出每桶行数、最大桶、复制增加的行数与字节、写入量倍数、左右频次相乘的关联行数。
- 执行后校验：结果与「DuckDB 直接内连接」按 `(left_row_id, right_row_id)` 做双向 EXCEPT，
  并统计缺失/多出/重复的对、空键是否漏入、左右写入行数是否等于源行数（右侧允许等于源行数 + 复制行数）。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查与数据目录 |
| GET | `/api/datasets` | 数据集列表 |
| POST | `/api/datasets/sample` | 重建内置样例 |
| POST | `/api/datasets/upload` | multipart 上传 `left` / `right` Parquet |
| GET | `/api/datasets/{id}/keys` | 键频次（`q` 搜索、`sort=hot\|pairs`） |
| POST | `/api/plan` | 试算当前方案 |
| POST | `/api/plan/compare` | 同一热键下多盐桶方案对比（含不拆的对照） |
| POST | `/api/execute` | 写分区 Parquet、逐桶关联并校验 |
| GET | `/api/jobs/{id}` | 作业详情（文件、逐桶行数、校验） |
| GET | `/api/jobs/{id}/keys/{key}` | 单个键（含 `__empty__`）的分桶与复制明细 |
| GET | `/api/jobs/{id}/result`、`/baseline` | 关联结果样例、直接内连接基准样例 |

## 测试

```bash
cd backend
../.venv/bin/python -m pytest -q
```

10 项测试，覆盖：试算桶行数与实际写出一致、盐桶结果与 DuckDB 基准逐对一致（含重复次数）、
热键 180 条左记录在 4 个桶中各出现一次、空键不丢行也不进入结果、单侧热键复制为 0 但结果仍一致、
非热键拒绝拆分、重复 `row_id` 拒绝上传。

## 目录

```
backend/app/    sample_data.py  dataset.py  plan.py  engine.py  keys.py  main.py
backend/tests/  test_lab.py
frontend/src/   App.tsx  api.ts  types.ts  format.ts  components/
data/           运行时生成（数据集与作业分区文件，已被 .gitignore 忽略）
```
