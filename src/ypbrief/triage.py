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
