from pathlib import Path

from ypbrief.prompts import PromptFileService


def test_prompt_file_service_loads_defaults_and_persists_updates(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompts.yaml"
    service = PromptFileService(prompt_file)

    prompts = service.list()
    updated = service.save(
        "daily_digest",
        system_prompt="系统提示词",
        user_template="日报 {{ summaries }} {{ run_date }} {{ digest_language }}",
    )
    reloaded = PromptFileService(prompt_file).get("daily_digest")

    assert len(prompts) == 3
    assert prompts[0]["prompt_type"] == "video_summary"
    assert updated["system_prompt"] == "系统提示词"
    assert reloaded["user_template"] == "日报 {{ summaries }} {{ run_date }} {{ digest_language }}"
    assert prompt_file.exists()


def test_daily_digest_default_prompt_separates_current_synthesis_and_forward_watch(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompts.yaml"
    service = PromptFileService(prompt_file)

    daily = service.get("daily_digest")

    assert "当前状况是什么" in daily["user_template"]
    assert "关注什么" in daily["user_template"]
    assert "如何验证" in daily["user_template"]
    assert "以提供的单条视频总结为主要来源" in daily["user_template"]
    assert "不要引入来源摘要之外的新主张或细节" in daily["user_template"]


def test_video_summary_default_prompt_emphasizes_core_summary_and_decision_value(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompts.yaml"
    service = PromptFileService(prompt_file)

    video = service.get("video_summary")

    assert "用 2-3 句清晰的话说明" in video["user_template"]
    assert "列出 4-6 条最重要的收获要点" in video["user_template"]
    assert "每条可 1-2 句" in video["user_template"]
    assert "# 时间线" in video["user_template"]
    assert "列出 3-7 个带时间戳的关键节点" in video["user_template"]
    assert "聚焦行业、市场、政策、投资影响" in video["user_template"]
    assert "如果影响有限或不确定" in video["user_template"]


def test_prompt_file_service_preview_rejects_unknown_variable(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompts.yaml"
    service = PromptFileService(prompt_file)
    service.save(
        "video_summary",
        system_prompt="",
        user_template="Summarize {{ transcript }} and {{ missing }}.",
    )

    try:
        service.preview("video_summary", {"transcript": "hello"})
    except ValueError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("Expected unknown variable to fail")
