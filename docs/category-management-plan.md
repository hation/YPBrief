# 频道分类管理需求与实施计划

> 状态：待确认 / 待实施（用户暂离，后续根据本文档执行）
> 创建日期：2026-09-04
> 关联仓库：https://github.com/hation/YPBrief

## 1. 背景与目标

用户对关注的 YouTube 频道做「分类」管理：

1. 一个分类下挂若干频道，分类可独立开关、独立生成简报。
2. 当前只关注 **AI** 分类，本次把 AI 频道内容补齐。
3. 后续新增 **跑步健身** 分类（含频道），中英文都要覆盖。
4. 每个分类有独立简报参数（标题、语言、运行时间、时区、每源最多视频数），可分开推送。

## 2. 已确认的设计决策

| 决策点 | 结论 |
| --- | --- |
| 管理形式 | 新建 `categories.yaml` 作为分类与频道的唯一主文件 |
| AI 分类 | 现在补齐频道，先列清单给用户确认 |
| 跑步健身频道来源 | 我推荐国内外频道 + 用户提供自己的清单，中英文都要 |
| 简报参数 | 每个分类独立配置 |
| 接入方式 | 写同步脚本 `scripts/sync_categories.py`：`categories.yaml` → 现有系统使用的 `sources.yaml`（GitHub Actions 与 Web UI 导入均读 `sources.yaml`，见 `scripts/github_actions_daily.py` 与 `src/ypbrief_api/app.py` 的 import/save 接口） |

## 3. 方案设计

### 3.1 categories.yaml（新主文件，仓库根目录）

```yaml
categories:
  - name: ai                  # 分类唯一标识
    display_name: AI
    description: AI 领域频道
    digest_title: AI 每日简报
    digest_language: zh
    run_time: "07:00"
    timezone: Asia/Shanghai
    max_videos_per_source: 10
    sources:
      - name: Andrej Karpathy
        display_name: Andrej Karpathy
        url: https://www.youtube.com/@AndrejKarpathy
        type: channel
        enabled: true
  - name: running_fitness      # 后续新增分类
    display_name: 跑步健身
    ...
```

要点：
- `categories` 为顶层列表；每个分类的字段与现有 `sources.yaml` 的 `groups` 字段对齐（`group_name` ↔ `name`，其余如 `display_name`、`digest_title`、`digest_language`、`run_time`、`timezone`、`max_videos_per_source` 保持一致）。
- 频道挂在分类的 `sources` 子列表下，字段与现有 `sources` 条目对齐（`name`、`display_name`、`url`、`type`、`enabled`）。
- 后续新增分类只需往 `categories.yaml` 加一个 `- name: ...` 块。

### 3.2 同步脚本 scripts/sync_categories.py

- 读取根目录 `categories.yaml`。
- 生成现有系统可直接使用的 `sources.yaml`（格式参考 `sources.example.yaml`：顶层 `groups:` + `sources:`，`sources` 中每项带 `group:` 指向分类）。
- 幂等、可重复执行；执行后提示 `sources.yaml` 已更新，可在 Web UI 导入或由 GitHub Actions 读取。
- 校验：分类名重复、频道 url 缺失、`type` 仅允许 channel/playlist/video 等，与现有校验保持一致（参考 `src/ypbrief/sources.py`）。

### 3.3 文档与规则同步（实施时一并做）

- README.md / README-en.md：补充 categories.yaml 维护说明。
- `.trae/rules/project_rules.md`：补充「分类与频道维护」规则（如何新增分类、如何同步）。

## 4. 初始内容：AI 分类频道清单（候选，待用户勾选）

> 注：以下 handle 为搜索所得，实施前需在 YouTube 逐一验证真实 handle（例如 Karpathy 有 @karpathy / @AndrejKarpathy 两种写法、AI Explained 显示 @aiexplained-official 或 @ai_explained）。

### 4.1 英文（12 个候选）

| # | 频道 | handle（待验证） | 定位 |
| --- | --- | --- | --- |
| 1 | Andrej Karpathy | @AndrejKarpathy | LLM 底层原理、从零写 GPT |
| 2 | DeepLearning.AI（吴恩达） | @DeepLearningAI | 系统课程 |
| 3 | Lex Fridman | @lexfridman | AI 长访谈 |
| 4 | Dwarkesh Patel | @DwarkeshPatel | 技术深度访谈 |
| 5 | Yannic Kilcher | @YannicKilcher | 论文精读 |
| 6 | Two Minute Papers | @TwoMinutePapers | 论文速览 |
| 7 | AI Explained | @aiexplained-official | AI 趋势解读 |
| 8 | Hugging Face | @HuggingFace | 开源模型工程教程 |
| 9 | OpenAI 官方 | @OpenAI | 产品/模型动态 |
| 10 | Anthropic 官方 | @anthropic-ai | AI 安全研究 |
| 11 | Google DeepMind 官方 | @googledeepmind | 前沿研究 |

### 4.2 中文（1 个候选）

| # | 频道 | handle（已验证） | 定位 |
| --- | --- | --- | --- |
| 1 | 李宏毅 Hung-yi Lee | UC2ggjtuuWvxrHHHiaDH1dlQ | 华语 AI 学术教学 |

## 5. 跑步健身分类（待补充）

### 5.1 英文（候选，待用户勾选）

- The Running Channel（入门综合）
- Ben Parkes（马拉松训练）
- The Run Experience（跑姿/防伤）
- Strength Running（训练科学）
- Global Triathlon Network（铁三/跑步）
- Total Running Productions（精英赛事/纪录片）
- Kofuzi（跑鞋测评）
- CITIUS MAG（精英赛事新闻）

### 5.2 中文（较少，待用户提供清单）

- 初步仅筛到「陈洪龍42.195的熱血」等跑者频道。
- ⚠️ 注意：YPBrief 只支持 YouTube 频道；B 站/小宇宙播客（如山雨小月）需有 YouTube 镜像才能接入。
- 需用户提供自己的中文频道清单，实施时再验证 handle。

## 6. 待确认事项（用户回来时确认）

1. AI 分类：上面 12 个英文 + 1 个中文频道，哪些保留、哪些删、是否补充。
2. 跑步健身：英文先上哪几个；中文频道用户提供清单。
3. 方案设计（categories.yaml + sync 脚本）是否 OK。
4. 是否照旧提交推送到线上。

## 7. 实施步骤（后续依据此执行）

1. 创建根目录 `categories.yaml`，写入确认后的 AI 分类及频道。
2. 创建 `scripts/sync_categories.py`，实现 `categories.yaml` → `sources.yaml` 转换。
3. 生成 `sources.yaml` 并人工核对。
4. 更新 README.md / README-en.md，补充 categories.yaml 维护说明。
5. 更新 `.trae/rules/project_rules.md`，补充分类与频道维护规则。
6. 运行相关测试（至少 `test_sources.py`、`test_github_actions_daily.py`、`test_api.py` 的导入导出相关用例）。
7. 提交并推送到 `origin/main`（若用户确认）。

## 8. 参考资料

- 现有示例：`sources.example.yaml`
- 来源解析与校验：`src/ypbrief/sources.py`
- Actions 读取：`scripts/github_actions_daily.py`
- Web UI 导入导出：`src/ypbrief_api/app.py`（`import_sources` / `save_sources`）
- 本地全量日报脚本（已入项目规则）：`scripts/run_digest_local.py`
