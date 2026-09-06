from pathlib import Path

from ypbrief.cleaner import TranscriptSegment
from ypbrief.database import Database


def test_database_initializes_tables_and_searches_subtitle_text(tmp_path: Path) -> None:
    db = Database(tmp_path / "ypbrief.db")
    db.initialize()

    db.upsert_channel(
        channel_id="UC123",
        channel_name="Test Channel",
        channel_url="https://youtube.com/channel/UC123",
        handle="@test",
        uploads_playlist_id="UU123",
    )
    db.upsert_video(
        video_id="vid1",
        channel_id="UC123",
        video_title="Episode 1",
        video_url="https://youtu.be/vid1",
        video_date="2026-04-25",
        duration=120,
    )
    db.save_transcript(
        video_id="vid1",
        raw_json='[{"text": "hello world"}]',
        clean_text="hello searchable world",
        segments=[
            TranscriptSegment(start=0.0, duration=2.0, text="hello searchable world"),
        ],
    )

    results = db.search("searchable")

    assert len(results) == 1
    assert results[0]["video_id"] == "vid1"
    assert results[0]["video_title"] == "Episode 1"
    assert results[0]["text"] == "hello searchable world"


def test_database_saves_summaries_and_updates_video_status(tmp_path: Path) -> None:
    db = Database(tmp_path / "ypbrief.db")
    db.initialize()
    db.upsert_channel("UC123", "Test Channel", "https://youtube.com/channel/UC123")
    db.upsert_video("vid1", "UC123", "Episode 1", "https://youtu.be/vid1")

    summary_id = db.save_summary(
        summary_type="video",
        content_markdown="# Summary",
        provider="openai",
        model="gpt-test",
        video_id="vid1",
        channel_id="UC123",
    )

    video = db.get_video("vid1")
    summary = db.get_summary(summary_id)

    assert video["status"] == "summarized"
    assert video["summary_latest_id"] == summary_id
    assert summary["content_markdown"] == "# Summary"
    assert summary["model_provider"] == "openai"


def test_database_get_video_transcript_includes_source_and_dates(tmp_path: Path) -> None:
    db = Database(tmp_path / "ypbrief.db")
    db.initialize()
    db.upsert_channel("UC123", "Test Channel", "https://youtube.com/channel/UC123")
    db.upsert_video(
        "vid1",
        "UC123",
        "Episode 1",
        "https://youtu.be/vid1",
        video_date="2026-04-25",
    )
    db.save_transcript(
        video_id="vid1",
        raw_json='{"source": "yt_dlp"}',
        raw_vtt="WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nhello world\n",
        clean_text="hello world",
        segments=[TranscriptSegment(0.0, 2.0, "hello world")],
    )
    with db.connect() as conn:
        conn.execute(
            "UPDATE Videos SET fetched_at = ? WHERE video_id = ?",
            ("2026-04-25 13:14:15", "vid1"),
        )

    transcript = db.get_video_transcript("vid1")

    assert transcript["channel_name"] == "Test Channel"
    assert transcript["video_title"] == "Episode 1"
    assert transcript["video_date"] == "2026-04-25"
    assert transcript["fetched_at"] == "2026-04-25 13:14:15"
    assert transcript["transcript_raw_json"] == '{"source": "yt_dlp"}'
    assert transcript["transcript_raw_vtt"].startswith("WEBVTT")


def test_database_delete_scheduled_job_keeps_history_runs(tmp_path: Path) -> None:
    db = Database(tmp_path / "ypbrief.db")
    db.initialize()
    job = db.save_scheduled_job(job_name="Old Automation")
    with db.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO DailyRuns(run_type, status, window_start, window_end, source_ids_json, scheduled_job_id)
            VALUES ('scheduled', 'completed', '2026-04-27', '2026-04-27', '[]', ?)
            """,
            (job["job_id"],),
        )
        run_id = int(cursor.lastrowid)

    db.delete_scheduled_job(job["job_id"])

    with db.connect() as conn:
        run = conn.execute("SELECT scheduled_job_id FROM DailyRuns WHERE run_id = ?", (run_id,)).fetchone()
        deleted = conn.execute("SELECT job_id FROM ScheduledJobs WHERE job_id = ?", (job["job_id"],)).fetchone()
    assert run is not None
    assert run["scheduled_job_id"] is None
    assert deleted is None


def test_database_get_video_includes_channel_name(tmp_path: Path) -> None:
    db = Database(tmp_path / "ypbrief.db")
    db.initialize()
    db.upsert_channel("UC123", "Readable Podcast", "https://youtube.com/channel/UC123")
    db.upsert_video("vid1", "UC123", "Episode 1", "https://youtu.be/vid1")

    video = db.get_video("vid1")

    assert video["channel_name"] == "Readable Podcast"


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


def test_database_update_source_importance(tmp_path: Path) -> None:
    db = Database(tmp_path / "ypbrief.db")
    db.initialize()
    source_id = db.upsert_source(
        source_type="channel",
        source_name="Test Channel",
        youtube_id="UC123",
        url="https://www.youtube.com/channel/UC123",
    )
    assert db.get_source(source_id)["importance"] == "normal"

    db.update_source(source_id, importance="important")
    assert db.get_source(source_id)["importance"] == "important"

    db.update_source(source_id, importance="low")
    assert db.get_source(source_id)["importance"] == "low"
