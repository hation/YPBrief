# YPBrief实现细节-三级筛选漏斗设计V1.0

| 文档版本 | 创建日期 | 修订简述 |
| --- | --- | --- |
| V1.1 | 2026-09-06 | 依据实际实现更新：三分流、TriageService、Telegram 回复编号、Web UI 待选勾选视图、待选清单推送、Sources 重要性编辑入口均已落地 |
| V1.0 | 2026-09-06 | 首次创建。设计"发现→初筛→人工挑选→总结"三级漏斗，解决关注频道多、全量 LLM 总结 token 消耗过高的问题 |

## 目录

- [1 背景与目标](#1-背景与目标)
- [2 现状分析](#2-现状分析)
- [3 设计概述](#3-设计概述)
- [4 数据模型改动](#4-数据模型改动)
- [5 每日执行流程](#5-每日执行流程)
- [6 便宜 LLM 初筛（控费点）](#6-便宜-llm-初筛控费点)
- [7 挑选交互](#7-挑选交互)
- [8 日报范围与触发时机](#8-日报范围与触发时机)
- [9 错误处理](#9-错误处理)
- [10 测试计划](#10-测试计划)
- [11 源码定位](#11-源码定位)
- [12 备选方案对比](#12-备选方案对比)

## 1 背景与目标

### 1.1 问题

用户关注大量 YouTube 频道，但并非每个视频都值得做全字幕 LLM 总结。现状是 `DigestRunService.run()` 对窗口内**每个新视频**自动做单视频总结（[daily.py:L247-L260](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/daily.py#L247-L260)），1 小时播客 ≈ 1.2 万-1.5 万 token 输入，token 消耗与视频数成正比，浪费在低价值视频上。

### 1.2 目标

- 先低成本获知"有哪些视频 + 基础信息"（标题/频道/时长/日期），**零 token**
- 通过"频道重要性 + 标题级初筛 + 人工挑选"三层过滤，只对重点视频做全字幕总结
- 日报只收纳入选视频，未选的跨天保留、可再挑、不重复花 token

### 1.3 成功标准

- 普通/低优频道的新视频在"被发现"阶段不触发任何全字幕总结调用
- 待选清单可推送到 Telegram/飞书，且支持在 Telegram 回复编号或 Web UI 勾选
- 日报只包含"重要频道自动总结 + 人工选中"的视频
- 历史数据库升级后（已有数据）不丢数据、功能可用

## 2 现状分析

| 已有能力 | 位置 | 说明 |
| --- | --- | --- |
| 视频元数据先于总结入库 | [database.py:L85-L103](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/database.py#L85-L103) | Videos 表有 title/url/date/duration，`status` 默认 `new`，总结前就存在 |
| "发现但不总结"开关 | [daily.py:L250](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/daily.py#L250) | `process_missing_videos=False` 时无总结视频被记为 skipped |
| 单视频手动总结 | [app.py:L1168](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief_api/app.py#L1168) | POST `/api/videos/{video_id}/summarize` |
| 视频列表 | [app.py:L1051](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief_api/app.py#L1051) | GET `/api/videos`，含 status / has_transcript |
| Telegram Bot 收消息 | [app.py:L1081](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief_api/app.py#L1081) | 已有 webhook，支持"发链接→总结"，可扩展为"回复编号选择" |
| 提示词模板体系 | [prompts.py:L16](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/prompts.py#L16) | `DEFAULT_PROMPTS` 支持 `video_summary` / `daily_digest`，可加 `video_triage` |

### 2.1 缺口

1. Sources 无"重要性"字段
2. Videos 无"是否被选中/待选"状态
3. 无标题级初筛（便宜 LLM）步骤
4. 无"批量总结选中视频"入口
5. Telegram webhook 只支持"发链接"，不支持"回复编号选择"

## 3 设计概述

```
① 每日自动发现新视频（YouTube API，无 token，只入库元数据）
② 按 Sources.importance 分流：
   ├─ important → 直接单视频总结（自动，行为不变）
   ├─ normal    → 便宜 LLM 初筛标题 → 标记 pending + triage_score → 进待选清单
   └─ low       → 仅记录标题和链接，不执行总结
③ 推送待选清单：Telegram 编号版 / 其他渠道（飞书）带 Web UI 链接
④ 人工挑选：Telegram 回复编号 或 Web UI 勾选 → 标记 selected → 立即单视频总结
⑤ 日报合成：只纳入「重要频道自动总结 + 人工选中」的视频
⑥ 未选的留在 pending，跨天保留，可再挑，不重复花 token
```

## 4 数据模型改动

### 4.1 Sources 增加 `importance`

| 字段 | 类型 | 取值 | 默认 |
| --- | --- | --- | --- |
| `importance` | TEXT | `important` / `normal` / `low` | `normal` |

- `important`：新视频自动全字幕总结（现状行为）
- `normal`：只发现 + 初筛 → 待选清单
- `low`：仅记录标题和链接，不执行总结

> 已实现编辑入口：Web UI 来源表新增「重要性」列（`important/normal/low` 三档下拉，切换即保存，走 `PATCH /api/sources/{id}`，对应 `db.update_source(..., importance=...)`）。未设置时默认 `normal`。

### 4.2 Videos 增加筛选状态字段

| 字段 | 类型 | 取值 | 说明 |
| --- | --- | --- | --- |
| `selection_status` | TEXT | `auto` / `pending` / `selected` / `dismissed` | `auto`=重要频道已自动总结；`pending`=待选；`selected`=已选中；`dismissed`=用户忽略 |
| `triage_score` | REAL | NULL / 1-5 | 便宜 LLM 初筛推荐分，NULL 表示未初筛 |
| `triage_at` | TEXT | NULL / 时间戳 | 初筛时间 |

### 4.3 迁移策略

`database.py` 的 `initialize()` 目前用 `CREATE TABLE IF NOT EXISTS`（[database.py:L50-L103](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/database.py#L50-L103)）。新列需兼容已有库：

- 新库：把新列直接写入建表语句
- 旧库：`initialize()` 内做幂等迁移——`PRAGMA table_info` 检查列是否存在，不存在则 `ALTER TABLE ... ADD COLUMN`（SQLite 支持 ADD COLUMN）

### 4.4 新提示词 `video_triage`

在 `DEFAULT_PROMPTS` 新增 `video_triage`（[prompts.py:L16](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/prompts.py#L16)）：

- 输入：一批视频的标题/频道/时长/日期（**不含字幕**），每条约 30-50 token
- 输出：每条的推荐分 1-5 + 一句话理由
- 一次批量 20 条，单次调用几千 token，远小于逐条全字幕总结

## 5 每日执行流程

改造 [DigestRunService.run](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/daily.py#L187)（或抽独立 `TriageRunService`，见 11.2）：

```
1. 发现阶段（现有 _discover 逻辑不变，无 token）
2. 分流阶段（新增）：
   for each 新视频:
     importance = source.importance
     if importance == 'important':
         process 全字幕总结（现有逻辑）
     elif importance == 'normal':
         收集待初筛批次
     else:  # low
         仅记录标题和链接，selection_status='pending'，不执行总结
3. 初筛阶段（仅 normal 批次，新增）：
   批量调用 video_triage 提示词 → 写回 triage_score / selection_status='pending'
4. 待选清单生成（新增）：
   汇总当日 selection_status='pending' 的视频 → 生成清单 Markdown
5. 推送（复用 delivery.py）：
   - Telegram：编号列表 + "回复 1 3 5 选择"
   - 飞书/Email：清单 + Web UI 待选页链接
6. 日报：仅对「已总结的视频（auto + selected）」调用 summarize_videos
```

## 6 便宜 LLM 初筛（控费点）

### 6.1 目的

只对 normal 频道做一次"标题级"初筛，用最便宜的输入模型，把"要不要花全字幕钱"的判断从人工粗筛变成带推荐的清单。

### 6.2 配置

| 配置 | 说明 |
| --- | --- |
| 独立模型配置 | 复用现有 Provider 抽象，可用独立 `*_MODEL`（如 `TRIAGE_MODEL`）指向便宜档；未配置时回退到当前生效 LLM |
| 提示词 | `video_triage`（见 4.4），输入不含字幕 |
| 批量大小 | 20 条/次，上限防超上下文 |

### 6.3 成本估算

假设普通频道每天 20 个新视频，批量 1 次调用 ≈ 1-2k token 输入 + 0.5k 输出，比"20 条全字幕总结"（≈ 24 万-30 万 token）低两个数量级。

## 7 挑选交互

### 7.1 Telegram 回复编号（复用已有 webhook）

扩展 [telegram_webhook](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief_api/app.py#L1081)：

- 消息解析：若文本匹配 `^\d+(\s*,\s*\d+)*$`（如 `1 3 5`、`1,3,5`），视为"选择当日待选清单中的编号"
- 把对应 video_id 标记 `selected` → 逐个触发单视频总结（复用 `_process_video_url` / `Summarizer`）
- 回复确认："已选中 N 条，正在总结..."
- 无编号且无链接的消息仍回复原引导文案

### 7.2 Web UI 勾选（已实现）

视频页新增第三分段视图「待选视频」（`mode='triage'`），与「阅读/维护」并列：

- 数据源：`GET /api/triage/candidates` 拉取 `selection_status='pending'` 的视频（不受主列表 200 条上限影响）
- 列表行：勾选框 + 标题/频道/日期 + ★初筛分数（`triage_score`）
- 工具栏：全选、已选计数、「总结选中」、「忽略选中」
- 端点：
  - `POST /api/videos/select`（body: `video_ids[]`, `action: selected|dismissed`）
  - `POST /api/videos/summarize-selected`（批量标 `selected` + 逐个总结，单条失败不中断其他，返回 `summarized/failed` 计数）
- 详情面板在待选视图同样提供「完整处理/重新总结」按钮
- `GET /api/videos` 列表已返回 `selection_status` / `triage_score` 字段

### 7.3 其他渠道

飞书/Email 推送带 Web UI 待选页 URL；无法内联交互，一律引导到 Web UI。

## 8 日报范围与触发时机

### 8.1 日报范围

`DailyDigestService.summarize_videos`（[daily.py:L64](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/daily.py#L64)）只接收显式 video_ids，天然只收录"有总结"的视频。改造后 `unique_included` 只包含 `auto + selected`，未选/忽略的不进日报。

### 8.2 触发时机

- 人工选择后**立即**触发对应视频总结（Telegram 回复 / Web UI 勾选即执行）
- 日报合成时机保持现状：当日 run 结束时若 `unique_included` 非空则合成

### 8.3 未选视频

- `selection_status='pending'` 跨天保留
- 下次 run 的发现阶段若该视频仍在窗口内且未总结，仍可再次进入待选清单（需去重，避免重复推送——见 9）

## 9 错误处理

| 场景 | 处理 |
| --- | --- |
| 初筛 LLM 调用失败 | 该批次标记 pending 但不写分数，仍进待选清单（人工可兜底），记日志 |
| Telegram 编号选择超界/非法 | 回复"编号无效，请回复 1-N" |
| 选中视频总结失败 | 复用现有 `_retry_digest_run_video` / run 的 failed 记录 |
| 同一天重复推送同一 pending 视频 | 清单生成时按 `triage_at/discovered_at` 去重，同一 run 内只推一次 |
| 数据库迁移失败 | 迁移放在 `initialize()` 内，逐列 try/except，失败仅告警不阻断启动 |

## 10 测试计划

| 测试 | 说明 |
| --- | --- |
| 迁移测试 | 旧库（无新列）initialize 后列存在、数据不丢 |
| 分流测试 | 三种 importance 分别走 自动总结 / 初筛 / 仅记录标题和链接 |
| 初筛测试 | video_triage 提示词渲染、批量解析、写回 triage_score |
| Telegram 编号解析 | `1 3 5` / `1,3,5` / 非法编号 |
| 选择-总结链路 | 选中 → selected → 总结 → 进日报 |
| 去重测试 | 同视频跨 run 不重复推送 |
| Web UI 端点 | `/api/videos/select` 批量接口单测 |

## 11 源码定位

### 11.1 主要改动文件

| 文件 | 改动 |
| --- | --- |
| [database.py](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/database.py) | 建表加列、幂等迁移、`upsert_source`/`update_source` 支持 importance、视频筛选状态访问方法 |
| [daily.py](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/daily.py) | `DigestRunService` 三分流 + 初筛触发 + 候选清单返回 + `triage_service` 注入 |
| [prompts.py](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/prompts.py) | 新增 `video_triage` 默认提示词 |
| [triage.py](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/triage.py) | `TriageService`：标题级批量初筛 + JSON 解析（新建） |
| [scheduler.py](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/scheduler.py) | 自动任务结束后推送待选清单（`send_triage_list`） |
| [app.py](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief_api/app.py) | Telegram 编号选择、`GET /api/triage/candidates`、`POST /api/videos/select`、`POST /api/videos/summarize-selected`、`PATCH /api/sources/{id}` 支持 importance |
| [delivery.py](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/delivery.py) | `send_triage_list` 待选清单渲染与推送 |
| [web/src/App.tsx](file:///Users/xingan/Documents/software/aiengine/ypbrief/web/src/App.tsx) | 「待选视频」勾选视图 + 来源「重要性」列 |
| [web/src/types.ts](file:///Users/xingan/Documents/software/aiengine/ypbrief/web/src/types.ts) | `Source.importance`、`Video.selection_status/triage_score/duration`、`VideoMode` 增加 `triage` |
| [web/src/App.css](file:///Users/xingan/Documents/software/aiengine/ypbrief/web/src/App.css) | 勾选行/工具栏/重要性下拉样式 |

### 11.2 结构选择

优先**在 DigestRunService.run 内扩展**（改动集中在 daily.py），而非新建 TriageRunService，避免调度/Web UI 双入口都要感知新服务；若 run 方法膨胀到难以维护，再拆 `VideoTriageService`（只负责 分流+初筛+清单）。

## 12 备选方案对比

| 方案 | 优点 | 缺点 | 结论 |
| --- | --- | --- | --- |
| 三级漏斗（本设计） | 重要频道零感知；普通频道有推荐；低优频道零 token | 需加字段与 UI，改动中等 | ✅ 采纳 |
| 两级（重要/普通） | 改动最小 | 普通频道全进待选，清单冗长，无推荐 | 不采纳 |
| 不分级全人工 | 最省 token | 清单极长、挑选负担重，违背"自动发现"初衷 | 不采纳 |
| 全自动按规则（时长/关键词） | 零额外 token | 机械、误判多，仍需人工确认 | 作为初筛辅助可选 |

## 13 实施状态

V1.0 设计已按实施计划全部落地并合入 main，共 8 个提交：

| 提交 | 内容 |
| --- | --- |
| `0d45a56` | db：triage 列 + importance 分级 + 幂等迁移 |
| `8128c35` | triage：`video_triage` 提示词 + `TriageService` |
| `850887a` | daily：`DigestRunService` 三分流 + 候选清单 |
| `9700751` | api：待选查询 + `POST /api/videos/select` |
| `f96d517` | api：Telegram 回复编号选择 |
| `1301811` | delivery：自动任务后推送待选清单 |
| `f4fa245` | web：待选勾选视图 + `POST /api/videos/summarize-selected` |
| `fc6933f` | web：来源「重要性」编辑入口 |

验证：后端全量测试 **243 passed**（新增约 15 个用例），前端 `npm run build` 通过。

### 已知边界

- 既有来源默认 `normal`，升级后不再自动总结，需在来源页把重点频道改为「重要·自动总结」
- 待选视图展示跨天累积的 `pending` 视频；Web UI 无去重（依赖 run 内按发现时间去重推送）
- Telegram 编号选择需 `TELEGRAM_BOT_INBOX_ENABLED=true` + 公网 HTTPS 才能用
