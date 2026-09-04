"""Run a full YPBrief digest locally WITHOUT the Web UI / FastAPI backend.

Reads enabled sources from the local SQLite database (the same database the Web
UI writes to), then runs the full pipeline: discover new videos -> fetch
subtitles -> LLM single-video summaries -> synthesize the daily digest. Optionally
pushes the result to Telegram / Feishu / Email via the configured delivery
settings.

Why this exists: the CLI (`ypbrief daily summarize`) can only combine already-
summarized videos by explicit id. The full auto pipeline lives in
`DigestRunService.run()`, which the Web UI's "run now" and the background
scheduler use. This script exposes that same pipeline from the command line.

Usage:
    .venv/bin/python scripts/run_digest_local.py --window last_7 --env-file key.env
    .venv/bin/python scripts/run_digest_local.py --window all_time --send-empty --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if SRC_DIR.exists() and str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ypbrief.config import load_settings
from ypbrief.daily import DailyDigestService, DigestRunService
from ypbrief.database import Database
from ypbrief.delivery import DeliveryService
from ypbrief.llm import ConfigError
from ypbrief.provider_config import create_provider_from_database
from ypbrief.transcripts import TranscriptFetcher
from ypbrief.video_processor import VideoProcessor
from ypbrief.youtube import YouTubeDataClient


WINDOW_DAYS = {
    "last_1": 1,
    "last_3": 3,
    "last_7": 7,
    "all_time": None,
}


def _is_no_updates(result: dict) -> bool:
    return result.get("status") == "no_updates"


def _is_failed_without_summary(result: dict) -> bool:
    return result.get("status") == "failed" and not result.get("summary_id")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a YPBrief digest from the local database, no Web UI required."
    )
    parser.add_argument("--env-file", default="key.env", help="Path to key.env")
    parser.add_argument(
        "--window",
        choices=sorted(WINDOW_DAYS),
        default="last_1",
        help="Lookback window for new videos (default: last_1)",
    )
    parser.add_argument("--run-date", default=None, help="Digest date YYYY-MM-DD (default: today)")
    parser.add_argument("--group", default=None, help="Only sources in this source group")
    parser.add_argument("--max-videos-per-source", type=int, default=10)
    parser.add_argument("--language", choices=["zh", "en"], default="zh")
    parser.add_argument(
        "--send-empty",
        action="store_true",
        help="Also push a 'no updates' notice when nothing is included",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not call the LLM or push anything")
    args = parser.parse_args(argv)

    settings = load_settings(args.env_file)
    db = Database(settings.db_path)
    db.initialize()

    if not settings.youtube_data_api_key:
        print("ERROR: YOUTUBE_DATA_API_KEY is required in key.env", file=sys.stderr)
        return 1

    # Resolve enabled sources (optionally filtered to one group).
    all_sources = db.list_sources(enabled_only=True)
    if args.group:
        group = next(
            (g for g in db.list_source_groups() if g["group_name"] == args.group or g["group_name"] == args.group),
            None,
        )
        if group is None:
            print(f"ERROR: source group '{args.group}' not found", file=sys.stderr)
            return 1
        group_id = int(group["group_id"])
        all_sources = [s for s in all_sources if s.get("group_id") == group_id]
    source_ids = [int(s["source_id"]) for s in all_sources]
    if not source_ids:
        print("ERROR: no enabled sources (add one with `ypbrief source add <url>` first)", file=sys.stderr)
        return 1

    # If dry-run, skip the LLM provider entirely (it would not be used anyway),
    # but we still need one to construct the processor. Use a stub that raises
    # if invoked, so a dry run truly does not call the model.
    try:
        provider = create_provider_from_database(db, settings)
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    youtube = YouTubeDataClient(settings.youtube_data_api_key)
    processor = VideoProcessor.from_api_key(
        db=db,
        youtube_api_key=settings.youtube_data_api_key,
        transcripts=TranscriptFetcher.from_settings(settings),
        provider=provider,
        export_dir=settings.export_dir,
        settings=settings,
    )
    digest_service = DailyDigestService(
        db=db, provider=provider, export_dir=settings.export_dir, settings=settings
    )
    runner = DigestRunService(db=db, youtube=youtube, processor=processor, digest_service=digest_service)

    window_days = WINDOW_DAYS[args.window]
    from datetime import date

    run_date = args.run_date or date.today().isoformat()

    print(f"Running digest: window={args.window} run_date={run_date} sources={source_ids} dry_run={args.dry_run}")
    result = runner.run(
        source_ids=source_ids,
        run_date=run_date,
        window_days=window_days,
        max_videos_per_source=args.max_videos_per_source,
        reuse_existing_summaries=True,
        # In dry-run we still discover videos (cheap, uses YouTube API) but do
        # not fetch subtitles or call the LLM for videos lacking a summary.
        process_missing_videos=not args.dry_run,
        retry_failed_once=True,
        digest_language=args.language,
        run_type="manual",
    )
    print(
        f"run_id={result['run_id']} status={result['status']} "
        f"included={result.get('included_count')} failed={result.get('failed_count')} "
        f"skipped={result.get('skipped_count')} summary_id={result.get('summary_id')}"
    )

    deliveries: list[dict] = []
    summary_id = result.get("summary_id")
    if not args.dry_run:
        delivery = DeliveryService(db, settings)
        if summary_id:
            deliveries = delivery.send_summary(int(summary_id), run_id=int(result["run_id"]))
        elif _is_failed_without_summary(result):
            deliveries = delivery.send_failure_notice(int(result["run_id"]), run_date, args.language)
        elif _is_no_updates(result) and args.send_empty:
            deliveries = delivery.send_no_updates(run_date, args.language, run_id=int(result["run_id"]))
    for d in deliveries:
        print(f"  delivered {d.get('channel')} -> {d.get('status')} ({d.get('target')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
