"""Deterministic fallback renderer for already-downloaded video resources."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from beatodds.agents.access_tools import sync_video_render_artifacts
from beatodds.agents.models import utc_now

KEY_TERMS = [
    "习近平",
    "總書記",
    "总书记",
    "接班",
    "二十一大",
    "二十大",
    "四中全会",
    "中央全会",
    "党主席",
    "健康",
    "去世",
    "下台",
    "退休",
    "政变",
    "军",
    "蔡奇",
    "丁薛祥",
    "李强",
]


@dataclass
class SubtitleEntry:
    index: int
    start: str
    end: str
    text: str


def finalize_video_resource_report(resource_dir: Path) -> dict[str, Any]:
    """Create a minimal complete video report from local metadata and transcript files."""
    resource_dir = Path(resource_dir)
    processor_path = resource_dir / "resource_processor.json"
    if not processor_path.exists():
        raise FileNotFoundError(f"Missing resource_processor.json: {processor_path}")
    _write_stage_lock(
        resource_dir / "video_render.lock.json",
        status="running",
        command="beatodds finalize_video_resource_report",
    )
    try:
        processor = json.loads(processor_path.read_text(encoding="utf-8"))
        title = str(processor.get("title") or resource_dir.name)
        url = str(processor.get("url") or "")
        metadata = _load_metadata(resource_dir, processor)
        entries = _load_subtitle_entries(resource_dir)
        if not entries:
            _run_whisper_if_video_exists(resource_dir, metadata["source_url"])
            entries = _load_subtitle_entries(resource_dir)
        if not entries:
            raise RuntimeError("No transcript or ASR entries available for video report.")
        selected = _select_entries(entries)
        cover = _prepare_cover(resource_dir)

        _write_video_metadata(resource_dir, processor, metadata)
        _write_claims(resource_dir, selected, title)
        _write_parse_report(resource_dir, processor, metadata, entries, selected)
        _write_evidence_card(resource_dir, processor, metadata, selected)
        _write_frame_index(resource_dir, cover)
        tex_path = _write_video_report_tex(resource_dir, processor, metadata, selected, cover)
        pdf_path = _compile_tex(resource_dir, tex_path)
        _write_artifact_index(resource_dir, processor, metadata, entries, pdf_path)
        _update_locks(resource_dir)
        synced = sync_video_render_artifacts(resource_dir, rewrite_artifact_index=False)
        return {
            "status": "ok",
            "resource_dir": str(resource_dir),
            "title": title,
            "url": url,
            "subtitle_entries": len(entries),
            "selected_entries": len(selected),
            "video_report_pdf": str(pdf_path),
            "render_status": (synced.get("render") or {}).get("render_status"),
        }
    except Exception as exc:
        _write_stage_lock(
            resource_dir / "video_render.lock.json",
            status="failed",
            command="beatodds finalize_video_resource_report",
            error=str(exc),
        )
        raise


def _load_metadata(resource_dir: Path, processor: dict[str, Any]) -> dict[str, Any]:
    raw_path = resource_dir / "video_metadata_raw.json"
    raw = {}
    if raw_path.exists():
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
    return {
        "title": raw.get("title") or processor.get("title") or resource_dir.name,
        "video_id": raw.get("id") or processor.get("video_id") or "",
        "channel": raw.get("channel") or raw.get("uploader") or processor.get("author") or "",
        "channel_id": raw.get("channel_id") or processor.get("author_id") or "",
        "published_at": raw.get("upload_date") or processor.get("published_at") or "",
        "duration_seconds": raw.get("duration") or processor.get("duration_seconds") or 0,
        "view_count": raw.get("view_count") or (processor.get("stats") or {}).get("view_count"),
        "like_count": raw.get("like_count") or (processor.get("stats") or {}).get("like_count"),
        "comment_count": raw.get("comment_count")
        or (processor.get("stats") or {}).get("comment_count"),
        "thumbnail": raw.get("thumbnail") or "",
        "source_url": raw.get("webpage_url") or processor.get("url") or "",
        "description": raw.get("description") or processor.get("description") or "",
    }


def _load_subtitle_entries(resource_dir: Path) -> list[SubtitleEntry]:
    path = _find_subtitle(resource_dir)
    if path is None:
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    blocks = re.split(r"\n\s*\n", text.strip())
    entries: list[SubtitleEntry] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        time_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if time_index is None:
            continue
        times = lines[time_index]
        start, end = [part.strip().replace(",", ".") for part in times.split("-->", 1)]
        body = _clean_subtitle_text(" ".join(lines[time_index + 1 :]))
        if not body:
            continue
        try:
            index = int(lines[0])
        except ValueError:
            index = len(entries) + 1
        entries.append(SubtitleEntry(index=index, start=start, end=end, text=body))
    return entries


def _find_subtitle(resource_dir: Path) -> Path | None:
    for name in ["transcript.srt", "audio.srt"]:
        path = resource_dir / name
        if path.exists():
            return path
    candidates = sorted(
        resource_dir.glob("*.srt"),
        key=lambda item: item.stat().st_size,
        reverse=True,
    )
    if candidates:
        chosen = candidates[0]
        target = resource_dir / "transcript.srt"
        if chosen != target and not target.exists():
            shutil.copyfile(chosen, target)
        return target if target.exists() else chosen
    return None


def _run_whisper_if_video_exists(resource_dir: Path, url: str) -> None:
    video = _find_video(resource_dir) or _download_video_for_asr(resource_dir, url)
    if video is None:
        return
    command = [
        "whisper",
        video.name,
        "--model",
        "small",
        "--language",
        "zh",
        "--task",
        "transcribe",
        "--output_format",
        "srt",
        "--output_dir",
        ".",
    ]
    lock_path = resource_dir / "asr.lock.json"
    process = subprocess.Popen(
        command,
        cwd=resource_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _write_json(
        lock_path,
        {
            "status": "running",
            "pid": process.pid,
            "started_at": utc_now().isoformat(),
            "updated_at": utc_now().isoformat(),
            "command": " ".join(command),
        },
    )
    stdout, stderr = process.communicate(timeout=1800)
    _append_log(resource_dir / "download_log.md", "## Fallback Whisper ASR", stdout, stderr)
    if process.returncode != 0:
        _write_stage_lock(
            lock_path,
            status="failed",
            command=" ".join(command),
            error=stderr[-1000:],
        )
        raise RuntimeError(f"Whisper ASR failed: {stderr[-1000:]}")
    _write_stage_lock(lock_path, status="complete", command=" ".join(command))


def _find_video(resource_dir: Path) -> Path | None:
    candidates = []
    for pattern in ["*.mp4", "*.mkv", "*.webm", "*.m4a", "*.mp3", "*.wav"]:
        candidates.extend(resource_dir.glob(pattern))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_size)


def _download_video_for_asr(resource_dir: Path, url: str) -> Path | None:
    if not url:
        return None
    yt_dlp = shutil.which("yt-dlp")
    command = [yt_dlp] if yt_dlp else ["uvx", "yt-dlp"]
    command.extend([
        "--no-warnings",
        "-f",
        "bv*[height<=480]+ba/b[height<=480]/best[height<=480]/best",
        "--merge-output-format",
        "mp4",
        "-o",
        "%(title).160B [%(id)s].%(ext)s",
    ])
    cookie_file = Path("data/secrets/www.bilibili.com_cookies.txt")
    if "bilibili" in url.lower() and cookie_file.exists():
        command.extend(["--cookies", str(cookie_file)])
    command.append(url)
    result = subprocess.run(
        command,
        cwd=resource_dir,
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )
    _append_log(
        resource_dir / "download_log.md",
        "## Fallback yt-dlp video download",
        result.stdout,
        result.stderr,
    )
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp fallback download failed: {result.stderr[-1000:]}")
    return _find_video(resource_dir)


def _select_entries(entries: list[SubtitleEntry], limit: int = 10) -> list[SubtitleEntry]:
    scored = []
    for entry in entries:
        score = sum(1 for term in KEY_TERMS if term in entry.text)
        if score:
            scored.append((score, entry.index, entry))
    if not scored:
        stride = max(1, len(entries) // max(1, limit))
        return entries[::stride][:limit]
    selected = [item[2] for item in sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]]
    return sorted(selected, key=lambda item: item.index)


def _write_video_metadata(
    resource_dir: Path,
    processor: dict[str, Any],
    metadata: dict[str, Any],
) -> None:
    payload = dict(metadata)
    payload.update({
        "platform": processor.get("platform") or processor.get("resource_type") or "",
        "content_access": processor.get("content_access") or {},
        "generated_by": "beatodds_video_reporter_fallback",
        "generated_at": utc_now().isoformat(),
    })
    _write_json(resource_dir / "video_metadata.json", payload)


def _write_claims(resource_dir: Path, selected: list[SubtitleEntry], title: str) -> None:
    lines = []
    for idx, entry in enumerate(selected, start=1):
        payload = {
            "id": f"c{idx}",
            "timestamp_start": entry.start,
            "timestamp_end": entry.end,
            "claim": entry.text,
            "claim_type": "asr_excerpt",
            "source": title,
            "supports": "needs_main_agent_interpretation",
            "confidence": "medium",
        }
        lines.append(json.dumps(payload, ensure_ascii=False))
    (resource_dir / "claims.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_parse_report(
    resource_dir: Path,
    processor: dict[str, Any],
    metadata: dict[str, Any],
    entries: list[SubtitleEntry],
    selected: list[SubtitleEntry],
) -> None:
    lines = [
        "# 视频解析报告",
        "",
        "## 基本信息",
        "",
        f"- 标题：`《{metadata['title']}》`",
        f"- URL：`{metadata['source_url']}`",
        f"- 作者：`{metadata['channel']}`",
        f"- 发布时间：`{metadata['published_at']}`",
        f"- 时长：`{metadata['duration_seconds']}s`",
        "",
        "## 处理说明",
        "",
        "- 本报告由本地 fallback 从已落盘 metadata、封面和字幕/ASR 文件生成。",
        "- 已有字幕条数：`{}`。".format(len(entries)),
        "- 下列摘录来自字幕/ASR 正文，不来自标题、简介或评论。",
        "- 该 fallback 不做深度政治判断，只把可审查正文材料整理给主 agent。",
        "",
        "## 关键时间戳摘录",
        "",
    ]
    for entry in selected:
        lines.extend([
            f"### {entry.start}--{entry.end}",
            "",
            entry.text,
            "",
        ])
    lines.extend([
        "## 对 forecast 的使用边界",
        "",
        "- 可用于判断视频是否真的讨论了本题相关机制、人物和时间窗口。",
        "- 不能单独作为官方事实 source。",
        "- 需要主 agent 结合 resolution、官方材料和其他 source 再解释其概率意义。",
        "",
        "## 局限",
        "",
        "- ASR 可能有错字、断句和繁简混杂。",
        "- 未做评论区抽样。",
        "- 视觉内容仅保留封面或已有图片，没有逐帧深度审查。",
    ])
    (resource_dir / "video_parse_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_evidence_card(
    resource_dir: Path,
    processor: dict[str, Any],
    metadata: dict[str, Any],
    selected: list[SubtitleEntry],
) -> None:
    lines = [
        "# 来源卡片",
        "",
        f"- 标题: {metadata['title']}",
        f"- url: {metadata['source_url']}",
        f"- 来源: `{metadata['channel']}`",
        f"- category: `{processor.get('source_category') or 'expert_social'}`",
        "- provider: `process_resource + local_video_reporter_fallback`",
        f"- published_at: `{metadata['published_at']}`",
        f"- retrieved_at: `{utc_now().date().isoformat()}`",
        "- content_access: `metadata + transcript_or_asr + fallback_pdf`",
        "- bias_note: `视频正文需由主 agent 结合其他 source 解释；不能作官方事实 source。`",
        "",
        "## 正文摘录",
        "",
    ]
    for entry in selected[:6]:
        lines.append(f"- `{entry.start}--{entry.end}`: {entry.text}")
    lines.extend([
        "",
        "## 对 forecast 的意义",
        "",
        "- 该卡片确认视频正文已可读，可进入 evidence review。",
        "- 概率方向必须由主 agent 根据 resolution 和其他证据判定。",
        "",
        "## 局限",
        "",
        "- fallback 只做正文整理，不做完整 source 背景调查。",
    ])
    (resource_dir / "evidence_card.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_frame_index(resource_dir: Path, cover: Path | None) -> None:
    lines = [
        "# Frame Index",
        "",
        "## 已检查视觉材料",
        "",
    ]
    if cover:
        lines.append(f"- `{cover.name}`: 视频封面或已落盘预览图。")
    else:
        lines.append("- 未找到可复用封面或帧图。")
    lines.extend([
        "",
        "## 说明",
        "",
        "本 fallback 未进行逐帧语义筛选；正文证据主要来自字幕/ASR。",
    ])
    (resource_dir / "frame_index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _prepare_cover(resource_dir: Path) -> Path | None:
    for path in sorted(
        resource_dir.glob("*.png"),
        key=lambda item: item.stat().st_size,
        reverse=True,
    ):
        target = resource_dir / "video_cover.png"
        if path != target:
            shutil.copyfile(path, target)
        return target
    return None


def _write_video_report_tex(
    resource_dir: Path,
    processor: dict[str, Any],
    metadata: dict[str, Any],
    selected: list[SubtitleEntry],
    cover: Path | None,
) -> Path:
    cover_block = ""
    if cover:
        cover_block = "\n".join([
            r"\begin{figure}[H]",
            r"\centering",
            rf"\includegraphics[width=0.82\textwidth]{{{_latex_escape_path(cover.name)}}}",
            r"\caption{视频封面或预览图}",
            r"\end{figure}",
        ])
    excerpt_items = "\n".join(
        rf"\item \texttt{{{_escape_latex(entry.start)}--{_escape_latex(entry.end)}}}: "
        rf"{_escape_latex(entry.text)}"
        for entry in selected
    )
    tex = "\n".join([
        r"\documentclass[11pt,a4paper]{ctexart}",
        r"\usepackage[margin=22mm]{geometry}",
        r"\usepackage{graphicx}",
        r"\usepackage{booktabs}",
        r"\usepackage{float}",
        r"\usepackage{xurl}",
        r"\usepackage[colorlinks=true,urlcolor=blue]{hyperref}",
        r"\setlength{\parindent}{0pt}",
        r"\setlength{\parskip}{6pt}",
        rf"\title{{视频证据简报\\《{_escape_latex(metadata['title'])}》}}",
        r"\author{BeatOdds local video reporter fallback}",
        rf"\date{{{_escape_latex(utc_now().date().isoformat())}}}",
        r"\begin{document}",
        r"\maketitle",
        cover_block,
        r"\section*{元数据}",
        r"\begin{center}",
        r"\renewcommand{\arraystretch}{1.2}",
        r"\begin{tabular}{ll}",
        r"\toprule",
        r"字段 & 数值 \\",
        r"\midrule",
        rf"作者 & {_escape_latex(metadata['channel'])} \\",
        rf"发布时间 & {_escape_latex(metadata['published_at'])} \\",
        rf"时长 & {_escape_latex(metadata['duration_seconds'])} 秒 \\",
        rf"播放量 & {_escape_latex(metadata.get('view_count') or '')} \\",
        rf"点赞量 & {_escape_latex(metadata.get('like_count') or '')} \\",
        rf"评论数 & {_escape_latex(metadata.get('comment_count') or '')} \\",
        rf"链接 & \url{{{_escape_latex(metadata['source_url'])}}} \\",
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{center}",
        r"\section*{处理说明}",
        (
            "本报告从已下载视频资源、metadata 和字幕/ASR 文件生成。"
            "以下摘录来自视频正文字幕，不来自标题、简介或评论。"
        ),
        r"\section*{关键时间戳摘录}",
        r"\begin{itemize}",
        excerpt_items,
        r"\end{itemize}",
        r"\section*{使用边界}",
        (
            "该视频可作为中文社媒/视频 source 的正文材料进入 evidence review。"
            "它不具备官方确认效力，主 agent 需要结合 resolution 和其他 source "
            "判断其概率意义。"
        ),
        r"\section*{局限}",
        r"\begin{itemize}",
        r"\item ASR 可能存在错字、断句和繁简混杂。",
        r"\item fallback 未做评论区抽样和逐帧深度审查。",
        r"\item 如果需要更强证据质量，应继续由视频 skill/subagent 做完整深处理。",
        r"\end{itemize}",
        r"\end{document}",
    ])
    tex_path = resource_dir / "video_report.tex"
    tex_path.write_text(tex, encoding="utf-8")
    return tex_path


def _compile_tex(resource_dir: Path, tex_path: Path) -> Path:
    command = [
        "xelatex",
        "-interaction=nonstopmode",
        "-halt-on-error",
        tex_path.name,
    ]
    result = subprocess.run(
        command,
        cwd=resource_dir,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    (resource_dir / "xelatex_stdout.log").write_text(
        result.stdout + "\n" + result.stderr,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(f"xelatex failed for {tex_path}: {result.stderr[-1000:]}")
    return resource_dir / "video_report.pdf"


def _write_artifact_index(
    resource_dir: Path,
    processor: dict[str, Any],
    metadata: dict[str, Any],
    entries: list[SubtitleEntry],
    pdf_path: Path,
) -> None:
    expected = [
        "video_metadata.json",
        "video_parse_report.md",
        "claims.jsonl",
        "evidence_card.md",
        "video_report.tex",
        "video_report.pdf",
        "artifact_index.md",
        "frame_index.md",
        "transcript.srt",
    ]
    lines = [
        "# Resource Artifact Index",
        "",
        f"- title: 《{metadata['title']}》",
        f"- url: {metadata['source_url']}",
        f"- resource_type: `{processor.get('resource_type', '')}`",
        "- processor_status: `video_render_complete`",
        "- render_status: `complete`",
        "- generated_by: `beatodds_video_reporter_fallback`",
        f"- subtitle_entries: `{len(entries)}`",
        f"- video_report_pdf: `{pdf_path.name}`",
        "",
        "## Artifacts",
        "",
    ]
    for item in expected:
        exists = (resource_dir / item).exists() or item == "artifact_index.md"
        status = "exists" if exists else "missing"
        lines.append(f"- `{item}`: `{status}`")
    lines.extend([
        "",
        "## Coverage",
        "",
        "- `video_body_status`: `complete_report`",
        "- `transcript`: `available`",
        "- `source_note`: fallback report generated from local subtitle/ASR and metadata.",
    ])
    (resource_dir / "artifact_index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _update_locks(resource_dir: Path) -> None:
    for name in ["video_render.lock.json", "asr.lock.json"]:
        path = resource_dir / name
        if path.exists() or name == "video_render.lock.json":
            _write_stage_lock(path, status="complete", command="beatodds video report finalized")


def _write_stage_lock(
    path: Path,
    *,
    status: str,
    command: str,
    error: str = "",
) -> None:
    now = utc_now().isoformat()
    data: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except json.JSONDecodeError:
            data = {}
    data.update({
        "status": status,
        "pid": data.get("pid") or os.getpid(),
        "updated_at": now,
        "command": command or data.get("command", ""),
        "error": error,
    })
    if status == "running":
        data.setdefault("started_at", now)
    if status in {"complete", "completed", "failed", "error"}:
        data["completed_at"] = now
    _write_json(path, data)


def _append_log(path: Path, title: str, stdout: str, stderr: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n\n{title}\n\n")
        if stdout.strip():
            handle.write("### stdout\n\n```text\n")
            handle.write(stdout[-4000:])
            handle.write("\n```\n")
        if stderr.strip():
            handle.write("### stderr\n\n```text\n")
            handle.write(stderr[-4000:])
            handle.write("\n```\n")


def _clean_subtitle_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\{[^}]+\}", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _escape_latex(value: Any) -> str:
    text = str(value)
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in text)


def _latex_escape_path(path: str) -> str:
    return path.replace("\\", "/")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
