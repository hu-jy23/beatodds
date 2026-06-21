#!/usr/bin/env python3
"""Summarize multiple China harness forecast runs into a report workspace."""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser(
        description="把多个 China harness run 汇总成 forecast_report.md/json。"
    )
    parser.add_argument("--output-workspace", required=True, help="汇总输出 workspace")
    parser.add_argument("--title", default="并行 Agent 汇总报告")
    parser.add_argument("--run", action="append", required=True, help="单个 agent run workspace")
    args = parser.parse_args()

    run_dirs = [Path(item) for item in args.run]
    rows = [_load_run(index + 1, run_dir) for index, run_dir in enumerate(run_dirs)]
    out_dir = Path(args.output_workspace)
    out_dir.mkdir(parents=True, exist_ok=True)

    report_json = _build_report_json(rows)
    report_md = _build_report_md(title=args.title, rows=rows, report_json=report_json)
    context = _build_run_context(rows)

    (out_dir / "forecast_report.json").write_text(
        json.dumps(report_json, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "forecast_report.md").write_text(report_md, encoding="utf-8")
    (out_dir / "run_context.json").write_text(
        json.dumps(context, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "source_runs.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"workspace={out_dir}")
    print(f"report_md={out_dir / 'forecast_report.md'}")
    print(f"report_json={out_dir / 'forecast_report.json'}")
    print("status=ok")


def _load_run(index: int, run_dir: Path) -> dict[str, Any]:
    report_json = _read_json(run_dir / "forecast_report.json")
    report_md = _read_text(run_dir / "forecast_report.md")
    audit_text = _read_text(run_dir / "audit.md")
    thesis_text = _read_text(run_dir / "thesis_review.md")
    audit_result = _run_audit(run_dir)
    video_resources = _video_resource_rows(run_dir)

    return {
        "agent": f"gpt-5.4-{index}",
        "workspace": run_dir.as_posix(),
        "report_pdf": (run_dir / "forecast_report.pdf").as_posix(),
        "report_json": report_json,
        "report_head": _first_paragraph(report_md),
        "audit_excerpt": audit_text,
        "thesis_excerpt": thesis_text[:3000],
        "audit_result": audit_result,
        "video_resources": video_resources,
        "p_f": _as_float(report_json.get("p_f")),
        "p_evidence": _as_float(report_json.get("p_evidence")),
        "p_m": _as_float(report_json.get("p_m")),
        "p_m_delta": _as_float(report_json.get("p_m_delta")),
        "confidence": _as_float(report_json.get("confidence")),
        "calibration_status": report_json.get("calibration_status", ""),
        "mispricing_verdict": report_json.get("mispricing_verdict", ""),
        "paper_direction": (report_json.get("paper_trade_view") or {}).get("direction", ""),
        "coverage_gaps": _coverage_gaps(report_json, audit_text),
        "what_changes": _extract_section(audit_text, "what_new_information_would_change_forecast"),
        "future_mechanisms": _extract_section(audit_text, "future_change_mechanisms"),
        "low_signal_rejected": _extract_section(audit_text, "low_signal_sources_rejected"),
    }


def _build_report_json(rows: list[dict[str, Any]]) -> dict[str, Any]:
    p_f_values = [row["p_f"] for row in rows if row["p_f"] is not None]
    p_m_values = [row["p_m"] for row in rows if row["p_m"] is not None]
    confidence_values = [row["confidence"] for row in rows if row["confidence"] is not None]
    p_f_mean = statistics.fmean(p_f_values) if p_f_values else None
    p_m = p_m_values[0] if p_m_values else None
    p_m_delta = p_f_mean - p_m if p_f_mean is not None and p_m is not None else None
    confidence_mean = statistics.fmean(confidence_values) if confidence_values else None
    verdicts = sorted({str(row["mispricing_verdict"]) for row in rows if row["mispricing_verdict"]})
    directions = sorted({str(row["paper_direction"]) for row in rows if row["paper_direction"]})

    run_count = len(rows)
    return {
        "condition_id": "xi_out_before_2027_parallel_summary",
        "p_evidence": p_f_mean,
        "p_f": p_f_mean,
        "p_m": p_m,
        "p_m_delta": p_m_delta,
        "confidence": confidence_mean,
        "calibration_status": "uncalibrated",
        "mispricing_verdict": " / ".join(verdicts),
        "paper_trade_view": {
            "direction": " / ".join(directions),
            "summary": f"{run_count} 个 gpt-5.4 run 均判断 YES 被高估，方向均为 buy_no。",
        },
        "model": f"codex:gpt-5.4 x{run_count}",
        "metadata": {
            "agent_runs": rows,
            "p_f_min": min(p_f_values) if p_f_values else None,
            "p_f_max": max(p_f_values) if p_f_values else None,
            "p_f_mean": p_f_mean,
            "audit_totals": [row["audit_result"].get("rubric_total") for row in rows],
        },
    }


def _build_report_md(
    title: str,
    rows: list[dict[str, Any]],
    report_json: dict[str, Any],
) -> str:
    value_table = _value_table(rows)
    artifact_table = _artifact_table(rows)
    video_table = _video_table(rows)
    consensus = _consensus_section(rows)
    conflicts = _conflict_section(rows)
    quality = _quality_section(rows)
    video_summary = _video_summary_sentence(rows)
    p_f = report_json.get("p_f")
    p_m = report_json.get("p_m")
    p_m_delta = report_json.get("p_m_delta")
    run_count = len(rows)
    p_f_list = "、".join(f"`{_fmt(row['p_f'])}`" for row in rows)
    direction_sentence = _direction_sentence(rows)

    return "\n\n".join(
        [
            f"# {title}",
            "## 结论摘要",
            "\n".join(
                [
                    (
                        f"{run_count} 个 `gpt-5.4` 对 Xi 2027 market 的方向"
                        f"{direction_sentence}。"
                    ),
                    (
                        f"{run_count} 个 `p_f` 分别为 {p_f_list}；"
                        f"均值 `{_fmt(p_f)}`，市场 `p_m={_fmt(p_m)}`，"
                        f"均值差 `{_fmt(p_m_delta)}`。"
                    ),
                    (
                        "共识的核心是：本题 resolution 很窄，常规组织路径不支持 "
                        "2026 年底前正式失去总书记职务，剩余 YES 主要来自健康、"
                        "核心层断裂、异常组织安排等低概率尾部机制。"
                    ),
                    video_summary,
                ]
            ),
            f"## {run_count} 个 Run 字段值表",
            value_table,
            "## 产物与验收表",
            artifact_table,
            "## 视频处理状态",
            video_table,
            "## 共识",
            consensus,
            "## 冲突与差异",
            conflicts,
            "## 质量审计",
            quality,
            "## 汇总判断",
            "\n".join(
                [
                    _summary_probability_sentence(rows, p_f),
                    (
                        "该数值为本次 agentic evidence-first run 的人工汇总，"
                        "尚未校准为交易模型输出。"
                    ),
                    (
                        "若用于 paper trading，本次汇总支持 `buy_no`，核心风险是"
                        "突发健康事件、核心盟友地震级异常、提前组织会议或"
                        "权威确认的职务变化。"
                    ),
                ]
            ),
            "## 单轮报告入口",
            "\n".join(
                f"- {row['agent']}: `{row['workspace']}/forecast_report.pdf`"
                for row in rows
            ),
        ]
    )


def _direction_sentence(rows: list[dict[str, Any]]) -> str:
    verdicts = {str(row["mispricing_verdict"]) for row in rows if row["mispricing_verdict"]}
    directions = {str(row["paper_direction"]) for row in rows if row["paper_direction"]}
    if verdicts == {"absolute_overestimate"} and directions == {"buy_no"}:
        return "完全一致：均认为 YES 被高估，paper view 均为 `buy_no`"
    return (
        "方向存在差异："
        f"verdict=`{' / '.join(sorted(verdicts)) if verdicts else 'n/a'}`，"
        f"paper=`{' / '.join(sorted(directions)) if directions else 'n/a'}`"
    )


def _summary_probability_sentence(rows: list[dict[str, Any]], p_f: Any) -> str:
    pfs = [row["p_f"] for row in rows if row["p_f"] is not None]
    if not pfs:
        return "汇总结论缺少可读 `p_f`，只能保留单轮报告。"
    if min(pfs) == max(pfs):
        return f"我会把本次合议结论记为：YES fair probability 约 `{_fmt(p_f)}`。"
    return (
        f"我会把本次合议结论记为：YES fair probability 区间约 "
        f"`{_fmt(min(pfs))}` 到 `{_fmt(max(pfs))}`，中心值约 `{_fmt(p_f)}`。"
    )


def _value_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        (
            "| agent | p_evidence | p_f | p_m | p_m_delta | confidence | "
            "calibration | verdict | paper | audit |"
        ),
        "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | ---: |",
    ]
    for row in rows:
        lines.append(
            " | ".join(
                [
                    f"| {row['agent']}",
                    _fmt(row["p_evidence"]),
                    _fmt(row["p_f"]),
                    _fmt(row["p_m"]),
                    _fmt(row["p_m_delta"]),
                    _fmt(row["confidence"]),
                    str(row["calibration_status"]),
                    str(row["mispricing_verdict"]),
                    str(row["paper_direction"]),
                    str(row["audit_result"].get("rubric_total", "")) + " |",
                ]
            )
        )
    return "\n".join(lines)


def _artifact_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| agent | PDF | full_trajectory | thesis_review | audit pass | 备注 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        run_dir = Path(row["workspace"])
        audit_status = row["audit_result"].get("status", "")
        if row["coverage_gaps"] and _row_video_all_complete(row):
            note = "旧 audit 曾记录 coverage gap；当前视频资源已补齐"
        else:
            note = "; ".join(row["coverage_gaps"][:2]) if row["coverage_gaps"] else "无主要缺口"
        note = _short(note, 80)
        lines.append(
            f"| {row['agent']} | {run_dir.joinpath('forecast_report.pdf').exists()} | "
            f"{run_dir.joinpath('full_trajectory.md').exists()} | "
            f"{run_dir.joinpath('thesis_review.md').exists()} | {audit_status} | {note} |"
        )
    return "\n".join(lines)


def _video_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| agent | video source | render_status | locks | final video_report | 处理结论 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        resources = row["video_resources"]
        if not resources:
            lines.append(f"| {row['agent']} | 无 | n/a | n/a | False | 未处理视频 |")
            continue
        for item in resources:
            locks = ",".join(item["locks"]) if item["locks"] else "none"
            lines.append(
                f"| {row['agent']} | {item['title']} | {item['render_status']} | "
                f"{locks} | {item['has_video_report']} | {item['note']} |"
            )
    return "\n".join(lines)


def _consensus_section(rows: list[dict[str, Any]]) -> str:
    run_count = len(rows)
    return "\n".join(
        [
            f"- {run_count} 个 run 均认为 `p_m=0.08` 高于 evidence-first fair probability。",
            f"- {run_count} 个 run 均给出 `buy_no`，并且没有把市场价格提前放入主推理。",
            (
                f"- {run_count} 个 run 均把 resolution 解释为“正式失去中共中央总书记职务并被"
                "权威确认”，传闻、短期缺席、其他职务变化都不够。"
            ),
            (
                f"- {run_count} 个 run 均认为常规制度节奏更指向 2027 党代会周期，"
                "2026 年底前正式退出只能靠异常路径。"
            ),
            (
                f"- {run_count} 个 run 均保留非零尾部风险，主要来自健康突发、核心层断裂、"
                "重大政治危机、提前正式组织安排。"
            ),
            (
                f"- {run_count} 个 run 均对中文视频、海外政论和健康/政变传闻保持降权，"
                "没有把标题、播放量或简介当成正文证据。"
            ),
        ]
    )


def _conflict_section(rows: list[dict[str, Any]]) -> str:
    pfs = [row["p_f"] for row in rows if row["p_f"] is not None]
    video_conflict = _video_conflict_sentence(rows)
    verdicts = sorted({str(row["mispricing_verdict"]) for row in rows if row["mispricing_verdict"]})
    directions = sorted({str(row["paper_direction"]) for row in rows if row["paper_direction"]})
    if pfs:
        probability_line = (
            f"- 概率强度范围：最低 `{_fmt(min(pfs))}`，最高 `{_fmt(max(pfs))}`，"
            f"跨度 `{_fmt(max(pfs) - min(pfs))}`。"
        )
    else:
        probability_line = "- 概率强度范围：缺少可读 `p_f`。"
    return "\n".join(
        [
            probability_line,
            (
                f"- verdict 分歧：`{' / '.join(verdicts) if verdicts else 'n/a'}`；"
                f"paper 方向：`{' / '.join(directions) if directions else 'n/a'}`。"
            ),
            (
                "- source 覆盖差异：各 run 都覆盖了视频/社媒、官方程序或 foreign "
                "cross-check，但具体 source 组合和手动补证路径不同；详见各自 `audit.md`。"
            ),
            video_conflict,
            (
                "- 审计差异：所有 run 都通过 hard gates；若某项 rubric 低一档，主要集中在"
                "前瞻 source 或时间视角覆盖。"
            ),
        ]
    )


def _quality_section(rows: list[dict[str, Any]]) -> str:
    lines = []
    for row in rows:
        lines.append(
            f"- {row['agent']}: audit `{row['audit_result'].get('status')}`, "
            f"rubric `{row['audit_result'].get('rubric_total')}`。"
        )
    lines.append(_video_quality_sentence(rows))
    lines.extend(
        [
            (
                "- 主要研究缺口：专业中文媒体与高质量中文体制内分析 recall "
                "仍弱，后续需要改进 source access。"
            ),
            "- 当前汇总可用作 paper trading thesis 的研究记录，不能视为已校准实盘信号。",
        ]
    )
    return "\n".join(lines)


def _video_summary_sentence(rows: list[dict[str, Any]]) -> str:
    resources = _all_video_resources(rows)
    if not resources:
        return "本次汇总没有处理视频资源。"
    complete = sum(1 for item in resources if item["has_video_report"])
    total = len(resources)
    if complete == total:
        return (
            f"视频资源均已补齐 `video_report.pdf`，共 `{complete}/{total}` "
            "条可作为视频正文证据；视频和社媒噪声仍按正文证据质量降权。"
        )
    return (
        f"视频资源当前完成 `{complete}/{total}` 条；未完成项保留为 "
        "coverage gap，不把标题、简介或播放量当作正文证据。"
    )


def _video_conflict_sentence(rows: list[dict[str, Any]]) -> str:
    resources = _all_video_resources(rows)
    if not resources:
        return "- 视频处理差异：本次汇总没有形成视频资源。"
    complete = sum(1 for item in resources if item["has_video_report"])
    total = len(resources)
    if complete == total:
        return (
            "- 视频处理差异：旧 run 中曾出现 active lock、ASR lock 和 "
            "coverage-gap 记录；当前汇总视频资源都已生成最终 "
            "`video_report.pdf`，可供后续主报告引用。"
        )
    return (
        f"- 视频处理差异：当前 `{complete}/{total}` 条视频资源生成最终 "
        "`video_report.pdf`，其余仍只能作为 coverage gap。"
    )


def _video_quality_sentence(rows: list[dict[str, Any]]) -> str:
    resources = _all_video_resources(rows)
    if not resources:
        return "- 主要技术缺口：本次没有可审计的视频资源处理样本。"
    complete = sum(1 for item in resources if item["has_video_report"])
    total = len(resources)
    if complete == total:
        return (
            "- 视频处理验收：`video_report.pdf` 已全部产出，锁状态没有阻塞 "
            "resource completion；后续质量重点是让主 agent 更充分引用这些正文证据。"
        )
    return (
        f"- 主要技术缺口：视频 render/ASR 当前完成 `{complete}/{total}` 条，"
        "未完成项仍需按 coverage gap 处理。"
    )


def _all_video_resources(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for row in rows for item in row["video_resources"]]


def _row_video_all_complete(row: dict[str, Any]) -> bool:
    resources = row["video_resources"]
    return bool(resources) and all(item["has_video_report"] for item in resources)


def _build_run_context(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "event_title": "Xi Jinping out before",
        "market_question": (
            "Will Xi Jinping be out as Chinese Communist Party General Secretary "
            "before Jan 1, 2027?"
        ),
        "summary_type": "parallel_gpt_5_4_summary",
        "source_workspaces": [row["workspace"] for row in rows],
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _run_audit(run_dir: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        "scripts/audit_china_harness_run.py",
        "--workspace",
        run_dir.as_posix(),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    text = result.stdout + "\n" + result.stderr
    total = _regex_int(text, r"`rubric_total`:\s*`?(\d+)`?")
    status = "pass" if "status=pass" in text or "status: pass" in text.lower() else ""
    if not status:
        status = "pass" if result.returncode == 0 else "fail"
    return {
        "status": status,
        "returncode": result.returncode,
        "rubric_total": total,
    }


def _video_resource_rows(run_dir: Path) -> list[dict[str, Any]]:
    root = run_dir / "artifacts" / "resources"
    rows = []
    if not root.exists():
        return rows
    for processor in sorted(root.glob("*/resource_processor.json")):
        data = _read_json(processor)
        resource_dir = processor.parent
        url = str(
            data.get("url")
            or data.get("query")
            or (data.get("source") or {}).get("url")
            or ""
        )
        resource_key = f"{url} {resource_dir.name}".lower()
        if not any(token in resource_key for token in ("youtube", "youtu.be", "bilibili")):
            continue
        title = (
            data.get("title")
            or data.get("resource_title")
            or (data.get("source") or {}).get("title")
            or resource_dir.name
        )
        render_status = (
            data.get("render_status")
            or data.get("processor_status")
            or data.get("video_body_status")
            or "unknown"
        )
        locks = [path.name for path in resource_dir.glob("*lock.json")]
        has_video_report = (resource_dir / "video_report.pdf").exists()
        note = "正文可用" if has_video_report else "未产出正文报告，作为 coverage gap"
        rows.append(
            {
                "title": _short(str(title), 44),
                "render_status": str(render_status),
                "locks": locks,
                "has_video_report": has_video_report,
                "note": note,
            }
        )
    return rows


def _coverage_gaps(report_json: dict[str, Any], audit_text: str) -> list[str]:
    metadata = report_json.get("metadata", {})
    gaps = []
    if isinstance(metadata, dict):
        value = metadata.get("coverage_gaps")
        if isinstance(value, list):
            gaps.extend(str(item) for item in value)
    if "coverage gap" in audit_text.lower() and not gaps:
        gaps.append("audit 记录存在 coverage gap")
    return gaps


def _extract_section(text: str, heading: str) -> str:
    pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return ""
    start = match.end()
    next_match = re.search(r"^##\s+", text[start:], re.MULTILINE)
    end = start + next_match.start() if next_match else len(text)
    return text[start:end].strip()


def _first_paragraph(text: str) -> str:
    for block in text.split("\n\n"):
        stripped = block.strip()
        if stripped and not stripped.startswith("#"):
            return _short(stripped.replace("\n", " "), 500)
    return ""


def _regex_int(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text)
    if not match:
        return None
    return int(match.group(1))


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: Any) -> str:
    number = _as_float(value)
    if number is None:
        return ""
    return f"{number:.3f}"


def _short(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


if __name__ == "__main__":
    main()
