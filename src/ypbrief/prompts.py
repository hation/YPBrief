from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import yaml

from .database import Database


_VARIABLE = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")

DEFAULT_PROMPTS: dict[str, dict[str, Any]] = {
    "video_summary": {
        "prompt_id": 1,
        "prompt_type": "video_summary",
        "prompt_name": "视频总结提示词（强制中文）",
        "language": "zh",
        "version": "default",
        "is_active": 1,
        "system_prompt": (
            "你是一名专业的内容研究编辑。你的任务是把一段播客/长视频节目整理成一份清晰、结构化、"
            "可直接交付给客户的高质量总结。\n\n"
            "你会收到视频的基本信息、字幕文稿和相关元数据。请基于这些材料，产出一份结构完整、信息密度高、"
            "突出核心结论和真实价值的总结。\n\n"
            "无论视频的原始语言是什么，都必须用简体中文输出。"
        ),
        "user_template": (
            "请严格遵守以下要求：\n"
            "1. 输出语言：无论视频原始语言是中文、英文还是其他语言，一律使用简体中文输出全部内容"
            "（包括标题、字段标签、正文）。\n"
            "2. 面向客户写作，聚焦核心洞见、重要观点、具体数据和实用价值。\n"
            "3. 不要提及任何系统或处理流程细节。\n"
            "4. 保持专业、准确、克制的文风，避免空话、夸张和套话。\n"
            "5. 如果字幕零散或口语化，请整理成清晰、有条理的总结。\n"
            "6. 不要编造信息，只使用字幕中真实出现的内容。\n"
            "7. 优先保留结论、判断、数据、时间线和有参考价值的内容。\n"
            "8. 如果本期涉及多个话题，聚焦最重要、最有价值的主题。\n\n"
            "严格使用以下结构：\n\n"
            "# 播客名称\n"
            "{{ channel_name }}\n\n"
            "# 视频标题\n"
            "{{ video_title }}\n\n"
            "# 发布日期\n"
            "{{ video_date }}\n\n"
            "# 核心总结\n"
            "用 2-3 句清晰的话说明：\n"
            "- 本期节目主要讲什么\n"
            "- 核心论点或主要结论\n"
            "- 对听众最重要、最有价值的一点\n\n"
            "# 要点\n"
            "列出 4-6 条最重要的收获要点（每条可 1-2 句，优先深度和具体性，优先包含数据、时间线、"
            "明确判断或高实用价值的内容）。\n\n"
            "# 时间线\n"
            "在字幕中存在可靠时间提示时，列出 3-7 个带时间戳的关键节点，每条包含：\n"
            "- 大致时间戳\n"
            "- 该时间点讨论的主题或段落\n"
            "- 一句话说明这个节点对回顾为什么有用\n\n"
            "# 为什么重要\n"
            "用 2-4 句说明本期节目的更广泛意义，聚焦行业、市场、政策、投资影响或对目标受众的价值。\n"
            "如果影响有限或不确定，请谨慎表述，不要夸大。\n\n"
            "请确保输出信息密度高、便于快速浏览、可直接用于日报汇总。\n\n"
            "以下是素材：\n"
            "- 视频链接：{{ video_url }}\n\n"
            "{{ transcript }}"
        ),
        "variables": ["channel_name", "video_date", "video_title", "video_url", "transcript"],
    },
    "daily_digest": {
        "prompt_id": 2,
        "prompt_type": "daily_digest",
        "prompt_name": "每日简报提示词（强制中文）",
        "language": "zh",
        "version": "default",
        "is_active": 1,
        "system_prompt": (
            "你是一名专业的行业内容编辑，负责为客户准备每日播客简报。\n\n"
            "你会收到当天的一组视频总结及相关元数据。你的任务不是描述工作流程，而是帮助客户快速了解："
            "哪些播客来源有更新、当天的主要话题是什么、哪些结论最重要、哪些趋势值得持续关注。\n\n"
            "无论输入材料是什么语言，一律使用简体中文输出。"
        ),
        "user_template": (
            "请严格遵守以下要求：\n"
            "1. 输出语言：始终使用简体中文输出整个简报（包括标题、字段标签和正文），无论单条视频总结的"
            "原始语言是什么。\n"
            "2. 面向客户写作，聚焦信息价值和提炼后的结论。\n"
            "3. 不要提及任何运行或后端细节，如失败、跳过的视频、字幕状态、模型调用、重试、技术实现或"
            "工作流程。\n"
            "4. 结构顺序固定：先更新概览，再整体结论，再逐条视频摘要，最后是后续关注。\n"
            "5. 整体结论必须是跨视频的综合结论，而不是各条摘要的简单拼接。\n"
            "6. 保持专业、简洁、克制，避免重复、空话、模板化填充或夸张。\n"
            "7. 优先呈现结论、变化、趋势、判断和值得持续关注的信号，而不是大段复述内容。\n"
            "8. 单条视频摘要应紧凑、清晰、便于扫读。\n"
            "9. 不要编造来源摘要中不存在的信息。\n"
            "10. 如果当天没有值得总结的新视频，用专业自然的语气生成一段简短简报，明确说明今天没有值得"
            "总结的新视频。\n"
            "11. 第一行必须是包含摘要日期的顶级标题，且必须包含精确日期 `{{ run_date }}`。\n"
            "12. 章节标题和字段标签必须与简报输出语言一致（此处为中文）。\n"
            "13. 每条视频的标题必须严格使用提供的「来源标题」，不要猜测、翻译、缩写或替换来源层级。\n\n"
            "严格使用以下结构：\n\n"
            "# 每日播客简报 - {{ run_date }}\n\n"
            "# 每日更新概览\n"
            "用 2-4 句说明：\n"
            "- 今天有多少个播客来源更新\n"
            "- 今天的简报纳入了多少个新视频\n"
            "- 今天更新的主要内容集中在哪些主题、方向或话题\n\n"
            "# 整体结论\n"
            "给出对当天关键结论和判断的跨视频综合。\n"
            "聚焦：当前状况是什么、发生了什么变化、客户应从今天的更新中理解什么。\n"
            "用 3-5 条要点，每条一个清晰结论或综合观察。\n"
            "不要把本段写成一大段文字。\n"
            "这里不要聚焦未来监控内容。\n\n"
            "# 视频摘要\n"
            "以提供的单条视频总结为主要来源。\n"
            "将它们按重要性排序（最重要的在前），精简重排为以下结构，保留原始总结的含义、重点和关键洞见。\n"
            "不要引入来源摘要之外的新主张或细节。\n"
            "让语言更精炼、适合日常阅读，但不要过度简化。\n\n"
            "每条视频严格使用以下格式：\n"
            "## {来源标题}\n"
            "- 发布日期：{发布日期}\n"
            "- 要点：\n"
            "  - 要点 1（由提供的总结精简而来）\n"
            "  - 要点 2\n"
            "  - 要点 3\n"
            "- 影响与意义：\n"
            "  用 1-2 句说明。可改编自原总结的「为什么重要」部分，但保留其核心含义。\n\n"
            "# 后续关注\n"
            "提取 2-4 条近期（未来几天到 4 周内）值得持续关注的具体趋势、风险、机会或进展。\n"
            "每一条都要明确：\n"
            "- 关注什么\n"
            "- 为什么重要\n"
            "- 有哪些具体信号、事件、政策决定、数据发布或时间窗口可以验证方向\n"
            "本部分必须是前瞻性的、有增量的内容。\n"
            "不要重复「整体结论」中已覆盖的结论。\n"
            "聚焦接下来会发生什么、如何验证，而不是复述今天的收获。\n"
            "只提来源材料支持或合理延伸的信息。\n"
            "不要编造没有依据的具体日期、催化剂或阈值。\n"
            "最多 2-4 条，优先最有可操作性或时效性的内容。\n\n"
            "摘要日期：{{ run_date }}\n"
            "目标输出语言：简体中文（中文）\n\n"
            "{{ summaries }}"
        ),
        "variables": ["digest_language", "run_date", "summaries"],
    },
}


class DatabasePromptService:
    def __init__(self, db: Database, fallback_path: str | Path | None = None) -> None:
        self.db = db
        self.fallback_path = Path(fallback_path) if fallback_path else None

    def ensure_defaults(self) -> None:
        existing = self.db.list_prompt_templates(group_id=None)
        if not existing and self.fallback_path and self.fallback_path.exists():
            self.import_from_file(self.fallback_path)
            existing = self.db.list_prompt_templates(group_id=None)
        by_type = {row["prompt_type"] for row in existing if row.get("group_id") is None}
        for prompt_type, default in DEFAULT_PROMPTS.items():
            if prompt_type not in by_type:
                self.db.create_prompt_template(
                    prompt_type=prompt_type,
                    prompt_name=default["prompt_name"],
                    version=default["version"],
                    language=default["language"],
                    group_id=None,
                    system_prompt=default["system_prompt"],
                    user_template=default["user_template"],
                    variables_json=json.dumps(default["variables"]),
                    is_active=True,
                )

    def list(self, group_id: int | None = -1) -> list[dict[str, Any]]:
        self.ensure_defaults()
        prompts = self.db.list_prompt_templates(group_id=group_id)
        for item in prompts:
            item["variables"] = self._decode_variables(item.get("variables_json"))
        return prompts

    def get(self, prompt_ref: str | int, group_id: int | None = None) -> dict[str, Any]:
        self.ensure_defaults()
        if isinstance(prompt_ref, int) or str(prompt_ref).isdigit():
            prompt = self.db.get_prompt_template(int(prompt_ref))
            prompt["variables"] = self._decode_variables(prompt.get("variables_json"))
            return prompt
        prompt_type = str(prompt_ref).strip()
        candidates = self.db.list_prompt_templates(group_id=group_id if group_id is not None else -1)
        chosen = _select_prompt(candidates, prompt_type=prompt_type, group_id=group_id)
        if chosen is None and group_id is not None:
            chosen = _select_prompt(candidates, prompt_type=prompt_type, group_id=None)
        if chosen is None:
            default = DEFAULT_PROMPTS.get(prompt_type)
            if default is None:
                raise KeyError(prompt_ref)
            return copy.deepcopy(default)
        chosen["variables"] = self._decode_variables(chosen.get("variables_json"))
        return chosen

    def save(
        self,
        *,
        prompt_type: str,
        system_prompt: str,
        user_template: str,
        prompt_name: str | None = None,
        language: str = "auto",
        group_id: int | None = None,
        activate: bool = True,
        notes: str | None = None,
        skip_bootstrap: bool = False,
    ) -> dict[str, Any]:
        if not skip_bootstrap:
            self.ensure_defaults()
        existing = [
            item
            for item in self.db.list_prompt_templates(group_id=group_id)
            if item["prompt_type"] == prompt_type and item.get("group_id") == group_id
        ]
        version = f"v{len(existing) + 1}"
        default = DEFAULT_PROMPTS.get(prompt_type)
        saved = self.db.create_prompt_template(
            prompt_type=prompt_type,
            prompt_name=prompt_name or (default["prompt_name"] if default else prompt_type),
            version=version,
            language=language,
            group_id=group_id,
            system_prompt=system_prompt,
            user_template=user_template,
            variables_json=json.dumps(sorted(_template_variables(user_template))),
            is_active=activate,
            notes=notes,
        )
        saved["variables"] = self._decode_variables(saved.get("variables_json"))
        return saved

    def activate(self, prompt_id: int) -> dict[str, Any]:
        prompt = self.db.activate_prompt_template(prompt_id)
        prompt["variables"] = self._decode_variables(prompt.get("variables_json"))
        return prompt

    def reset_defaults(self) -> list[dict[str, Any]]:
        created: list[dict[str, Any]] = []
        for prompt_type, default in DEFAULT_PROMPTS.items():
            created.append(
                self.save(
                    prompt_type=prompt_type,
                    prompt_name=default["prompt_name"],
                    language=default["language"],
                    system_prompt=default["system_prompt"],
                    user_template=default["user_template"],
                    activate=True,
                )
            )
        return created

    def preview(self, prompt_ref: str | int, values: dict[str, Any], group_id: int | None = None) -> dict[str, str]:
        prompt = self.get(prompt_ref, group_id=group_id)
        system_prompt = _render(prompt.get("system_prompt") or "", values)
        user_prompt = _render(prompt["user_template"], values)
        return {"system_prompt": system_prompt, "user_prompt": user_prompt}

    def export_payload(self) -> dict[str, Any]:
        self.ensure_defaults()
        groups: dict[str, dict[str, Any]] = {}
        for prompt in self.list(group_id=-1):
            variables = prompt.get("variables") or self._decode_variables(prompt.get("variables_json"))
            record = {
                "prompt_name": prompt["prompt_name"],
                "language": prompt["language"],
                "system_prompt": prompt.get("system_prompt") or "",
                "user_template": prompt["user_template"],
                "variables": variables,
            }
            group_name = prompt.get("group_name")
            if group_name:
                groups.setdefault(group_name, {})[prompt["prompt_type"]] = record
            else:
                groups.setdefault("__global__", {})[prompt["prompt_type"]] = record
        payload: dict[str, Any] = {"prompts": {"global": groups.pop("__global__", {})}}
        if groups:
            payload["prompts"]["groups"] = groups
        return payload

    def save_to_file(self, path: str | Path) -> Path:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(yaml.safe_dump(self.export_payload(), allow_unicode=True, sort_keys=False), encoding="utf-8")
        return output_path

    def import_from_file(self, path: str | Path) -> int:
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        imported = 0
        prompt_root = payload.get("prompts")
        if prompt_root is None:
            prompt_root = {
                "global": {
                    key: value
                    for key, value in payload.items()
                    if key in DEFAULT_PROMPTS
                }
            }
        global_prompts = prompt_root.get("global", {})
        for prompt_type, value in global_prompts.items():
            self.save(
                prompt_type=prompt_type,
                prompt_name=value.get("prompt_name") or DEFAULT_PROMPTS[prompt_type]["prompt_name"],
                language=value.get("language") or DEFAULT_PROMPTS[prompt_type]["language"],
                system_prompt=value.get("system_prompt") or "",
                user_template=value.get("user_template") or DEFAULT_PROMPTS[prompt_type]["user_template"],
                group_id=None,
                activate=True,
                skip_bootstrap=True,
            )
            imported += 1
        for group_name, prompts in (prompt_root.get("groups") or {}).items():
            group_id = _group_id_by_name(self.db, group_name)
            if group_id is None:
                continue
            for prompt_type, value in prompts.items():
                self.save(
                    prompt_type=prompt_type,
                    prompt_name=value.get("prompt_name") or DEFAULT_PROMPTS[prompt_type]["prompt_name"],
                    language=value.get("language") or DEFAULT_PROMPTS[prompt_type]["language"],
                    system_prompt=value.get("system_prompt") or "",
                    user_template=value.get("user_template") or DEFAULT_PROMPTS[prompt_type]["user_template"],
                    group_id=group_id,
                    activate=True,
                    skip_bootstrap=True,
                )
                imported += 1
        return imported

    @staticmethod
    def _decode_variables(raw: str | list[str] | None) -> list[str]:
        if isinstance(raw, list):
            return raw
        if not raw:
            return []
        return list(json.loads(raw))


class PromptFileService:
    def __init__(self, path: str | Path = "prompts.yaml") -> None:
        self.path = Path(path)

    def list(self) -> list[dict[str, Any]]:
        prompts = self._load_legacy()
        return [copy.deepcopy(prompts[key]) for key in sorted(prompts, key=lambda item: prompts[item]["prompt_id"])]

    def get(self, prompt_ref: str | int) -> dict[str, Any]:
        key = self._resolve_key(prompt_ref)
        return copy.deepcopy(self._load_legacy()[key])

    def save(self, prompt_type: str, system_prompt: str, user_template: str) -> dict[str, Any]:
        prompts = self._load_legacy()
        key = self._resolve_key(prompt_type)
        prompt = prompts[key]
        prompt["system_prompt"] = system_prompt
        prompt["user_template"] = user_template
        prompt["variables"] = sorted(_template_variables(user_template))
        self._write_legacy(prompts)
        return copy.deepcopy(prompt)

    def reset_defaults(self) -> list[dict[str, Any]]:
        prompts = copy.deepcopy(DEFAULT_PROMPTS)
        self._write_legacy(prompts)
        return [copy.deepcopy(prompts[key]) for key in sorted(prompts, key=lambda item: prompts[item]["prompt_id"])]

    def preview(self, prompt_ref: str | int, values: dict[str, Any]) -> dict[str, str]:
        prompt = self.get(prompt_ref)
        system_prompt = _render(prompt.get("system_prompt") or "", values)
        user_prompt = _render(prompt["user_template"], values)
        return {"system_prompt": system_prompt, "user_prompt": user_prompt}

    def _load_legacy(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            prompts = copy.deepcopy(DEFAULT_PROMPTS)
            self._write_legacy(prompts)
            return prompts
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        prompt_root = raw.get("prompts", {}).get("global") if isinstance(raw.get("prompts"), dict) else raw
        prompts: dict[str, dict[str, Any]] = {}
        for key, default_prompt in DEFAULT_PROMPTS.items():
            current = (prompt_root or {}).get(key) or {}
            prompts[key] = {
                **copy.deepcopy(default_prompt),
                **current,
                "prompt_type": key,
                "prompt_id": default_prompt["prompt_id"],
                "variables": current.get("variables")
                or sorted(_template_variables(current.get("user_template") or default_prompt["user_template"])),
            }
        return prompts

    def _write_legacy(self, prompts: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        output: dict[str, Any] = {}
        for key in sorted(prompts, key=lambda item: prompts[item]["prompt_id"]):
            prompt = prompts[key]
            output[key] = {
                "prompt_name": prompt["prompt_name"],
                "language": prompt["language"],
                "system_prompt": prompt["system_prompt"],
                "user_template": prompt["user_template"],
                "variables": prompt["variables"],
            }
        self.path.write_text(yaml.safe_dump(output, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def _resolve_key(self, prompt_ref: str | int) -> str:
        prompts = self._load_legacy()
        if isinstance(prompt_ref, int):
            for key, prompt in prompts.items():
                if prompt["prompt_id"] == prompt_ref:
                    return key
            raise KeyError(prompt_ref)
        text = str(prompt_ref).strip()
        if text in prompts:
            return text
        if text.isdigit():
            return self._resolve_key(int(text))
        raise KeyError(prompt_ref)


def _select_prompt(candidates: list[dict[str, Any]], *, prompt_type: str, group_id: int | None) -> dict[str, Any] | None:
    scoped = [
        item for item in candidates
        if item["prompt_type"] == prompt_type and item.get("group_id") == group_id
    ]
    active = [item for item in scoped if item.get("is_active")]
    if active:
        return sorted(active, key=lambda item: item["prompt_id"], reverse=True)[0]
    if scoped:
        return sorted(scoped, key=lambda item: item["prompt_id"], reverse=True)[0]
    return None


def _group_id_by_name(db: Database, group_name: str) -> int | None:
    name = group_name.strip()
    if not name:
        return None
    for group in db.list_source_groups():
        if group["group_name"] == name:
            return int(group["group_id"])
    return None


def _render(template: str, values: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise ValueError(f"Missing prompt variable: {name}")
        return str(values[name])

    return _VARIABLE.sub(replace, template)


def _template_variables(template: str) -> set[str]:
    return set(_VARIABLE.findall(template))
