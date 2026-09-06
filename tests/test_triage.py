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
