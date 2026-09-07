# YPBrief实现细节-字幕抓取与文本清洗V1.0

| 文档版本 | 创建日期 | 修订简述 |
| --- | --- | --- |
| V1.0 | 2026-09-04 | 基于 `src/ypbrief/transcripts.py`、`src/ypbrief/cleaner.py` 及 `config.py` 中 `YT_DLP_*` 相关配置，首次成稿 |
| V1.1 | 2026-09-07 | 同步字幕抓取反风控实战修复：yt-dlp 改用 YouTube **android 播放器客户端**（`extractor_args.player_client=android`）绕过 nsig 签名挑战与 429 限流；cookies 默认关闭（android 客户端不支持 cookies，加载反而触发挑战）；`key.env.example` 建议代理 `127.0.0.1:10808`、sleep 间隔 5/15 |

## 目录

- [1 子系统概述](#1-子系统概述)
- [2 核心机制详解](#2-核心机制详解)
  - [2.1 TranscriptFetcher 后端编排](#21-transcriptfetcher-后端编排)
  - [2.2 字幕语言优先级](#22-字幕语言优先级)
  - [2.3 直接字幕语言发现与过滤](#23-直接字幕语言发现与过滤)
  - [2.4 fetch_with_yt_dlp 下载与反限流](#24-fetch_with_yt_dlp-下载与反限流)
  - [2.5 按语言优先级选择 VTT 文件](#25-按语言优先级选择-vtt-文件)
  - [2.6 VTT 解析与增量去重](#26-vtt-解析与增量去重)
  - [2.7 文本清洗与片段合并](#27-文本清洗与片段合并)
- [3 关键流程](#3-关键流程)
- [4 设计决策与权衡](#4-设计决策与权衡)
- [5 关键配置项](#5-关键配置项)
- [6 与其他子系统的关系](#6-与其他子系统的关系)
- [7 源码定位](#7-源码定位)
- [8 参考资料](#8-参考资料)

## 1 子系统概述

字幕抓取与文本清洗子系统负责将 YouTube 视频的**自带字幕**抓取下来，解析为带时间戳的片段，再清洗成可投喂 LLM（大语言模型）的干净文本。它由 `src/ypbrief/transcripts.py`（字幕抓取）与 `src/ypbrief/cleaner.py`（文本清洗）两个模块共同构成，是 YPBrief「视频 → 摘要」链路中把原始媒体转化为结构化文本的关键环节。

子系统具备以下特征：

- **默认后端为 yt-dlp**：通过 `TranscriptFetcher` 的可扩展后端机制注册 `"yt-dlp"` 后端，实际下载走 `yt_dlp.YoutubeDL`。
- **不做本地语音识别（ASR）**：字幕文本全部来自 YouTube 提供的 `subtitles`（人工字幕）与 `automatic_captions`（自动字幕），不调用本地 whisper 等语音转写能力；yt-dlp 仅负责下载字幕文件本身。
- **输出两级结果**：`TranscriptFetchResult` 携带原始 VTT 文本（`source_vtt`）、结构化片段（`segments`）与来源标记（`source`）；`CleanTranscript` 携带合并后的片段与整段干净文本（`text`）。
- **错误聚合**：多后端、多语言尝试均失败时，聚合为单个 `TranscriptFetchError` 抛出，错误信息包含各次尝试的明细。

整个子系统通过 [fetch](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L109-L124) 对外暴露统一入口，对上层（`VideoProcessor`、`ArchiveService`）屏蔽了后端选择、语言发现、下载重试与解析细节。

## 2 核心机制详解

### 2.1 TranscriptFetcher 后端编排

[TranscriptFetcher](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L32-L124) 是字幕抓取的门面类，内部维护一个 `backends` 列表。每个后端是一个二元组 `(名称, 可调用对象)`，类型别名定义如下：

```python
TranscriptSource = Callable[[str, list[str]], list[TranscriptSegment] | TranscriptFetchResult]
TranscriptBackend = tuple[str, TranscriptSource]
```

`__init__` 根据构造参数决定后端列表，存在三种装配路径：

| 路径 | 判定条件 | 结果 |
| --- | --- | --- |
| 显式传入 `backends` | `backends is not None` | 直接采用传入列表，不做任何加工 |
| 传入 `primary` / `fallback` | 二者至少一个非 `None` | 依次注册为 `("primary", ...)`、`("fallback", ...)` |
| 均未传入 | 默认分支 | 注册单一 `("yt-dlp", ytdlp_backend)` 后端 |

在默认分支中，代理按 `yt_dlp_proxy or proxy_https or proxy_http` 的优先级归一成一个 `proxy` 变量，并闭包捕获 cookies 与限速参数，构造 `ytdlp_backend`。

[from_settings](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L80-L107) 是面向配置的工厂方法：从 `Settings` 读取 `yt_dlp_proxy_url`、cookies、`sleep_interval`、`max_sleep_interval`、`retries`，统一以 `("yt-dlp", ...)` 后端构造实例，同时把 `youtube_proxy_http/https` 作为代理参数保存。

[fetch](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L109-L124) 的执行逻辑：

1. 语言列表为空时，用 `preferred_subtitle_languages("en")` 得到默认优先级。
2. 依次遍历 `backends`，将 `(video_id, 语言列表)` 交给后端。
3. 首个成功的后端直接返回，返回值为 `TranscriptFetchResult` 时保留其 `source` 与 `source_vtt`，否则以当前后端名包裹为 `TranscriptFetchResult`。
4. 后端抛异常时记录 `"{name}={exc}"` 到 `errors`；全部失败后抛出 `TranscriptFetchError`，消息为 `Could not fetch transcript for {video_id}: <明细>`。

### 2.2 字幕语言优先级

[preferred_subtitle_languages](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L127-L137) 根据视频默认语言（`video.default_language`）映射出一组**按优先级降序排列**的候选语言，供下载与文件选择使用。规则如下：

| 视频默认语言 | 返回的语言优先级列表 |
| --- | --- |
| `zh-Hant` / `zh-TW` / `zh-HK` | `["zh-Hant", "zh", "zh-Hans"]` |
| 以 `zh` 开头（如 `zh-Hans`、`zh-CN`） | `["zh-Hans", "zh-Hant", "zh"]` |
| `en-US` / `en` | `["en-US", "en", "en-GB"]` |
| 以 `en-` 开头的其他地区 | `[该语言, "en", "en-US", "en-GB"]` |
| 其他 | `[该语言]` |

其设计意图：中文内容优先繁体中文人工字幕（`zh-Hant`），英文内容优先美式英语（`en-US`），同时提供宽泛的 `zh` / `en` 作为兜底，以覆盖自动字幕的常见语言标记。

[subtitle_language_attempts](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L140-L148) 将优先级列表拆成逐个单语言尝试列表，并去除重复项，供下载阶段逐语言重试。

### 2.3 直接字幕语言发现与过滤

为避免 yt-dlp 把「自动翻译字幕（tlang）」当作可用字幕，抓取前先做一次**发现（discovery）**，只统计 YouTube 直接提供的字幕语言。

[_discover_yt_dlp_direct_caption_languages](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L236-L263)：

- 构造只读型 `YoutubeDL` 选项：`skip_download: True`、`quiet`、`no_warnings`、`noprogress`、`noplaylist`，并按需注入 `proxy`、`cookiefile`、`cookiesfrombrowser`。
- 固定 `extractor_args={"youtube": {"player_client": ["android"]}}`，强制走 YouTube **android 播放器客户端**：该客户端不触发 web 客户端的 nsig 签名挑战（"The page needs to be reloaded"），且对字幕下载的 429 限流更宽容。
- 调用 `ydl.extract_info(video_url, download=False)` 获取视频元信息（不下载）。
- 发现过程本身失败时不阻断主流程，异常被吞掉后 `direct_caption_languages` 置空（见 [fetch_with_yt_dlp](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L190-L198)）。

[_extract_direct_caption_languages](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L266-L277) 遍历 `info` 的 `subtitles` 与 `automatic_captions` 两个字段，对每个语言条目，只要任一格式通过 [_is_direct_caption_format](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L280-L286) 判定为直接字幕即收录该语言，并保持首次出现的顺序去重。

`_is_direct_caption_format` 的判定规则：

- 条目本身带 `tlang` 字段 → 判定为翻译字幕，返回 `False`。
- 条目 URL 的查询参数中包含 `tlang` → 同样判定为翻译字幕，返回 `False`。
- 其余条目视为直接字幕，返回 `True`。

随后 [select_yt_dlp_language](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L151-L160) 在「偏好语言列表」与「实际存在的直接字幕语言」之间做匹配：遍历偏好语言，命中即返回；全部未命中则退回直接语言列表首个元素；若没有任何直接字幕则返回 `None`，进入逐语言兜底重试路径。

### 2.4 fetch_with_yt_dlp 下载与反限流

[fetch_with_yt_dlp](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L174-L233) 是 yt-dlp 后端的顶层实现，执行顺序：

1. 惰性导入 `yt_dlp`，缺失时抛出带 `YT_DLP_DEPENDENCY_MESSAGE` 的 `TranscriptFetchError`。
2. 构造视频 URL `https://www.youtube.com/watch?v={video_id}`。
3. 调用发现函数取得直接字幕语言（失败则视为空）。
4. 用 `select_yt_dlp_language` 选取首选语言；命中则只尝试该语言一次，失败即抛错。
5. 未命中则通过 `subtitle_language_attempts` 逐个语言尝试，累积所有失败明细。
6. 全部失败抛出 `TranscriptFetchError`，消息为 `yt-dlp could not fetch subtitles for {video_id}: <明细>`。

[_fetch_with_yt_dlp_language_attempt](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L289-L345) 负责单次语言的实际下载：

- 使用 `tempfile.TemporaryDirectory()` 作为输出目录，保证下载中间产物自动清理。
- yt-dlp 选项（见下表）同时开启 `writesubtitles` 与 `writeautomaticsub`，字幕格式限定 `vtt`，并声明 `subtitleslangs=[*languages, "-live_chat"]`（`-live_chat` 表示排除直播聊天字幕）。
- 与发现阶段一致，固定 `extractor_args` 走 android 客户端；反限流三件套：`sleep_interval`（下载间最小休眠）、`max_sleep_interval`（随机休眠上限）、`retries` 与 `fragment_retries`（下载重试次数）。
- 下载完成后在临时目录用 `Path.glob(f"{video_id}*.vtt")` 收集 VTT 文件；一个都没有则报「未下载到字幕」。
- 用 `_select_vtt_file` 按语言优先级选出目标文件，读取为 UTF-8 文本（`errors="ignore"`），交给 `parse_youtube_vtt_text` 解析；解析结果为空同样报错。

| yt-dlp 选项 | 取值 | 作用 |
| --- | --- | --- |
| `outtmpl` | `tmp_dir / "%(id)s"` | 以视频 id 命名输出文件 |
| `skip_download` | `True` | 不下载视频本体，仅字幕 |
| `writesubtitles` / `writeautomaticsub` | `True` | 同时允许人工与自动字幕 |
| `subtitlesformat` | `vtt` | 只请求 WebVTT 格式 |
| `subtitleslangs` | `[*languages, "-live_chat"]` | 按语言列表请求，排除直播聊天 |
| `extractor_args` | `{"youtube": {"player_client": ["android"]}}` | 强制 android 播放器客户端，绕过 web 客户端 nsig 挑战、缓解 429 |
| `writeinfojson` | `True` | 写入视频元信息 JSON |
| `sleep_interval` | 配置值（默认 2） | 两次下载间最小休眠秒数 |
| `max_sleep_interval` | 配置值（默认 8） | 随机休眠上限秒数 |
| `retries` / `fragment_retries` | 配置值（默认 3） | 网络/分片重试次数 |

### 2.5 按语言优先级选择 VTT 文件

[_select_vtt_file](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L348-L358) 在下载出的多个 VTT 文件中做排序选择：

- 先为语言列表建立 `{language: index}` 排名表。
- 对每个文件计算排名键 `(index, 文件名)`：文件名含 `.{language}.` 或以其结尾时取该语言序号，否则取 `len(语言列表)`（最低优先级）。
- 按排名键升序取第一个，即优先返回偏好语言靠前的 VTT。

### 2.6 VTT 解析与增量去重

[parse_youtube_vtt_text](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L361-L396) 将原始 VTT 文本解析为 `TranscriptSegment` 列表，核心是解决 **YouTube 自动字幕逐句累积重复**的问题。步骤：

1. 将 `\r\n` 统一为 `\n`，按空行把文本切分为块（每个字幕 cue 一个块）。
2. 每块内过滤空白行；定位包含 `-->` 的时间行；其后续非空行拼接为字幕文本。
3. 用 `_parse_vtt_timing` 解析起止时间（经 `_timestamp_to_seconds` 把 `HH:MM:SS.mmm` 转秒），用 `_normalize_vtt_caption_text` 去掉标签并压缩空白。
4. 调用 `_caption_increment` 计算「相对上一句的增量文本」。

`_caption_increment` 的去重逻辑：

- 无上一句 → 返回整句。
- 与上一句完全相同 → 返回空串（整句重复，丢弃）。
- 以上一句开头（YouTube 自动字幕常见的「前句完整 + 追加新词」）→ 仅返回追加部分。
- 其他情况 → 返回整句。

解析层同时维护 `previous_visible_text` 与 `previous_incremental_text`：增量文本为空、或与上一条已入队增量相同时，跳过当前块，避免连续重复。

`TranscriptSegment` 的 `duration` 取 `max(0.0, end - start)`，保证非负；起始时间沿用 cue 的起始秒。

### 2.7 文本清洗与片段合并

[clean_transcript](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/cleaner.py#L55-L88) 在解析出的片段之上执行两级处理：单片段清洗 + 跨片段合并。

**单片段清洗**（[_clean_text](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/cleaner.py#L43-L52)）：

- `html.unescape` 还原 HTML 实体，并把不间断空格 `\xa0` 归一为普通空格。
- 填充词删除：对 `DEFAULT_FILLERS` 按**长度降序**遍历，英文短语用单词边界正则（`\b...\b`，忽略大小写）替换为空格（避免误伤 `likeable` 等词内片段），中文短语直接 `replace` 为空格。
- 空白合并：`\s+` 压成单个空格。
- 标点压缩：标点前的空白删除（`\s+([,.!?;:，。！？；：])` → 标点本身）。
- 首尾裁剪：去掉开头/结尾空白与中英文逗号句号。

**跨片段合并**：

- 先用 `_clean_text` 过滤出非空片段（清洗后为空文本的片段直接丢弃）。
- 遍历已清洗片段：当前片段起点与上一条已合并片段的 `end`（即 `start + duration`）之间的 `gap` 若 `<= merge_gap_seconds`（默认 2.5 秒），则合并为同一条：起点取更早的，`duration` 覆盖到更晚的结束点，文本以单个空格拼接；否则另起一条。
- 最终产出 `CleanTranscript`：`segments` 为合并后片段列表，`text` 为各片段文本以换行符 `\n` 连接而成的整段干净文本。

`DEFAULT_FILLERS` 集合（见 [cleaner.py:L25-L40](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/cleaner.py#L25-L40)）：

| 类别 | 填充词 |
| --- | --- |
| 英文 | `um`、`uh`、`erm`、`ah`、`like`、`you know`、`i mean` |
| 中文 | `嗯`、`啊`、`呃`、`额`、`那个`、`这个`、`就是` |

## 3 关键流程

以下序列图描述「单视频字幕抓取 → 解析 → 清洗 → 入库」的完整链路（下游 `VideoProcessor` 视角）：

```mermaid
sequenceDiagram
    participant VP as VideoProcessor.process
    participant TF as TranscriptFetcher.fetch
    participant D as _discover_yt_dlp_direct_caption_languages
    participant F as fetch_with_yt_dlp
    participant P as parse_youtube_vtt_text
    participant C as clean_transcript
    participant DB as Database.save_transcript

    VP->>TF: fetch(video_id, languages)
    TF->>TF: languages 为空则取 preferred_subtitle_languages("en")
    TF->>D: 发现直接字幕语言（subtitles + automatic_captions）
    D-->>TF: direct_caption_languages（已过滤 tlang 翻译字幕）
    TF->>F: select_yt_dlp_language 选中语言 → 下载 VTT
    Note over F: 临时目录 + writesubtitles/writeautomaticsub<br/>subtitlesformat=vtt + player_client=android<br/>sleep_interval 限速
    F-->>TF: TranscriptFetchResult(segments, source_vtt)
    TF-->>VP: TranscriptFetchResult(source="yt-dlp", ...)
    VP->>P: 解析 VTT → TranscriptSegment[]（增量去重）
    P-->>VP: segments
    VP->>C: clean_transcript(segments)
    Note over C: 填充词删除 + html.unescape<br/>空白/标点压缩 + gap<=2.5s 合并
    C-->>VP: CleanTranscript(text, merged segments)
    VP->>DB: save_transcript(raw_json, raw_vtt, clean_text, segments)
    Note over DB: Videos.transcript_raw_json/raw_vtt/clean<br/>Subtitles 表 + FTS5 索引
```

语言选择分支说明：

```mermaid
flowchart TD
    A[发现直接字幕语言] --> B{存在直接字幕?}
    B -- 否 --> C[逐个语言尝试兜底下载]
    B -- 是 --> D{偏好列表命中?}
    D -- 是 --> E[下载命中语言 VTT]
    D -- 否 --> F[退回直接语言列表首个]
    C --> G{任一成功?}
    F --> E
    E --> G
    G -- 是 --> H[解析 + 清洗 + 入库]
    G -- 否 --> I[聚合错误抛 TranscriptFetchError]
```

## 4 设计决策与权衡

| 决策 | 选择 | 权衡说明 |
| --- | --- | --- |
| 字幕来源 | 只用 YouTube 自带字幕（人工 + 自动），不做本地 ASR | 零计算成本、速度快，且字幕已带时间戳；代价是**依赖视频本身有字幕**，无字幕视频会抓取失败（`TranscriptFetchError`） |
| 后端可扩展性 | `backends` 列表 + 依次尝试 + 错误聚合 | 便于未来接入 yt-dlp 之外的抓取器（如 youtube-transcript-api），但当前默认仅注册 yt-dlp 一个后端 |
| 自动字幕累积重复 | 增量提取 `_caption_increment` | 针对 YouTube 自动字幕「逐句完整 + 追加」的格式，保留增量文本消除重复；代价是同一 cue 内若前句被编辑修正，可能保留不完整片段（推测：极端编辑场景下增量算法可能不完美） |
| 翻译字幕过滤 | 发现阶段过滤 `tlang`，只取直接字幕 | 避免把多语言机翻字幕当作原始语言抓取，保证文本忠实于原音 |
| 反爬与封禁 | android 播放器客户端 + 代理 + `sleep_interval` 限速 + `retries` 重试 | 主手段是 **android 客户端**（绕过 web 客户端的 nsig 签名挑战，429 更宽容）；代理/限速/重试为辅助。实战教训：**加载 cookies 会让 yt-dlp 跳过 android 客户端**（android 不支持 cookies）而退回 web/tv 客户端，反而触发 "page needs to be reloaded"，因此默认关闭 cookies；代价是下载变慢（建议 5–15 秒随机休眠），且无 cookies 的匿名访问在短时密集抓取时仍可能被 429 临时限流 |
| 字幕格式 | 固定请求 `vtt`，不依赖默认格式 | VTT 带时间信息且易解析，解析器只按 `-->` 时间行工作 |
| 下载目录 | `tempfile.TemporaryDirectory` | 自动清理中间文件，避免污染工作目录；代价是原始 VTT 不落盘（仅内存中的 `source_vtt` 入库） |
| 清洗合并粒度 | `merge_gap_seconds=2.5` 合并相邻片段 | 把被自动字幕切碎的同义段落拼成完整句，利于 LLM 理解；代价是过大的 gap 会错误拼接不相关句子（可通过参数调整） |
| 语言优先级 | 中文繁体优先、英文 `en-US` 优先 | 贴合 YouTube 实际字幕标注习惯，人工字幕通常为 `zh-Hant`/`en-US`（推测：基于 YouTube 字幕常见标注约定） |

## 5 关键配置项

以下配置均来自 [config.py](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/config.py#L48-L60) 的 `Settings` 字段，经环境变量注入（映射见 [config.py:L183-L195](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/config.py#L183-L195)）。

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `YT_DLP_COOKIES_FILE` | `""` | cookies 文件路径，存在时作为 `cookiefile` 传入 yt-dlp；经 `_resolve_path` 解析为绝对路径（见 [config.py:L108-L109](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/config.py#L108-L109)） |
| `YT_DLP_COOKIES_FROM_BROWSER` | `""` | 浏览器名（如 `chrome`），存在时作为 `cookiesfrombrowser` 传入 yt-dlp |
| `YT_DLP_PROXY` | `""` | yt-dlp 专用代理 URL，优先级高于通用 YouTube 代理（见 `yt_dlp_proxy_url`） |
| `YT_DLP_SLEEP_INTERVAL` | `"2"` | 两次下载间最小休眠秒数 |
| `YT_DLP_MAX_SLEEP_INTERVAL` | `"8"` | 随机休眠上限秒数 |
| `YT_DLP_RETRIES` | `"3"` | 下载重试次数，同时用于 `retries` 与 `fragment_retries` |
| `YOUTUBE_PROXY_ENABLED` | `"false"` | 是否启用 YouTube 代理，`as_bool` 解析 |
| `YOUTUBE_PROXY_HTTP` / `YOUTUBE_PROXY_HTTPS` | `""` | HTTP/HTTPS 代理地址；`youtube_proxy_url` 取 HTTPS > HTTP > IPRoyal |
| `IPROYAL_PROXY_HOST` / `_PORT` / `_USERNAME` / `_PASSWORD` | `""` | IPRoyal 代理四元组，四者齐全时拼接为 `http://user:pass@host:port`（用户名密码经 URL 编码，见 `iproyal_proxy_url`） |

代理解析规则（[config.py:L148-L151](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/config.py#L148-L151)）：`yt_dlp_proxy_url` = 代理启用时取 `yt_dlp_proxy`，为空则退回 `youtube_proxy_url`（即 HTTPS > HTTP > IPRoyal）；未启用代理则整体为空。该 URL 在 [TranscriptFetcher.from_settings](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L80-L107) 中被闭包捕获并传给 yt-dlp 后端。

**实战部署建议（V1.1，见 `key.env.example`）**：

- 字幕抓取已硬编码走 android 客户端，**不要配置 `YT_DLP_COOKIES_FILE` / `YT_DLP_COOKIES_FROM_BROWSER`**：cookies 会让 yt-dlp 跳过 android 客户端，退回 web/tv 客户端后触发 nsig 挑战（"page needs to be reloaded"）。
- `YT_DLP_PROXY` 建议指向本地代理（如 `http://127.0.0.1:10808`），可降低被 YouTube 标记的风险。
- `YT_DLP_SLEEP_INTERVAL` / `YT_DLP_MAX_SLEEP_INTERVAL` 建议 `5` / `15`（config.py 默认 `2` / `8`，短时密集抓取易触发 429）。

## 6 与其他子系统的关系

字幕抓取与文本清洗位于「YouTube 数据 → 结构化文本 → 摘要」链路的中间层，主要交互方如下：

| 交互方 | 方向 | 说明 |
| --- | --- | --- |
| `VideoProcessor`（video_processor.py） | 调用方 | 在 [process](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/video_processor.py#L78-L130) 中以 `video.default_language` 推导语言优先级后调用 `transcripts.fetch`，对结果执行 `clean_transcript`，再以 `save_transcript` 入库 |
| `ArchiveService`（archive.py） | 调用方 | 批量归档频道时对每个视频调用 `transcripts.fetch` 与 `clean_transcript`（见 [archive.py:L78-L93](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/archive.py#L78-L93)）；默认以 `TranscriptFetcher()` 装配 |
| `Database`（database.py） | 落库 | [save_transcript](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/database.py#L879-L919) 将原始 JSON、原始 VTT、干净文本写入 `Videos` 表（`transcript_raw_json` / `transcript_raw_vtt` / `transcript_clean`，状态置 `cleaned`），并将合并片段逐行写入 `Subtitles` 表；`Subtitles` 有 FTS5 全文索引（`Subtitles_fts`），供全文检索 |
| `Summarizer`（summarizer.py） | 下游消费 | [summarize_video](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/summarizer.py#L16-L45) 读取 `Videos.transcript_clean`，缺失时抛出 `ValueError("Video ... has no cleaned transcript")`，随后将其拼入提示词交 LLM 生成摘要 |
| `Exporter`（exporter.py） | 导出 | 读取 `transcript_raw_vtt` 导出原始字幕文件 |

数据流汇总：`yt-dlp 下载 VTT` → `parse_youtube_vtt_text` 解析片段 → `clean_transcript` 清洗合并 → `save_transcript` 入库（原始 + 干净两份）→ `Summarizer` 消费干净文本 → 用户侧 `search` 全文检索 `Subtitles`。

## 7 源码定位

以下路径均以仓库根目录 `/Users/xingan/Documents/software/aiengine/ypbrief` 为基准。行号为本版源码实际位置。

| 文件 | 符号 | 行号 |
| --- | --- | --- |
| `src/ypbrief/transcripts.py` | `TranscriptFetchError` | [L14-L15](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L14-L15) |
| `src/ypbrief/transcripts.py` | `YT_DLP_DEPENDENCY_MESSAGE` | [L18](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L18) |
| `src/ypbrief/transcripts.py` | `TranscriptFetchResult` | [L21-L25](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L21-L25) |
| `src/ypbrief/transcripts.py` | `TranscriptSource` / `TranscriptBackend` | [L28-L29](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L28-L29) |
| `src/ypbrief/transcripts.py` | `TranscriptFetcher.__init__` | [L33-L78](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L33-L78) |
| `src/ypbrief/transcripts.py` | `TranscriptFetcher.from_settings` | [L80-L107](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L80-L107) |
| `src/ypbrief/transcripts.py` | `TranscriptFetcher.fetch` | [L109-L124](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L109-L124) |
| `src/ypbrief/transcripts.py` | `preferred_subtitle_languages` | [L127-L137](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L127-L137) |
| `src/ypbrief/transcripts.py` | `subtitle_language_attempts` | [L140-L148](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L140-L148) |
| `src/ypbrief/transcripts.py` | `select_yt_dlp_language` | [L151-L160](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L151-L160) |
| `src/ypbrief/transcripts.py` | `_dedupe_languages` | [L163-L171](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L163-L171) |
| `src/ypbrief/transcripts.py` | `fetch_with_yt_dlp` | [L174-L233](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L174-L233) |
| `src/ypbrief/transcripts.py` | `_discover_yt_dlp_direct_caption_languages` | [L236-L264](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L236-L264) |
| `src/ypbrief/transcripts.py` | `_extract_direct_caption_languages` | [L267-L278](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L267-L278) |
| `src/ypbrief/transcripts.py` | `_is_direct_caption_format` | [L281-L287](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L281-L287) |
| `src/ypbrief/transcripts.py` | `_fetch_with_yt_dlp_language_attempt` | [L290-L347](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L290-L347) |
| `src/ypbrief/transcripts.py` | `_select_vtt_file` | [L350-L360](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L350-L360) |
| `src/ypbrief/transcripts.py` | `parse_youtube_vtt_text` | [L363-L398](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L363-L398) |
| `src/ypbrief/transcripts.py` | `_parse_vtt_timing` | [L401-L404](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L401-L404) |
| `src/ypbrief/transcripts.py` | `_timestamp_to_seconds` | [L407-L412](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L407-L412) |
| `src/ypbrief/transcripts.py` | `_normalize_vtt_caption_text` | [L415-L418](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L415-L418) |
| `src/ypbrief/transcripts.py` | `_caption_increment` | [L421-L428](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/transcripts.py#L421-L428) |
| `src/ypbrief/cleaner.py` | `TranscriptSegment` | [L8-L16](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/cleaner.py#L8-L16) |
| `src/ypbrief/cleaner.py` | `CleanTranscript` | [L19-L22](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/cleaner.py#L19-L22) |
| `src/ypbrief/cleaner.py` | `DEFAULT_FILLERS` | [L25-L40](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/cleaner.py#L25-L40) |
| `src/ypbrief/cleaner.py` | `_clean_text` | [L43-L52](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/cleaner.py#L43-L52) |
| `src/ypbrief/cleaner.py` | `clean_transcript` | [L55-L88](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/cleaner.py#L55-L88) |
| `src/ypbrief/config.py` | `Settings` 代理/yt-dlp 字段 | [L48-L60](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/config.py#L48-L60) |
| `src/ypbrief/config.py` | `yt_dlp_cookies_file` 路径解析 | [L108-L109](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/config.py#L108-L109) |
| `src/ypbrief/config.py` | `proxy_enabled` / `iproyal_proxy_url` / `youtube_proxy_url` / `yt_dlp_proxy_url` | [L113-L151](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/config.py#L113-L151) |
| `src/ypbrief/config.py` | 环境变量 → 字段映射 | [L183-L195](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/config.py#L183-L195) |
| `src/ypbrief/video_processor.py` | `VideoProcessor.process` | [L78-L130](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/video_processor.py#L78-L130) |
| `src/ypbrief/archive.py` | `ArchiveService.update_channel`（fetch + clean + save） | [L54-L98](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/archive.py#L54-L98) |
| `src/ypbrief/database.py` | `Videos` 字幕列 / `Subtitles` 表 / FTS5 | [L93-L132](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/database.py#L93-L132) |
| `src/ypbrief/database.py` | `save_transcript` | [L879-L919](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/database.py#L879-L919) |
| `src/ypbrief/summarizer.py` | `summarize_video`（消费 `transcript_clean`） | [L16-L45](file:///Users/xingan/Documents/software/aiengine/ypbrief/src/ypbrief/summarizer.py#L16-L45) |

## 8 参考资料

- yt-dlp 官方文档：字幕下载选项 `--write-subs`、`--write-auto-subs`、`--sub-langs`、`--sub-format`、`--sleep-interval`、`--max-sleep-interval`、`--retries`、`--proxy`、`--cookies`、`--cookies-from-browser`，见 <https://github.com/yt-dlp/yt-dlp> 与官方 README / Wiki。
- WebVTT（Web Video Text Tracks）格式规范：W3C 标准，时间行格式 `HH:MM:SS.mmm --> HH:MM:SS.mmm`，cue 间以空行分隔，见 <https://www.w3.org/TR/webvtt1/>。
- yt-dlp 提取器返回的 `subtitles` / `automatic_captions` 结构说明：`info_dict` 中各语言下为格式列表，翻译字幕条目含 `tlang` 字段，见 yt-dlp 源码 `YoutubeDL` 与 YouTube 提取器实现（<https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/youtube.py>）。
