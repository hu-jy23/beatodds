#!/usr/bin/env python3
"""Audit whether a China harness run shows prediction-oriented exploration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REQUIRED_FILES = [
    "source_plan.md",
    "plan.md",
    "trajectory.md",
    "full_trajectory.md",
    "thesis_review.md",
    "audit.md",
    "forecast_report.md",
    "forecast_report.json",
    "forecast_report.pdf",
]

FUTURE_TERMS = [
    "未来",
    "结算",
    "改变",
    "催化",
    "发布",
    "路线图",
    "节奏",
    "窗口",
    "预测",
    "分歧",
    "变化机制",
    "什么会改变预测",
]

TRAJECTORY_TERMS = [
    "Think",
    "Evidence",
    "Next",
    "下一步",
    "信息缺口",
    "实际阅读材料",
    "可展示推理札记",
    "Source 选择说明",
    "停止",
    "继续",
]

TRAJECTORY_SOFTLINK_TERMS = [
    "见 `full_trajectory.md`",
    "见 full_trajectory.md",
    "渲染脚本会把",
    "渲染 PDF 时会自动嵌入",
    "自动嵌入 `full_trajectory.md`",
    "见下方嵌入的完整轨迹正文",
]

AUDIT_TERMS = [
    "current_state_evidence",
    "future_change_mechanisms",
    "future_or_prediction_sources_attempted",
    "low_signal_sources_rejected",
    "why_future_exploration_is_sufficient_or_blocked",
    "what_new_information_would_change_forecast",
]

RUBRIC = [
    {
        "id": "time_perspective",
        "label": "时间视角",
        "terms": ["current state", "当前状态", "resolution-day", "结算日", "未来机制", "变化机制"],
        "hard_gate": True,
    },
    {
        "id": "search_branches",
        "label": "搜索分支",
        "terms": ["搜索线", "source", "query", "分支", "候选", "search_actions"],
        "hard_gate": True,
    },
    {
        "id": "query_design",
        "label": "查询设计",
        "terms": [
            "即将",
            "roadmap",
            "preview",
            "内测",
            "上榜",
            "更新",
            "7月底",
            "发布节奏",
            "2026",
            "2027",
            "窗口",
            "日程",
            "选举",
            "APEC",
            "触发器",
            "升级路径",
            "封锁",
            "行动",
        ],
        "hard_gate": True,
    },
    {
        "id": "trajectory_causality",
        "label": "轨迹因果",
        "terms": ["信息缺口", "为什么", "下一步", "Evidence", "Think", "Next", "支持", "削弱"],
        "hard_gate": True,
    },
    {
        "id": "forward_sources",
        "label": "前瞻 source 覆盖",
        "terms": [
            "expert_social",
            "market_professional",
            "公司",
            "release",
            "model card",
            "LMArena",
            "社区",
        ],
        "hard_gate": False,
    },
    {
        "id": "counterevidence_search",
        "label": "反证搜索",
        "terms": ["反证", "推翻", "翻盘", "削弱", "不支持", "opposing", "NO", "相反"],
        "hard_gate": False,
    },
    {
        "id": "stop_condition",
        "label": "停止条件",
        "terms": ["停止", "边际", "不足", "已覆盖", "未覆盖", "blocked", "sufficient"],
        "hard_gate": False,
    },
    {
        "id": "strong_thesis",
        "label": "强结论",
        "terms": [
            "结论先行",
            "核心论证链",
            "Mispricing Verdict",
            "Paper Trade View",
            "Probability Floor",
            "市场错判",
            "尾部风险",
            "最强反方",
            "战略一致性",
            "thesis_review",
        ],
        "hard_gate": False,
    },
    {
        "id": "full_trace",
        "label": "完整轨迹",
        "terms": [
            "full_trajectory",
            "实际阅读材料",
            "材料摘录",
            "可展示推理札记",
            "拒绝或降权",
        ],
        "hard_gate": False,
    },
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="Agent run workspace")
    parser.add_argument("--json", action="store_true", help="Emit JSON only")
    args = parser.parse_args()

    result = audit_run(Path(args.workspace))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(result))


def audit_run(run_dir: Path) -> dict:
    report_text = _read(run_dir / "forecast_report.md")
    trajectory_text = _read(run_dir / "trajectory.md")
    full_trajectory_text = _read(run_dir / "full_trajectory.md")
    thesis_review_text = _read(run_dir / "thesis_review.md")
    audit_text = _read(run_dir / "audit.md")
    combined = "\n".join([
        report_text,
        trajectory_text,
        full_trajectory_text,
        thesis_review_text,
        audit_text,
    ])
    forecast_json = _read_json(run_dir / "forecast_report.json")

    checks = {
        "required_files": {name: (run_dir / name).exists() for name in REQUIRED_FILES},
        "has_agentic_trajectory_appendix": (
            "Agentic Search Trajectory" in report_text or "完整轨迹附录" in report_text
        ),
        "has_full_trajectory_appendix": _has_embedded_full_trajectory_appendix(
            report_text=report_text,
            full_trajectory_text=full_trajectory_text,
        ),
        "has_softlink_trajectory_appendix": _has_softlink_trajectory_appendix(
            report_text
        ),
        "has_final_termination_decision": (
            "最终终止决策" in report_text
            and (
                "触发终止的主体" in report_text
                or "主 agent" in report_text
                or "主 Agent" in report_text
            )
            and (
                "停止" in report_text
                or "终止" in report_text
            )
        ),
        "full_trajectory_terms_count": _term_count(
            full_trajectory_text + "\n" + report_text,
            ["实际阅读材料", "可展示推理札记", "材料摘录", "拒绝或降权"],
        ),
        "full_trajectory_has_source_lines": _evidence_reviews_have_source_lines(
            full_trajectory_text
        ),
        "full_trajectory_has_long_workspace_paths": _has_long_workspace_path_metadata(
            full_trajectory_text
        ),
        "video_candidate_set_entry_visible": _video_candidate_set_entry_visible(
            full_trajectory_text
        ),
        "has_thesis_review": bool(thesis_review_text.strip()),
        "strong_report_terms_count": _term_count(
            report_text + "\n" + thesis_review_text,
            [
                "结论先行",
                "核心论证链",
                "Mispricing Verdict",
                "Paper Trade View",
                "Probability Floor",
                "市场错判",
                "尾部风险",
                "最强反方",
                "战略一致性",
            ],
        ),
        "has_mispricing_verdict": (
            "mispricing_verdict" in forecast_json
            or "Mispricing Verdict" in report_text
            or "mispricing verdict" in report_text
        ),
        "has_paper_trade_view": (
            "paper_trade_view" in forecast_json
            or "Paper Trade View" in report_text
            or "交易方向" in report_text
        ),
        "has_probability_floor_decomposition": (
            "Probability Floor Decomposition" in report_text
            or "probability_floor_decomposition" in thesis_review_text
            or "概率底线" in report_text
        ),
        "future_terms_count": _term_count(combined, FUTURE_TERMS),
        "trajectory_terms_count": _term_count(
            trajectory_text + "\n" + full_trajectory_text + "\n" + report_text,
            TRAJECTORY_TERMS,
        ),
        "audit_fields_present": {term: term in audit_text for term in AUDIT_TERMS},
        "evidence_paths_count": (
            len(forecast_json.get("evidence_paths", []))
            if isinstance(forecast_json, dict)
            else 0
        ),
        "has_uncalibrated_status": (
            forecast_json.get("calibration_status") == "uncalibrated"
            if isinstance(forecast_json, dict)
            else False
        ),
    }
    rubric_scores = _score_rubric(combined)
    total_score = sum(item["score"] for item in rubric_scores)
    hard_gate_failed = [
        item["id"] for item in rubric_scores
        if item["hard_gate"] and item["score"] == 0
    ]
    checks["rubric_scores"] = rubric_scores
    checks["rubric_total"] = total_score
    checks["rubric_pass_threshold"] = ">=10 and no zero on first four hard gates"
    failed = []
    if not all(checks["required_files"].values()):
        failed.append("missing_required_files")
    if not checks["has_agentic_trajectory_appendix"]:
        failed.append("missing_agentic_trajectory_appendix")
    if not checks["has_full_trajectory_appendix"]:
        failed.append("missing_full_trajectory_appendix")
    if checks["has_softlink_trajectory_appendix"]:
        failed.append("softlink_trajectory_appendix")
    if not checks["has_final_termination_decision"]:
        failed.append("missing_final_termination_decision")
    if checks["full_trajectory_terms_count"] < 4:
        failed.append("weak_full_trajectory_logging")
    if not checks["full_trajectory_has_source_lines"]:
        failed.append("non_human_readable_evidence_reviews")
    if checks["full_trajectory_has_long_workspace_paths"]:
        failed.append("long_workspace_paths_in_evidence_reviews")
    if not checks["video_candidate_set_entry_visible"]:
        failed.append("missing_video_candidate_set_entry")
    if not checks["has_thesis_review"]:
        failed.append("missing_thesis_review")
    if checks["strong_report_terms_count"] < 4:
        failed.append("weak_strong_report_thesis")
    if not checks["has_mispricing_verdict"]:
        failed.append("missing_mispricing_verdict")
    if not checks["has_paper_trade_view"]:
        failed.append("missing_paper_trade_view")
    if not checks["has_probability_floor_decomposition"]:
        failed.append("missing_probability_floor_decomposition")
    if checks["future_terms_count"] < 8:
        failed.append("weak_future_exploration_language")
    if checks["trajectory_terms_count"] < 6:
        failed.append("weak_trajectory_language")
    if not all(checks["audit_fields_present"].values()):
        failed.append("missing_prediction_exploration_audit_fields")
    if checks["evidence_paths_count"] < 3:
        failed.append("too_few_evidence_paths")
    if total_score < 10:
        failed.append("rubric_score_below_10")
    if hard_gate_failed:
        failed.append(f"rubric_hard_gate_zero:{','.join(hard_gate_failed)}")
    return {
        "workspace": str(run_dir),
        "status": "pass" if not failed else "fail",
        "failed": failed,
        "checks": checks,
    }


def render_markdown(result: dict) -> str:
    lines = [
        "# China Harness Run Audit",
        "",
        f"- workspace: `{result['workspace']}`",
        f"- status: `{result['status']}`",
    ]
    if result["failed"]:
        lines.append(f"- failed: `{', '.join(result['failed'])}`")
    lines.append("")
    lines.append("## Rubric")
    for item in result["checks"].get("rubric_scores", []):
        lines.append(
            f"- `{item['id']}` {item['label']}: `{item['score']}/2` "
            f"hits={item['hits']} hard_gate={item['hard_gate']}"
        )
    lines.append("")
    lines.append("## Checks")
    for key, value in result["checks"].items():
        lines.append(f"- `{key}`: `{value}`")
    return "\n".join(lines)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _term_count(text: str, terms: list[str]) -> int:
    return sum(text.count(term) for term in terms)


def _has_softlink_trajectory_appendix(report_text: str) -> bool:
    if "## 完整轨迹附录" not in report_text:
        return False
    appendix = report_text.split("## 完整轨迹附录", 1)[1]
    return any(term in appendix for term in TRAJECTORY_SOFTLINK_TERMS)


def _has_embedded_full_trajectory_appendix(
    report_text: str,
    full_trajectory_text: str,
) -> bool:
    if "## 完整轨迹附录" not in report_text:
        return False
    appendix = report_text.split("## 完整轨迹附录", 1)[1].strip()
    if not appendix:
        return False
    if _has_softlink_trajectory_appendix(report_text):
        return False
    full_len = len(full_trajectory_text.strip())
    if full_len:
        min_len = min(1500, max(500, int(full_len * 0.35)))
        if len(appendix) < min_len:
            return False
    return (
        "实际阅读材料" in appendix
        or "可展示推理札记" in appendix
        or "材料摘录" in appendix
        or "source_excerpt_or_summary" in appendix
        or "visible_reasoning_memo" in appendix
    )


def _evidence_reviews_have_source_lines(full_trajectory_text: str) -> bool:
    chunks = full_trajectory_text.split("## Evidence Review ")
    reviews = [chunk for chunk in chunks[1:] if chunk.strip()]
    if not reviews:
        return False
    for review in reviews:
        header_lines = review.splitlines()[:10]
        if not any(
            line.startswith("Source：") or line.startswith("Source:")
            for line in header_lines
        ):
            return False
    return True


def _has_long_workspace_path_metadata(full_trajectory_text: str) -> bool:
    metadata_prefixes = (
        "- review_path:",
        "- evidence_path:",
        "- candidate_set_path:",
    )
    for line in full_trajectory_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(metadata_prefixes):
            continue
        if (
            "`workspace/" in stripped
            or "`./workspace/" in stripped
            or "`/mnt/" in stripped
        ):
            return True
    return False


def _video_candidate_set_entry_visible(full_trajectory_text: str) -> bool:
    mentions_video_source = (
        "B站" in full_trajectory_text
        or "bilibili" in full_trajectory_text.lower()
        or "YouTube" in full_trajectory_text
    )
    if not mentions_video_source:
        return True
    return "候选池入口" in full_trajectory_text and "source_visits/" in full_trajectory_text


def _score_rubric(text: str) -> list[dict]:
    scores = []
    for item in RUBRIC:
        hits = sum(1 for term in item["terms"] if term in text)
        if hits >= 3:
            score = 2
        elif hits >= 1:
            score = 1
        else:
            score = 0
        scores.append({
            "id": item["id"],
            "label": item["label"],
            "score": score,
            "hits": hits,
            "hard_gate": item["hard_gate"],
        })
    return scores


if __name__ == "__main__":
    main()
