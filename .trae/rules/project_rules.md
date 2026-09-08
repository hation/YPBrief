# YPBrief 项目规则

## 分类与频道维护（categories.yaml）

### 用途

仓库根目录的 `categories.yaml` 是分类与频道的唯一事实源（单文件管理「分类 → 频道」）。`sources.yaml`（GitHub Actions 与 Web UI 导入读取）由它生成，不要手改 `sources.yaml`。

### 维护流程

1. 编辑根目录 `categories.yaml`：新增/调整分类（含独立简报参数）或频道。
2. 运行同步脚本重新生成 `sources.yaml`：

```bash
.venv/bin/python scripts/sync_categories.py
```

3. 核对生成的 `sources.yaml` 分组与频道数量、URL 正确。

### categories.yaml 结构

```yaml
categories:
  - name: ai                  # 分类唯一标识（须唯一、非空）
    display_name: AI
    description: AI 领域频道
    digest_title: AI 每日简报   # 分类独立简报参数
    digest_language: zh
    run_time: "07:00"
    timezone: Asia/Shanghai
    max_videos_per_source: 10
    sources:
      - name: Andrej Karpathy
        display_name: Andrej Karpathy
        url: https://www.youtube.com/@AndrejKarpathy
        type: channel            # channel / playlist / video
        enabled: true
```

### 校验规则（sync 脚本内置）

- 分类 `name` 非空且唯一；`sources` 必须为列表。
- 每个频道必须含 `url`（或 `id`）。
- `type` 仅允许 `channel` / `playlist` / `video`。

### 注意事项

- 新增分类只需追加一个 `- name: ...` 块；每个分类的简报参数相互独立。
- YPBrief 只支持 YouTube；B 站 / 小宇宙播客需有 YouTube 镜像才能接入。
- 新增频道前先验证 YouTube handle 真实存在。

## 本地运行完整日报（不启动 Web UI）

### 用途

Web UI 的“立即运行”和后台定时任务走的是 `DigestRunService.run()` 完整流水线；而命令行 `ypbrief daily summarize` 只能按 id 合并已经总结过的视频。如果不想启动 Web UI，用仓库里的脚本 `scripts/run_digest_local.py` 可直接跑完整流水线。

脚本从本地 SQLite 读取启用中的来源，走完整流程：

```text
发现新视频 -> 抓取字幕 -> LLM 单视频总结 -> 合成每日简报 -> 可选推送 Telegram / 飞书 / Email
```

### 运行前提

- 已初始化数据库（`ypbrief --env-file key.env init-db`）
- `key.env` 中已配置 `YOUTUBE_DATA_API_KEY` 和 LLM provider（`LLM_PROVIDER` / `LLM_MODEL` / 对应 API key）
- 需要在本地先有 `sources`（例如 `ypbrief source add <url>`）

### 基本用法

```bash
.venv/bin/python scripts/run_digest_local.py --window last_7 --env-file key.env
```

### 参数说明

| 参数 | 取值 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--env-file` | 文件路径 | `key.env` | 配置文件路径 |
| `--window` | `last_1` / `last_3` / `last_7` / `all_time` | `last_1` | 新视频回溯窗口 |
| `--run-date` | `YYYY-MM-DD` | 今天 | 指定摘要日期 |
| `--group` | 分组名 | 全部 | 只处理某个来源分组 |
| `--max-videos-per-source` | 整数 | `10` | 每个来源最多处理的视频数 |
| `--language` | `zh` / `en` | `zh` | 简报语言 |
| `--send-empty` | 开关 | 关 | 当天没有新视频时也推送“无更新”通知 |
| `--dry-run` | 开关 | 关 | 只发现视频，不调用 LLM、不推送 |

### 推送行为

- 生成了简报：推送到配置的 Telegram / 飞书 / Email。
- 失败且没有生成总结：推送失败通知（需带来源、频道、视频、发布时间和原因）。
- 无新视频且带 `--send-empty`：推送“无更新”通知。

### 常见场景示例

```bash
# 最近 7 天、全部分组
.venv/bin/python scripts/run_digest_local.py --window last_7 --env-file key.env

# 全部历史、指定分组、无更新也推送
.venv/bin/python scripts/run_digest_local.py --window all_time --group technology --send-empty

# 干跑：只发现视频，不调用 LLM、不推送
.venv/bin/python scripts/run_digest_local.py --window last_1 --dry-run
```
