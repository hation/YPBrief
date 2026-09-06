# 三级筛选漏斗 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把每日全量总结改成"发现 → 分级 → 标题级初筛 → 人工挑选 → 只总结重点"的三级漏斗，大幅降低 LLM token 消耗。

**Architecture:** 在 `DigestRunService.run()` 内做三分流：`important` 频道照旧自动总结；`normal` 频道只发现并用新的 `TriageService` 做标题级批量初筛；`low` 频道仅记录标题和链接。人工通过 Telegram 回复编号或 Web UI 勾选选中 `pending` 视频后立即总结；日报只纳入 `auto + selected` 的视频。数据层给 `Sources` 加 `importance`、给 `Videos` 加 `selection_status/triage_score/triage_at`（幂等迁移兼容旧库）。

**Tech Stack:** Python 3.13, SQLite (sqlite3), FastAPI, APScheduler, pytest, OpenAI-compatible/Gemini/Claude provider 抽象。

---

## 实施状态（2026-09-06 已执行完毕）

本计划 6 个 Task 已全部按 TDD 落地并合入 main。提交链：

| 提交 | 对应 Task | 内容 |
| --- | --- | --- |
| `0d45a56` | Task 1 | db：triage 列 + importance 分级 + 幂等迁移 |
| `8128c35` | Task 2 | triage：`video_triage` 提示词 + `TriageService` |
| `850887a` | Task 3 | daily：`DigestRunService` 三分流 + 候选清单 |
| `9700751` | Task 4 | api：待选查询 + `POST /api/videos/select` |
| `f96d517` | Task 5 | api：Telegram 回复编号选择 |
| `1301811` | Task 6 | delivery：自动任务后推送待选清单 |

### 与计划的偏差（均已按实际调整）

| Task | 偏差 | 处理 |
| --- | --- | --- |
| Task 2 | 新增默认提示词后，`tests/test_prompts.py` 硬编码 `len(prompts) == 2` 失败 | 同步改为 `== 3` |
| Task 3 | 计划测试的 `TierYouTube` 缺少 `resolve_channel`（channel 来源发现会调用） | 测试类补该方法 |
| Task 3 | 默认 `normal` 后，10 个旧 run 测试的"全量自动总结"断言失效 | 旧测试 `upsert_source` 补 `importance="important"`，保留"重要频道自动总结"语义 |
| Task 5 | 文件既有 webhook 测试用 `_send_telegram_text` 打桩而非计划的 `_post_telegram_message` | 测试改用 `_send_telegram_text` + 额外打桩 `_provider_from_settings` / `Summarizer.summarize_video` |
| Task 5 | 计划测试假设插入序即候选序，但候选按 `video_date DESC` 排序 | 调整测试中 vid1/vid2 日期使编号 2 命中 vid2 |

验证：后端全量 **243 passed**，前端 `npm run build` 通过。

> 计划之外又追加了两项前端能力（Web UI 待选勾选视图、来源重要性编辑入口），见文末「追加实施」。

---

## 文件结构

| 文件 | 职责 | 动作 |
| --- | --- | --- |
| `src/ypbrief/database.py` | 建表加列、幂等迁移、新字段访问方法、`upsert_source` 支持 importance | 修改 |
| `src/ypbrief/prompts.py` | 新增 `video_triage` 默认提示词 | 修改 |
| `src/ypbrief/triage.py` | `TriageService`：渲染初筛 prompt、调用 provider、解析 JSON 分数 | 新建 |
| `src/ypbrief/daily.py` | `DigestRunService.run` 三分流、候选清单收集、注入 triage | 修改 |
| `src/ypbrief/delivery.py` | `send_triage_list` 待选清单渲染与推送 | 修改 |
| `src/ypbrief/scheduler.py` | 自动任务结束后推送待选清单 | 修改 |
| `src/ypbrief_api/app.py` | 待选清单查询、批量 select/summarize-selected、Telegram 编号选择 | 修改 |
| `tests/test_database.py` | 迁移 + 新方法测试 | 修改 |
| `tests/test_triage.py` | TriageService 测试 | 新建 |
| `tests/test_daily.py` | 三分流 + 候选清单测试 | 修改 |
| `tests/test_api.py` | Web UI 端点 + Telegram 编号测试 | 修改 |
| `tests/test_delivery.py` | 待选清单渲染与推送测试 | 修改 |
| `web/src/App.tsx` | 「待选视频」勾选视图 + 来源「重要性」列 | 追加（Task 7/8） |
| `web/src/types.ts` | `Source.importance`、`Video` triage 字段、`VideoMode.triage` | 追加（Task 7/8） |
| `web/src/App.css` | 勾选行/工具栏/重要性下拉样式 | 追加（Task 7/8） |
| `tests/test_prompts.py` | 默认提示词数量断言 `2 → 3`（Task 2 联动） | 修改 |

测试统一用 `.venv/bin/python -m pytest <file>::<test> -v`。

---

### Task 1: 数据库迁移与字段访问方法

**Files:**
- Modify: `src/ypbrief/database.py`（建表语句、`initialize`、`upsert_source`）
- Test: `tests/test_database.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_database.py` 末尾追加：

```python
def test_database_migrates_triage_columns_and_importance(tmp_path: Path) -> None:
    db = Database(tmp_path / "ypbrief.db")
    db.initialize()
    source_id = db.upsert_source(
        source_type="channel",
        source_name="Normal Channel",
        youtube_id="UCABC",
        url="https://www.youtube.com/channel/UCABC",
        importance="important",
    )
    with db.connect() as conn:
        source = conn.execute("SELECT importance FROM Sources WHERE source_id = ?", (source_id,)).fetchone()
        assert source["importance"] == "important"
        cols = {row[1] for row in conn.execute("PRAGMA table_info(Videos)").fetchall()}
        assert {"selection_status", "triage_score", "triage_at"} <= cols

    db.upsert_channel("UCABC", "Channel", "https://youtube.com/channel/UCABC")
    db.upsert_video("vid1", "UCABC", "Episode 1", "https://youtu.be/vid1", video_date="2026-04-24")
    db.set_video_selection_status("vid1", "pending")
    db.set_video_triage("vid1", 4.0)
    video = db.get_video("vid1")
    assert video["selection_status"] == "pending"
    assert video["triage_score"] == 4.0

    pending = db.list_videos_by_selection_status("pending")
    assert [v["video_id"] for v in pending] == ["vid1"]
    db.update_source_importance(source_id, "low")
    assert db.get_source(source_id)["importance"] == "low"


def test_database_migrates_legacy_db_without_new_columns(tmp_path: Path) -> None:
    db = Database(tmp_path / "legacy.db")
    with db.connect() as conn:
        conn.executescript(
            """
            CREATE TABLE Videos (
                video_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, video_title TEXT NOT NULL,
                video_url TEXT NOT NULL, video_date TEXT, duration INTEGER,
                status TEXT NOT NULL DEFAULT 'new', transcript_raw_json TEXT,
                transcript_raw_vtt TEXT, transcript_clean TEXT, summary_latest_id INTEGER,
                error_message TEXT, fetched_at TEXT, cleaned_at TEXT, summarized_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE Sources (
                source_id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT NOT NULL,
                source_name TEXT NOT NULL, display_name TEXT, youtube_id TEXT NOT NULL,
                url TEXT NOT NULL, channel_id TEXT, channel_name TEXT, playlist_id TEXT,
                enabled INTEGER NOT NULL DEFAULT 1, last_checked_at TEXT, last_error TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source_type, youtube_id)
            );
            """
        )
    db.initialize()
    with db.connect() as conn:
        vcols = {row[1] for row in conn.execute("PRAGMA table_info(Videos)").fetchall()}
        scols = {row[1] for row in conn.execute("PRAGMA table_info(Sources)").fetchall()}
    assert {"selection_status", "triage_score", "triage_at"} <= vcols
    assert "importance" in scols
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_database.py::test_database_migrates_triage_columns_and_importance tests/test_database.py::test_database_migrates_legacy_db_without_new_columns -v`
Expected: FAIL（`importance` 列不存在 / `set_video_selection_status` 未定义）

- [ ] **Step 3: 实现**

3a. 建表语句加列。`database.py` 中 `Sources` 建表加一行 `importance TEXT NOT NULL DEFAULT 'normal',`（放在 `enabled` 行后）；`Videos` 建表加三行：

```python
                    selection_status TEXT NOT NULL DEFAULT 'pending',
                    triage_score REAL,
                    triage_at TEXT,
```

3b. `initialize()` 在 `conn.executescript(...)` 之后调用迁移：

```python
    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """ ... 原有建表脚本 ... """
            )
            self._ensure_triage_columns(conn)

    def _ensure_triage_columns(self, conn: sqlite3.Connection) -> None:
        migrations = [
            ("Sources", "importance", "TEXT NOT NULL DEFAULT 'normal'"),
            ("Videos", "selection_status", "TEXT NOT NULL DEFAULT 'pending'"),
            ("Videos", "triage_score", "REAL"),
            ("Videos", "triage_at", "TEXT"),
        ]
        for table, column, ddl in migrations:
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
```

3c. `upsert_source` 加 `importance: str = "normal"` 参数，SQL 与参数都加该列：

```python
    def upsert_source(
        self,
        source_type: str,
        source_name: str,
        youtube_id: str,
        url: str,
        display_name: str | None = None,
        channel_id: str | None = None,
        channel_name: str | None = None,
        playlist_id: str | None = None,
        enabled: bool = True,
        importance: str = "normal",
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO Sources(
                    source_type, source_name, display_name, youtube_id, url,
                    channel_id, channel_name, playlist_id, enabled, importance
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_type, youtube_id) DO UPDATE SET
                    source_name=excluded.source_name,
                    display_name=excluded.display_name,
                    url=excluded.url,
                    channel_id=excluded.channel_id,
                    channel_name=excluded.channel_name,
                    playlist_id=excluded.playlist_id,
                    enabled=excluded.enabled,
                    importance=excluded.importance,
                    updated_at=CURRENT_TIMESTAMP
                RETURNING source_id
                """,
                (
                    source_type,
                    source_name,
                    display_name,
                    youtube_id,
                    url,
                    channel_id,
                    channel_name,
                    playlist_id,
                    1 if enabled else 0,
                    importance,
                ),
            )
            return int(cursor.fetchone()["source_id"])
```

3d. 在 `set_source_enabled` 附近新增四个方法：

```python
    def set_video_selection_status(self, video_id: str, selection_status: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE Videos
                SET selection_status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE video_id = ?
                """,
                (selection_status, video_id),
            )

    def set_video_triage(self, video_id: str, score: float | None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE Videos
                SET triage_score = ?, triage_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE video_id = ?
                """,
                (score, video_id),
            )

    def list_videos_by_selection_status(self, selection_status: str, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT v.*, c.channel_name
                FROM Videos v
                JOIN Channels c ON c.channel_id = v.channel_id
                WHERE v.selection_status = ?
                ORDER BY COALESCE(v.video_date, '') DESC, v.created_at DESC
                LIMIT ?
                """,
                (selection_status, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_source_importance(self, source_id: int, importance: str) -> None:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE Sources
                SET importance = ?, updated_at = CURRENT_TIMESTAMP
                WHERE source_id = ?
                """,
                (importance, source_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(source_id)
```

（`database.py` 顶部已 `import sqlite3`，方法里用到 `sqlite3.Connection`，确认文件头有 `from __future__ import annotations` 即可用字符串注解；若文件头没有，把注解写成 `conn` 不带类型。）

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_database.py -v`
Expected: PASS（含原有测试）

- [ ] **Step 5: 提交**

```bash
git add src/ypbrief/database.py tests/test_database.py
git commit -m "feat(db): add triage columns and importance tiering with idempotent migration"
```

---

### Task 2: video_triage 提示词 + TriageService

**Files:**
- Modify: `src/ypbrief/prompts.py`（`DEFAULT_PROMPTS` 新增 `video_triage`）
- Create: `src/ypbrief/triage.py`
- Test: `tests/test_triage.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_triage.py`：

```python
from pathlib import Path

from ypbrief.config import Settings
from ypbrief.database import Database
from ypbrief.prompts import DEFAULT_PROMPTS
from ypbrief.triage import TriageResult, TriageService


class FakeTriageProvider:
    name = "fake"
    model = "fake-triage-model"

    def summarize(self, prompt: str, transcript: str) -> str:
        assert "1. Video A" in transcript
        assert "2. Video B" in transcript
        return '[{"id": 1, "score": 5, "reason": "重要"}, {"id": 2, "score": 2, "reason": "一般"}]'


def test_video_triage_prompt_has_default() -> None:
    assert "video_triage" in DEFAULT_PROMPTS
    assert "{{ videos }}" in DEFAULT_PROMPTS["video_triage"]["user_template"]


def test_triage_service_renders_batch_and_parses_scores(tmp_path: Path) -> None:
    db = Database(tmp_path / "ypbrief.db")
    db.initialize()
    db.upsert_channel("UC123", "Channel", "https://youtube.com/channel/UC123")
    db.upsert_video("vidA", "UC123", "Video A", "https://youtu.be/vidA", video_date="2026-04-24", duration=3600)
    db.upsert_video("vidB", "UC123", "Video B", "https://youtu.be/vidB", video_date="2026-04-25", duration=600)
    videos = [db.get_video("vidA"), db.get_video("vidB")]

    results = TriageService(db, FakeTriageProvider()).triage(videos)

    assert results == [
        TriageResult(video_id="vidA", score=5.0, reason="重要"),
        TriageResult(video_id="vidB", score=2.0, reason="一般"),
    ]


def test_triage_service_ignores_out_of_range_and_duplicate_ids(tmp_path: Path) -> None:
    class DupProvider:
        name = "fake"
        model = "m"

        def summarize(self, prompt: str, transcript: str) -> str:
            return '[{"id": 1, "score": 3, "reason": "a"}, {"id": 1, "score": 5, "reason": "b"}, {"id": 99, "score": 5, "reason": "c"}]'

    db = Database(tmp_path / "ypbrief.db")
    db.initialize()
    db.upsert_channel("UC123", "Channel", "https://youtube.com/channel/UC123")
    db.upsert_video("vidA", "UC123", "Video A", "https://youtu.be/vidA")
    results = TriageService(db, DupProvider()).triage([db.get_video("vidA")])
    assert results == [TriageResult(video_id="vidA", score=3.0, reason="a")]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_triage.py -v`
Expected: FAIL（`ypbrief.triage` 不存在，`video_triage` 不在 DEFAULT_PROMPTS）

- [ ] **Step 3: 实现**

3a. `prompts.py` 的 `DEFAULT_PROMPTS` 中 `daily_digest` 条目之后追加：

```python
    "video_triage": {
        "prompt_id": 3,
        "prompt_type": "video_triage",
        "prompt_name": "视频初筛评分（标题级）",
        "language": "zh",
        "version": "default",
        "is_active": 1,
        "system_prompt": (
            "你是一名内容筛选助手。你会收到一批视频的标题、频道、时长和日期（不含字幕内容）。\n"
            "请评估每个视频是否值得做深度拆解总结，只基于标题和元数据判断，不要臆测内容。\n"
            "用简体中文输出。"
        ),
        "user_template": (
            "请对下面每个编号的视频给出 1-5 的推荐分（5=非常值得深度拆解，1=不值得），并附一句简短理由。\n\n"
            "{{ videos }}\n\n"
            "只输出一个 JSON 数组，不要输出其他内容，格式：\n"
            '[{"id": 1, "score": 4, "reason": "理由"}, ...]'
        ),
        "variables": ["videos"],
    },
```

3b. 新建 `src/ypbrief/triage.py`：

```python
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .database import Database
from .llm import SummaryProvider
from .prompts import DEFAULT_PROMPTS, DatabasePromptService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TriageResult:
    video_id: str
    score: float
    reason: str


DEFAULT_TRIAGE_BATCH = 20


class TriageService:
    def __init__(self, db: Database, provider: SummaryProvider, settings: Settings | None = None) -> None:
        self.db = db
        self.provider = provider
        self.settings = settings or Settings()

    def triage(self, videos: list[dict[str, Any]], batch_size: int = DEFAULT_TRIAGE_BATCH) -> list[TriageResult]:
        results: list[TriageResult] = []
        for start in range(0, len(videos), batch_size):
            batch = videos[start : start + batch_size]
            prompt, user_prompt = self._render_batch(batch)
            raw = self.provider.summarize(prompt, user_prompt)
            results.extend(self._parse(raw, batch))
        return results

    def _render_batch(self, batch: list[dict[str, Any]]) -> tuple[str, str]:
        lines = [
            f"{index}. {video['video_title']} | {video.get('channel_name') or ''} "
            f"| duration={video.get('duration') or '?'}s | date={video.get('video_date') or '?'}"
            for index, video in enumerate(batch, start=1)
        ]
        videos_text = "\n".join(lines)
        try:
            rendered = DatabasePromptService(self.db, self.settings.prompt_file).preview(
                "video_triage",
                {"videos": videos_text},
            )
            return rendered["system_prompt"], rendered["user_prompt"]
        except KeyError:
            default = DEFAULT_PROMPTS["video_triage"]
            system_prompt = str(default["system_prompt"])
            user_prompt = str(default["user_template"]).replace("{{ videos }}", videos_text)
            return system_prompt, user_prompt

    def _parse(self, raw: str, batch: list[dict[str, Any]]) -> list[TriageResult]:
        video_ids = [video["video_id"] for video in batch]
        data = _parse_triage_json(raw)
        results: list[TriageResult] = []
        seen: set[int] = set()
        for item in data:
            try:
                index = int(item.get("id") or item.get("index"))
            except (TypeError, ValueError):
                continue
            if index < 1 or index > len(batch) or index in seen:
                continue
            seen.add(index)
            try:
                score = float(item.get("score") or 0)
            except (TypeError, ValueError):
                score = 0.0
            results.append(
                TriageResult(
                    video_id=video_ids[index - 1],
                    score=score,
                    reason=str(item.get("reason") or ""),
                )
            )
        return results


def _parse_triage_json(raw: str) -> list[dict[str, Any]]:
    text = (raw or "").strip()
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_triage.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/ypbrief/prompts.py src/ypbrief/triage.py tests/test_triage.py
git commit -m "feat(triage): add title-level LLM triage service and video_triage prompt"
```

---

### Task 3: DigestRunService 三分流 + 候选清单

**Files:**
- Modify: `src/ypbrief/daily.py`（`DigestRunService`）
- Test: `tests/test_daily.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_daily.py` 末尾追加。先加两个 fake：

```python
class FakeTriageService:
    def __init__(self) -> None:
        self.called_with: list[list[str]] = []

    def triage(self, videos):
        self.called_with.append([v["video_id"] for v in videos])
        from ypbrief.triage import TriageResult

        return [
            TriageResult(video_id=v["video_id"], score=3.0, reason="ok")
            for v in videos
        ]
```

再追加测试：

```python
def test_digest_run_tiers_by_importance_and_keeps_candidates(tmp_path: Path) -> None:
    db = Database(tmp_path / "ypbrief.db")
    db.initialize()
    prompt_file = tmp_path / "prompts.yaml"
    PromptFileService(prompt_file).save(
        "daily_digest",
        system_prompt="正式日报提示词",
        user_template="日报 {{ run_date }}\n\n{{ summaries }}",
    )
    db.upsert_channel("UC123", "Test Channel", "https://youtube.com/channel/UC123")
    important_id = db.upsert_source(
        source_type="channel",
        source_name="Important",
        youtube_id="UCIMP",
        url="https://www.youtube.com/channel/UCIMP",
        channel_id="UC123",
        channel_name="Test Channel",
        importance="important",
    )
    normal_id = db.upsert_source(
        source_type="channel",
        source_name="Normal",
        youtube_id="UCNOR",
        url="https://www.youtube.com/channel/UCNOR",
        channel_id="UC123",
        channel_name="Test Channel",
        importance="normal",
    )
    low_id = db.upsert_source(
        source_type="channel",
        source_name="Low",
        youtube_id="UCLOW",
        url="https://www.youtube.com/channel/UCLOW",
        channel_id="UC123",
        channel_name="Test Channel",
        importance="low",
    )

    class TierYouTube:
        def __init__(self) -> None:
            self.calls = 0

        def iter_uploads(self, uploads_playlist_id: str, limit: int | None = None):
            self.calls += 1
            return [
                type("Video", (), {
                    "video_id": f"v{self.calls}",
                    "title": f"Episode {self.calls}",
                    "url": f"https://youtu.be/v{self.calls}",
                    "published_at": "2026-04-24T10:00:00Z",
                    "channel_id": "UC123",
                    "channel_name": "Test Channel",
                    "duration_seconds": 600,
                })(),
            ]

    youtube = TierYouTube()
    processor = FakeProcessor(db)
    triage = FakeTriageService()
    runner = DigestRunService(
        db=db,
        youtube=youtube,
        processor=processor,
        digest_service=DailyDigestService(db, LenientFakeProvider(), tmp_path / "exports", settings=Settings(prompt_file=prompt_file)),
        triage_service=triage,
    )
    result = runner.run(
        source_ids=[important_id, normal_id, low_id],
        run_date="2026-04-25",
        window_days=3,
        max_videos_per_source=10,
    )

    # 重要频道：自动总结，selection_status=auto
    assert processor.processed == ["v1"]
    assert db.get_video("v1")["selection_status"] == "auto"
    # 普通频道：不总结，进初筛，selection_status=pending 且有分数
    assert "v2" not in processor.processed
    assert triage.called_with == [["v2"]]
    assert db.get_video("v2")["selection_status"] == "pending"
    assert db.get_video("v2")["triage_score"] == 3.0
    # 低优频道：仅记录标题和链接，不总结不初筛
    assert "v3" not in processor.processed
    assert triage.called_with == [["v2"]]
    assert db.get_video("v3")["selection_status"] == "pending"
    assert db.get_video("v3")["triage_score"] is None
    # 候选清单只含 normal
    assert [c["video_id"] for c in result["triage_candidates"]] == ["v2"]
    # 日报只含重要频道（auto）
    assert result["status"] == "completed"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_daily.py::test_digest_run_tiers_by_importance_and_keeps_candidates -v`
Expected: FAIL（`triage_service` 参数不存在 / 所有视频都被 process）

- [ ] **Step 3: 实现**

3a. `daily.py` 顶部导入 TriageService 与 TriageResult：

```python
from .triage import TriageService
```

3b. `DigestRunService.__init__` 加可选参数，并提供懒加载：

```python
    def __init__(
        self,
        db: Database,
        youtube: VideoDiscoverySource,
        processor: VideoProcessorLike,
        digest_service: DailyDigestService,
        triage_service: TriageService | None = None,
    ) -> None:
        self.db = db
        self.youtube = youtube
        self.processor = processor
        self.digest_service = digest_service
        self.triage_service = triage_service

    def _get_triage_service(self) -> TriageService:
        if self.triage_service is None:
            self.triage_service = TriageService(db=self.db, provider=self.processor.summarizer.provider)
        return self.triage_service
```

3c. `run()` 里 `triage_candidates: list[dict] = []` 与 `skipped` 并列声明。然后在 `summary_id = self._latest_summary_id(...)` 之后、`try:` 之前插入分流：

```python
                    importance = source.get("importance") or "normal"
                    if summary_id is None and importance != "important":
                        self.db.set_video_selection_status(video_id, "pending")
                        if importance == "low":
                            skipped.append((video_id, source["source_id"], "low priority (title+link only)"))
                            self._record_run_video(run_id, video_id, source["source_id"], "skipped", "triage", "low priority (title+link only)", None)
                        else:
                            triage_candidates.append(self.db.get_video(video_id))
                            self._record_run_video(run_id, video_id, source["source_id"], "skipped", "triage", "pending selection", None)
                        continue
```

3d. 成功纳入的视频打 `auto` 标记。在 `try:` 块内 `included.append(video_id)` 之后加一行：

```python
                        self.db.set_video_selection_status(video_id, "auto")
```

3e. 循环结束后、`unique_included` 统计之前，执行初筛：

```python
            if triage_candidates:
                for triage in self._get_triage_service().triage(triage_candidates):
                    self.db.set_video_triage(triage.video_id, triage.score)
```

3f. 返回值附加候选清单。把末尾 `return self.get_run(run_id)` 改为：

```python
            result = self.get_run(run_id)
            result["triage_candidates"] = [self.db.get_video(v["video_id"]) for v in triage_candidates]
            return result
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_daily.py -v`
Expected: PASS（含原有 run 测试）

- [ ] **Step 5: 提交**

```bash
git add src/ypbrief/daily.py tests/test_daily.py
git commit -m "feat(daily): tier digest run by source importance with triage candidates"
```

---

### Task 4: Web UI 待选查询 + 批量选择/总结端点

**Files:**
- Modify: `src/ypbrief_api/app.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_api.py` 末尾追加：

```python
def test_api_triage_candidates_and_select_and_summarize(tmp_path: Path) -> None:
    db = Database(tmp_path / "ypbrief.db")
    db.initialize()
    db.upsert_channel("UC123", "Test Channel", "https://youtube.com/channel/UC123")
    db.upsert_video("vid1", "UC123", "Episode 1", "https://youtu.be/vid1", video_date="2026-04-24")
    db.upsert_video("vid2", "UC123", "Episode 2", "https://youtu.be/vid2", video_date="2026-04-25")
    db.set_video_selection_status("vid1", "pending")
    db.set_video_selection_status("vid2", "pending")
    client = TestClient(create_app(db=db))

    pending = client.get("/api/triage/candidates")
    assert pending.status_code == 200
    assert {v["video_id"] for v in pending.json()} == {"vid1", "vid2"}

    selected = client.post("/api/videos/select", json={"video_ids": ["vid1"], "action": "selected"})
    assert selected.status_code == 200
    assert selected.json()["updated"] == 1
    assert db.get_video("vid1")["selection_status"] == "selected"

    dismissed = client.post("/api/videos/select", json={"video_ids": ["vid2"], "action": "dismissed"})
    assert dismissed.json()["updated"] == 1
    assert db.get_video("vid2")["selection_status"] == "dismissed"

    missing = client.post("/api/videos/select", json={"video_ids": ["nope"]})
    assert missing.status_code == 404
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_api.py::test_api_triage_candidates_and_select_and_summarize -v`
Expected: FAIL（404 路由不存在）

- [ ] **Step 3: 实现**

在 `app.py` 的 `list_videos` 端点（`@app.get("/api/videos")`）之后、`@app.get("/api/videos/{video_id}")` 之前插入两个端点（注意顺序，避免 `/api/triage/...` 被视频路由吞掉；该路径前缀不同，无冲突）：

```python
    @app.get("/api/triage/candidates")
    def triage_candidates(limit: int = 200) -> list[dict[str, Any]]:
        return db.list_videos_by_selection_status("pending", limit=limit)

    @app.post("/api/videos/select")
    def select_videos(payload: dict[str, Any]) -> dict[str, Any]:
        video_ids = [str(v) for v in (payload.get("video_ids") or [])]
        action = payload.get("action") or "selected"
        if action not in {"selected", "dismissed"}:
            raise HTTPException(status_code=400, detail="action must be selected or dismissed")
        for video_id in video_ids:
            try:
                db.get_video(video_id)
            except KeyError:
                raise HTTPException(status_code=404, detail=f"Video not found: {video_id}")
            db.set_video_selection_status(video_id, action)
        return {"updated": len(video_ids), "action": action}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_api.py::test_api_triage_candidates_and_select_and_summarize -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/ypbrief_api/app.py tests/test_api.py
git commit -m "feat(api): add triage candidates query and batch video select endpoint"
```

---

### Task 5: Telegram 回复编号选择

**Files:**
- Modify: `src/ypbrief_api/app.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_api.py` 末尾追加（复用 app_module 里已存在的 `_post_telegram_message` 打桩方式；参考 `test_api.py` 中已有 webhook 测试对 requests 的 monkeypatch 模式，下面给出自包含版本）：

```python
def test_telegram_webhook_selects_by_numbers(tmp_path: Path, monkeypatch) -> None:
    db = Database(tmp_path / "ypbrief.db")
    db.initialize()
    db.upsert_channel("UC123", "Test Channel", "https://youtube.com/channel/UC123")
    db.upsert_video("vid1", "UC123", "Episode 1", "https://youtu.be/vid1", video_date="2026-04-24")
    db.upsert_video("vid2", "UC123", "Episode 2", "https://youtu.be/vid2", video_date="2026-04-25")
    db.set_video_selection_status("vid1", "pending")
    db.set_video_selection_status("vid2", "pending")
    client = TestClient(
        create_app(
            db=db,
            settings_override={
                "telegram_bot_inbox_enabled": "true",
                "telegram_bot_webhook_secret": "sec",
                "telegram_bot_allowed_chat_ids": "123",
                "telegram_bot_token": "bot:token",
            },
        )
    )
    replies: list[str] = []
    monkeypatch.setattr(
        app_module,
        "_post_telegram_message",
        lambda token, chat_id, text, parse_mode=None: replies.append(text),
    )

    # 无链接且是编号 → 选中 vid2（第2条）并总结
    resp = client.post(
        "/api/telegram/webhook/sec",
        json={"message": {"chat": {"id": 123}, "from": {"id": 1}, "text": "2"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": ""},
    )
    assert resp.status_code == 200
    assert db.get_video("vid2")["selection_status"] == "selected"
    assert db.get_video("vid1")["selection_status"] == "pending"
    assert any("已选中" in r for r in replies)

    # 非法编号 → 引导
    replies.clear()
    resp2 = client.post(
        "/api/telegram/webhook/sec",
        json={"message": {"chat": {"id": 123}, "from": {"id": 1}, "text": "99"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": ""},
    )
    assert any("编号无效" in r for r in replies)
```

（注意：若 `test_api.py` 现有 webhook 测试用了不同的 header 约定，以现有测试为准调整 header。）

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_api.py::test_telegram_webhook_selects_by_numbers -v`
Expected: FAIL（回复的是"请发送链接"引导文案，而非选中）

- [ ] **Step 3: 实现**

3a. 模块级正则（放在 `_extract_first_youtube_url` 定义附近）：

```python
TRIAGE_NUMBERS_RE = re.compile(r"^\s*\d+(\s*[,，\s]\s*\d+)*\s*$")
```

3b. 把 `telegram_webhook` 内 `if not video_url:` 分支替换为：

```python
        video_url = _extract_first_youtube_url(text)
        if not video_url:
            handled = _telegram_handle_number_selection(settings, db, chat_id, text)
            if handled is not None:
                return handled
            _telegram_reply(settings, chat_id, "请发送一个 YouTube 视频链接，或回复待选清单中的编号（如 1 3 5）来选中要总结的视频。")
            return {"status": "ignored", "reason": "no_youtube_url"}
```

3c. 新增辅助函数（放在 `_extract_first_youtube_url` 之后）：

```python
def _telegram_handle_number_selection(settings: Settings, db: Database, chat_id: str, text: str) -> dict[str, Any] | None:
    if not TRIAGE_NUMBERS_RE.match(text or ""):
        return None
    candidates = db.list_videos_by_selection_status("pending", limit=200)
    if not candidates:
        _telegram_reply(settings, chat_id, "当前没有待选视频。")
        return {"status": "ignored", "reason": "no_pending"}
    numbers = [int(part) for part in re.findall(r"\d+", text)]
    valid = [number for number in numbers if 1 <= number <= len(candidates)]
    if not valid:
        _telegram_reply(settings, chat_id, f"编号无效，请回复 1-{len(candidates)}。")
        return {"status": "ignored", "reason": "invalid_numbers"}
    selected = [candidates[number - 1] for number in sorted(set(valid))]
    for video in selected:
        db.set_video_selection_status(video["video_id"], "selected")
    _telegram_reply(settings, chat_id, f"已选中 {len(selected)} 条视频，正在生成总结...")
    done: list[str] = []
    failed: list[str] = []
    for video in selected:
        try:
            summary_id = Summarizer(db, _provider_from_settings(db, settings), settings=settings).summarize_video(video["video_id"])
            done.append(f"- {video.get('video_title') or video['video_id']}（总结 #{summary_id}）")
        except Exception:
            failed.append(video.get("video_title") or video["video_id"])
    if done:
        reply = f"已完成 {len(done)} 条总结：\n" + "\n".join(done)
        if failed:
            reply += f"\n{len(failed)} 条失败：{', '.join(failed)}"
        _telegram_reply(settings, chat_id, reply)
    elif failed:
        _telegram_reply(settings, chat_id, "所选视频总结全部失败，请到 Web UI 查看详情。")
    return {"status": "selected", "selected": len(selected), "summarized": len(done), "failed": len(failed)}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_api.py::test_telegram_webhook_selects_by_numbers -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/ypbrief_api/app.py tests/test_api.py
git commit -m "feat(api): support telegram reply-by-number selection of triage candidates"
```

---

### Task 6: 待选清单推送（自动发现后）

**Files:**
- Modify: `src/ypbrief/delivery.py`、`src/ypbrief/scheduler.py`
- Test: `tests/test_delivery.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_delivery.py` 末尾追加：

```python
def test_send_triage_list_renders_numbered_candidates(tmp_path: Path) -> None:
    db = Database(tmp_path / "ypbrief.db")
    db.initialize()
    settings = Settings()
    delivery = DeliveryService(db, settings)
    from ypbrief.delivery import _render_triage_list

    videos = [
        {
            "video_id": "vid1",
            "video_title": "Episode 1",
            "channel_name": "Channel A",
            "video_date": "2026-04-24",
            "duration": 3600,
            "triage_score": 5.0,
        },
        {
            "video_id": "vid2",
            "video_title": "Episode 2",
            "channel_name": "Channel B",
            "video_date": "2026-04-25",
            "duration": 600,
            "triage_score": None,
        },
    ]
    text = _render_triage_list("2026-04-25", videos, web_url="http://localhost/triage")
    assert "待选视频" in text
    assert "1. Channel A | Episode 1" in text
    assert "★5" in text
    assert "http://localhost/triage" in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_delivery.py::test_send_triage_list_renders_numbered_candidates -v`
Expected: FAIL（`_render_triage_list` 不存在）

- [ ] **Step 3: 实现**

3a. `delivery.py` 在 `send_no_updates` 之后新增方法：

```python
    def send_triage_list(
        self,
        run_date: str,
        videos: list[dict[str, Any]],
        *,
        language: str = "zh",
        web_url: str = "",
        telegram_enabled: bool | None = None,
        feishu_enabled: bool | None = None,
        email_enabled: bool | None = None,
    ) -> list[dict[str, Any]]:
        if not videos or not self.any_enabled(
            telegram_enabled=telegram_enabled,
            feishu_enabled=feishu_enabled,
            email_enabled=email_enabled,
        ):
            return []
        text = _render_triage_list(run_date, videos, language=language, web_url=web_url)
        return self.send_text(
            text,
            run_date=run_date,
            summary_id=None,
            run_id=None,
            telegram_enabled=telegram_enabled,
            feishu_enabled=feishu_enabled,
            email_enabled=email_enabled,
        )
```

3b. 模块级渲染函数（放在 `_failure_notice_text` 附近）：

```python
def _render_triage_list(run_date: str, videos: list[dict[str, Any]], *, language: str = "zh", web_url: str = "") -> str:
    lines = [f"# 待选视频清单 - {run_date}", ""]
    if language == "en":
        lines.append(f"{len(videos)} videos await selection. Reply with numbers (e.g. 1 3 5) on Telegram to summarize.")
    else:
        lines.append(f"共 {len(videos)} 条待选视频。Telegram 回复编号（如 1 3 5）即可选中并生成总结。")
    if web_url:
        lines.extend(["", f"Web UI 待选页：{web_url}"])
    lines.append("")
    for index, video in enumerate(videos, start=1):
        duration = video.get("duration")
        duration_text = f"{int(duration)}s" if duration else "-"
        score = video.get("triage_score")
        score_text = f" ★{score:g}" if score else ""
        title = video.get("video_title") or video.get("video_id") or "-"
        lines.append(
            f"{index}. {video.get('channel_name') or '-'} | {title} | {video.get('video_date') or '-'} | {duration_text}{score_text}"
        )
    return "\n".join(lines)
```

3c. `scheduler.py` 的 `_finalize_run_result` 里，在 `if finalized.get("summary_id"):` 分支之后追加待选清单推送：

```python
        triage_candidates = finalized.get("triage_candidates")
        if triage_candidates and not self._is_no_updates(finalized):
            base = (self.settings.telegram_bot_public_base_url or "").strip().rstrip("/")
            web_url = f"{base}/triage" if base else ""
            finalized["triage_deliveries"] = self.delivery.send_triage_list(
                digest_date,
                triage_candidates,
                language=digest_language,
                web_url=web_url,
                telegram_enabled=telegram_enabled,
                feishu_enabled=feishu_enabled,
                email_enabled=email_enabled,
            )
```

（确认 `Settings` 有 `telegram_bot_public_base_url` 字段；若没有，把 web_url 传空字符串 `""`。）

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_delivery.py -v tests/test_daily.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/ypbrief/delivery.py src/ypbrief/scheduler.py tests/test_delivery.py
git commit -m "feat(delivery): push numbered triage candidate list after automatic discovery"
```

---

## 自检记录

- **Spec 覆盖**：Sources.importance（Task 1）✓；Videos.selection_status/triage_score/triage_at（Task 1）✓；三分流（Task 3）✓；便宜 LLM 初筛（Task 2）✓；Telegram 回复编号（Task 5）✓；Web UI 勾选（Task 4）✓；日报只含 auto+selected（Task 3 的 included 逻辑不变，normal/low 不再进 included）✓；未选跨天保留（`pending` 默认值 + 不去重删除）✓；低优仅记录标题和链接（Task 3 的 `low` 分支）✓。
- **占位符扫描**：无 TBD/TODO。
- **类型一致性**：`TriageResult(video_id, score, reason)` 在 Task 2/3 一致；`set_video_triage(video_id, score)`、`set_video_selection_status(video_id, status)`、`list_videos_by_selection_status(status, limit)`、`update_source_importance(source_id, importance)`、`upsert_source(..., importance=...)` 全文档一致。

---

## 追加实施（实施计划之外，按设计文档 V1.1 补做）

### Task 7: Web UI 待选勾选视图 + 批量总结端点（commit `f4fa245`）

**Files:** `src/ypbrief_api/app.py`、`web/src/App.tsx`、`web/src/types.ts`、`web/src/App.css`、`tests/test_api.py`

- 后端：`GET /api/videos` 新增返回 `selection_status` / `triage_score`；新增 `POST /api/videos/summarize-selected`（批量标 `selected` + 逐个总结，单条失败不中断其他，返回 `summarized/failed`）
- 前端：视频页新增第三分段视图「待选视频」（`mode='triage'`），从 `GET /api/triage/candidates` 拉取 pending 视频；每行勾选框 + ★初筛分数；工具栏：全选 / 已选计数 / 「总结选中」/「忽略选中」；详情面板在待选视图同样提供 process/summarize
- 类型：`Video` 加 `selection_status/triage_score/duration`，`VideoMode` 增加 `triage`
- 测试：新增 `test_api_videos_list_includes_triage_fields`、`test_api_summarize_selected_marks_selected_and_reports`（打桩 `ypbrief.summarizer.Summarizer`）

### Task 8: Sources 重要性编辑入口（commit `fc6933f`）

**Files:** `src/ypbrief/database.py`、`src/ypbrief_api/app.py`、`web/src/App.tsx`、`web/src/types.ts`、`web/src/App.css`、`tests/test_api.py`、`tests/test_database.py`

- 后端：`db.update_source` 新增 `importance` 参数（`_UNSET` 风格）；`SourceUpdate` 模型与 `PATCH /api/sources/{id}` 支持 `importance`
- 前端：来源表新增「重要性」列（`important/normal/low` 三档下拉，切换即保存）
- 类型：`Source` 加 `importance`
- 测试：新增 `test_api_update_source_importance`、`test_database_update_source_importance`

### 追加实施后的验证

- 后端全量测试：`243 passed`（含 6 个 Task 与追加项的全部用例）
- 前端：`npm run build` 通过

> 与追加实施相关的设计内容（§4.1 编辑入口、§7.2 已实现视图、§11.1 源码定位、§13 实施状态）已在设计文档 V1.1 同步更新。
