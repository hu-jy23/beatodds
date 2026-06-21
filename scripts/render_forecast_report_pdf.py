#!/usr/bin/env python3
"""Render a China harness forecast report into a LaTeX PDF."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties, fontManager


def main() -> None:
    parser = argparse.ArgumentParser(
        description="把 forecast_report.md/json 渲染为 forecast_report.tex/pdf。"
    )
    parser.add_argument("--workspace", required=True, help="Agent run workspace path")
    parser.add_argument("--output", default="", help="输出 PDF 路径")
    parser.add_argument("--title", default="预测报告")
    args = parser.parse_args()

    run_dir = Path(args.workspace)
    md_path = run_dir / "forecast_report.md"
    json_path = run_dir / "forecast_report.json"
    context_path = run_dir / "run_context.json"
    if not md_path.exists():
        raise FileNotFoundError(f"Missing {md_path}")

    original_report_text = md_path.read_text(encoding="utf-8")
    report_text = _embed_full_trajectory(
        report_text=original_report_text,
        run_dir=run_dir,
    )
    if report_text != original_report_text:
        md_path.write_text(report_text, encoding="utf-8")
    report_data = _read_json(json_path)
    context_data = _read_json(context_path)
    output_path = Path(args.output) if args.output else run_dir / "forecast_report.pdf"
    tex_path = run_dir / "forecast_report.tex"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    chart_path = _write_probability_chart(run_dir=run_dir, report_data=report_data)
    company_chart_path = _write_company_probability_chart(
        run_dir=run_dir,
        report_data=report_data,
    )
    chart_latex_path = _relative_chart_path(run_dir, chart_path)
    company_chart_latex_path = _relative_chart_path(run_dir, company_chart_path)
    tex_path.write_text(
        _render_latex(
            title=args.title,
            report_text=report_text,
            report_data=report_data,
            context_data=context_data,
            chart_path=chart_latex_path,
            company_chart_path=company_chart_latex_path,
        ),
        encoding="utf-8",
    )
    _compile_latex(run_dir=run_dir, tex_path=tex_path, output_path=output_path)

    print(f"tex={tex_path}")
    print(f"pdf={output_path}")
    if chart_path:
        print(f"chart={chart_path}")
    if company_chart_path:
        print(f"company_chart={company_chart_path}")
    print("status=ok")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_probability_chart(run_dir: Path, report_data: dict[str, Any]) -> Path | None:
    values = _numeric_values(report_data)
    if not values:
        return None
    chart_dir = run_dir / "artifacts" / "report_charts"
    chart_dir.mkdir(parents=True, exist_ok=True)
    chart_path = chart_dir / "probability_summary.png"

    font = _font_properties()
    labels = list(values.keys())
    numbers = [values[label] for label in labels]
    colors = ["#0f766e", "#1d4ed8", "#b45309", "#475569", "#7c3aed"][: len(labels)]

    fig, ax = plt.subplots(figsize=(7.6, 3.9), dpi=190)
    fig.patch.set_facecolor("#fbfaf7")
    ax.set_facecolor("#fbfaf7")
    bars = ax.barh(labels, numbers, color=colors, height=0.5)
    ax.set_xlim(0, max(1.0, max(numbers) * 1.18))
    ax.set_title("概率与置信度", fontproperties=font, fontsize=13, pad=12)
    ax.grid(axis="x", alpha=0.18)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_alpha(0.25)
    ax.set_xlabel("数值", fontproperties=font, color="#475569")
    ax.tick_params(axis="x", colors="#475569")
    ax.tick_params(axis="y", length=0, colors="#1f2937")
    for tick in ax.get_yticklabels() + ax.get_xticklabels():
        tick.set_fontproperties(font)
    for bar, value in zip(bars, numbers, strict=False):
        ax.text(
            value + 0.012,
            bar.get_y() + bar.get_height() / 2,
            f"{value:.3f}",
            va="center",
            fontsize=9,
            fontproperties=font,
            color="#111827",
        )
    fig.tight_layout()
    fig.savefig(chart_path, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return chart_path


def _write_company_probability_chart(run_dir: Path, report_data: dict[str, Any]) -> Path | None:
    companies = _multi_outcome_rows(report_data)
    if not companies:
        return None
    rows = []
    for item in companies:
        if not isinstance(item, dict):
            continue
        company = item.get("company") or item.get("outcome") or item.get("label")
        probability = _as_float(item.get("p_f"))
        if company and probability is not None:
            rows.append((str(company), probability, _as_float(item.get("p_m"))))
    if not rows:
        return None

    rows = sorted(rows, key=lambda row: row[1], reverse=True)[:12]
    chart_dir = run_dir / "artifacts" / "report_charts"
    chart_dir.mkdir(parents=True, exist_ok=True)
    chart_path = chart_dir / "company_probability_summary.png"

    font = _font_properties()
    labels = [row[0] for row in rows]
    p_f_values = [row[1] for row in rows]
    p_m_values = [row[2] for row in rows]

    fig, ax = plt.subplots(figsize=(8.4, 5.2), dpi=190)
    fig.patch.set_facecolor("#fbfaf7")
    ax.set_facecolor("#fbfaf7")
    y = list(range(len(labels)))
    ax.barh([value + 0.18 for value in y], p_f_values, height=0.32, color="#0f766e", label="p_f")
    if any(value is not None for value in p_m_values):
        ax.barh(
            [value - 0.18 for value in y],
            [value or 0.0 for value in p_m_values],
            height=0.32,
            color="#b45309",
            label="p_m",
        )
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlim(0, max(1.0, max(p_f_values + [value or 0.0 for value in p_m_values]) * 1.18))
    ax.set_title("公司胜出概率对比", fontproperties=font, fontsize=13, pad=12)
    ax.grid(axis="x", alpha=0.18)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_alpha(0.25)
    ax.set_xlabel("概率", fontproperties=font, color="#475569")
    ax.legend(prop=font, frameon=False)
    ax.tick_params(axis="x", colors="#475569")
    ax.tick_params(axis="y", length=0, colors="#1f2937")
    for tick in ax.get_yticklabels() + ax.get_xticklabels():
        tick.set_fontproperties(font)
    for index, value in enumerate(p_f_values):
        ax.text(
            value + 0.01,
            index + 0.18,
            f"{value:.3f}",
            va="center",
            fontsize=8,
            fontproperties=font,
        )
    for index, value in enumerate(p_m_values):
        if value is not None:
            ax.text(
                value + 0.01,
                index - 0.18,
                f"{value:.3f}",
                va="center",
                fontsize=8,
                fontproperties=font,
            )
    fig.tight_layout()
    fig.savefig(chart_path, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return chart_path


def _multi_outcome_rows(report_data: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = report_data.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    candidates = [
        metadata.get("company_probabilities"),
        metadata.get("outcome_probabilities"),
        report_data.get("outcomes"),
    ]
    for value in candidates:
        if isinstance(value, list) and value:
            return [item for item in value if isinstance(item, dict)]
    return []


def _embed_full_trajectory(report_text: str, run_dir: Path) -> str:
    """Ensure the rendered PDF carries the full audit trail, not just a file pointer."""
    trajectory_path = run_dir / "full_trajectory.md"
    if not trajectory_path.exists():
        return report_text

    trajectory_text = trajectory_path.read_text(encoding="utf-8").strip()
    if not trajectory_text:
        return report_text

    marker = "## 完整轨迹附录"
    appendix = "\n\n".join([
        marker,
        "以下为本次 run 的完整可展示轨迹，已直接嵌入报告正文和 PDF 附录。",
        trajectory_text,
    ])
    if marker not in report_text:
        return "\n\n".join([
            report_text.rstrip(),
            appendix,
        ])

    prefix = report_text.split(marker, 1)[0].rstrip()
    return "\n\n".join([prefix, appendix])


def _render_latex(
    title: str,
    report_text: str,
    report_data: dict[str, Any],
    context_data: dict[str, Any],
    chart_path: Path | None,
    company_chart_path: Path | None,
) -> str:
    subtitle = context_data.get("market_question") or report_data.get("condition_id") or ""
    event_title = context_data.get("event_title") or ""
    chart_latex = ""
    if chart_path and chart_path.exists():
        chart_latex = "\n".join([
            r"\begin{figure}[h]",
            r"\centering",
            rf"\includegraphics[width=0.86\linewidth]{{{_latex_path(chart_path)}}}",
            r"\end{figure}",
            "",
        ])
    company_chart_latex = ""
    if company_chart_path and company_chart_path.exists():
        company_chart_latex = "\n".join([
            r"\begin{figure}[h]",
            r"\centering",
            rf"\includegraphics[width=0.92\linewidth]{{{_latex_path(company_chart_path)}}}",
            r"\end{figure}",
            "",
        ])

    return "\n".join([
        r"\documentclass[11pt,a4paper]{ctexart}",
        r"\usepackage[margin=22mm]{geometry}",
        r"\usepackage{xcolor}",
        r"\usepackage{graphicx}",
        r"\usepackage{booktabs}",
        r"\usepackage{array}",
        r"\usepackage{tabularx}",
        r"\usepackage{enumitem}",
        r"\usepackage{fancyhdr}",
        r"\usepackage{titlesec}",
        r"\usepackage{xurl}",
        r"\usepackage[colorlinks=true,linkcolor=ReportBlue,urlcolor=ReportBlue]{hyperref}",
        r"\definecolor{ReportInk}{HTML}{172033}",
        r"\definecolor{ReportMuted}{HTML}{64748B}",
        r"\definecolor{ReportLine}{HTML}{CBD5E1}",
        r"\definecolor{ReportBg}{HTML}{FBFAF7}",
        r"\definecolor{ReportBlue}{HTML}{1D4ED8}",
        r"\definecolor{ReportGreen}{HTML}{0F766E}",
        r"\definecolor{ReportAmber}{HTML}{B45309}",
        r"\pagecolor{ReportBg}",
        r"\color{ReportInk}",
        r"\setlength{\parindent}{0pt}",
        r"\setlength{\parskip}{6pt}",
        r"\setlist[itemize]{leftmargin=16pt,itemsep=2pt,topsep=2pt}",
        r"\titleformat{\section}{\Large\bfseries\color{ReportInk}}{}{0pt}{}[\vspace{-4pt}\titlerule]",
        r"\titleformat{\subsection}{\large\bfseries\color{ReportInk}}{}{0pt}{}",
        r"\titleformat{\subsubsection}{\normalsize\bfseries\color{ReportInk}}{}{0pt}{}",
        r"\pagestyle{fancy}",
        r"\fancyhf{}",
        rf"\lhead{{\small {_escape_latex(title)}}}",
        r"\rhead{\small 中国相关预测}",
        r"\cfoot{\thepage}",
        r"\renewcommand{\headrulewidth}{0.2pt}",
        r"\begin{document}",
        r"\begin{center}",
        r"\vspace*{4mm}",
        rf"{{\Huge\bfseries {_escape_latex(title)}\par}}",
        r"\vspace{4mm}",
        rf"{{\Large {_escape_latex(subtitle)}\par}}",
        r"\vspace{2mm}",
        rf"{{\color{{ReportMuted}} {_escape_latex(event_title)}\par}}",
        r"\end{center}",
        r"\vspace{6mm}",
        _summary_table(report_data),
        chart_latex,
        company_chart_latex,
        r"\newpage",
        _markdown_to_latex(report_text),
        r"\end{document}",
        "",
    ])


def _summary_table(report_data: dict[str, Any]) -> str:
    rows = _summary_rows(report_data)
    body = "\n".join(
        rf"{_escape_latex(label)} & {_escape_latex(value)} \\"
        for label, value in rows
    )
    return "\n".join([
        r"\begin{center}",
        r"\renewcommand{\arraystretch}{1.25}",
        r"\begin{tabular}{@{}ll@{}}",
        r"\toprule",
        r"\textbf{字段} & \textbf{数值} \\",
        r"\midrule",
        body,
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{center}",
        "",
    ])


def _summary_rows(report_data: dict[str, Any]) -> list[tuple[str, str]]:
    keys = [
        ("condition_id", "Condition ID"),
        ("p_evidence", "证据概率 p_evidence"),
        ("p_f", "最终预测 p_f"),
        ("p_m", "市场概率 p_m"),
        ("p_m_delta", "市场差值 p_m_delta"),
        ("confidence", "置信度"),
        ("calibration_status", "校准状态"),
        ("model", "模型"),
    ]
    rows = []
    for key, label in keys:
        if key in report_data and report_data[key] is not None:
            value = report_data[key]
            if isinstance(value, float):
                value = f"{value:.4f}"
            rows.append((label, str(value)))
    return rows or [("状态", "未找到 forecast_report.json 字段。")]


def _numeric_values(report_data: dict[str, Any]) -> dict[str, float]:
    values = {}
    labels = {
        "p_evidence": "证据概率",
        "p_f": "最终预测",
        "p_m": "市场概率",
        "confidence": "置信度",
    }
    for key in ["p_evidence", "p_f", "p_m", "confidence"]:
        value = _as_float(report_data.get(key))
        if value is not None:
            values[labels[key]] = value
    delta = abs(_as_float(report_data.get("p_m_delta")) or 0.0)
    if delta:
        values["市场差值绝对值"] = delta
    return values


def _markdown_to_latex(text: str) -> str:
    lines = text.splitlines()
    output: list[str] = []
    in_itemize = False
    in_code = False
    code_lines: list[str] = []
    index = 0

    def close_itemize() -> None:
        nonlocal in_itemize
        if in_itemize:
            output.append(r"\end{itemize}")
            in_itemize = False

    def flush_code() -> None:
        nonlocal code_lines
        output.append(r"\begin{verbatim}")
        output.extend(code_lines)
        output.append(r"\end{verbatim}")
        code_lines = []

    while index < len(lines):
        raw = lines[index]
        line = raw.rstrip()
        if line.strip().startswith("```"):
            if in_code:
                flush_code()
                in_code = False
            else:
                close_itemize()
                in_code = True
                code_lines = []
            index += 1
            continue
        if in_code:
            code_lines.append(line)
            index += 1
            continue

        stripped = line.strip()
        if not stripped:
            close_itemize()
            output.append("")
            index += 1
            continue

        if _is_markdown_table_start(lines, index):
            close_itemize()
            table_lines = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index].strip())
                index += 1
            output.append(_markdown_table_to_latex(table_lines))
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            close_itemize()
            level = len(heading.group(1))
            text_value = _inline_markdown_to_latex(heading.group(2))
            command = "section" if level == 1 else "subsection" if level == 2 else "subsubsection"
            output.append(rf"\{command}*{{{text_value}}}")
            index += 1
            continue

        bullet = re.match(r"^[-*]\s+(.*)$", stripped)
        if bullet:
            if not in_itemize:
                output.append(r"\begin{itemize}")
                in_itemize = True
            output.append(rf"\item {_inline_markdown_to_latex(bullet.group(1))}")
            index += 1
            continue

        numbered = re.match(r"^\d+\.\s+(.*)$", stripped)
        if numbered:
            if not in_itemize:
                output.append(r"\begin{itemize}")
                in_itemize = True
            output.append(rf"\item {_inline_markdown_to_latex(numbered.group(1))}")
            index += 1
            continue

        close_itemize()
        output.append(_inline_markdown_to_latex(stripped))
        index += 1

    if in_code:
        flush_code()
    close_itemize()
    return "\n".join(output)


def _is_markdown_table_start(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    first = lines[index].strip()
    second = lines[index + 1].strip()
    if not (first.startswith("|") and second.startswith("|")):
        return False
    cells = _markdown_table_cells(second)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def _markdown_table_cells(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [cell.strip() for cell in text.split("|")]


def _markdown_table_to_latex(lines: list[str]) -> str:
    if len(lines) < 2:
        return ""
    header = _markdown_table_cells(lines[0])
    rows = [_markdown_table_cells(line) for line in lines[2:]]
    if not header:
        return ""
    column_count = len(header)
    column_spec = " ".join(
        [r">{\raggedright\arraybackslash}X" for _ in range(column_count)]
    )
    size = r"\scriptsize" if column_count >= 6 else r"\small"
    output = [
        r"\begin{center}",
        r"\begingroup",
        size,
        r"\renewcommand{\arraystretch}{1.2}",
        rf"\begin{{tabularx}}{{\linewidth}}{{@{{}}{column_spec}@{{}}}}",
        r"\toprule",
        " & ".join(_inline_markdown_to_latex(cell) for cell in header) + r" \\",
        r"\midrule",
    ]
    for row in rows:
        normalized = (row + [""] * column_count)[:column_count]
        output.append(
            " & ".join(_inline_markdown_to_latex(cell) for cell in normalized) + r" \\"
        )
    output.extend([
        r"\bottomrule",
        r"\end{tabularx}",
        r"\endgroup",
        r"\end{center}",
    ])
    return "\n".join(output)


def _inline_markdown_to_latex(text: str) -> str:
    placeholders: dict[str, str] = {}

    def stash(pattern: str, renderer) -> str:
        nonlocal text

        def repl(match: re.Match[str]) -> str:
            key = f"@@PLACEHOLDER{len(placeholders)}@@"
            placeholders[key] = renderer(match)
            return key

        text = re.sub(pattern, repl, text)
        return text

    stash(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: rf"\href{{{_escape_latex(m.group(2))}}}"
        rf"{{{_escape_latex(m.group(1))}}}",
    )
    stash(r"`([^`]+)`", lambda m: rf"\texttt{{{_escape_latex(m.group(1))}}}")
    stash(r"\*\*([^*]+)\*\*", lambda m: rf"\textbf{{{_escape_latex(m.group(1))}}}")

    escaped = _escape_latex(text)
    for key, value in placeholders.items():
        escaped = escaped.replace(_escape_latex(key), value)
    return escaped


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


def _latex_path(path: Path) -> str:
    return path.as_posix().replace("\\", "/")


def _relative_chart_path(run_dir: Path, chart_path: Path | None) -> Path | None:
    if chart_path is None:
        return None
    try:
        return chart_path.relative_to(run_dir)
    except ValueError:
        return chart_path


def _compile_latex(run_dir: Path, tex_path: Path, output_path: Path) -> None:
    run_dir = run_dir.resolve()
    tex_path = tex_path.resolve()
    output_path = output_path.resolve()
    build_dir = (run_dir / "artifacts" / "latex").resolve()
    build_dir.mkdir(parents=True, exist_ok=True)
    command = [
        "xelatex",
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-output-directory",
        str(build_dir),
        tex_path.name,
    ]
    try:
        result = subprocess.run(
            command,
            cwd=run_dir,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("xelatex is required to render forecast_report.pdf") from exc

    log_path = build_dir / "xelatex_stdout.log"
    log_path.write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"LaTeX render failed; see {log_path}")

    built_pdf = build_dir / tex_path.with_suffix(".pdf").name
    if not built_pdf.exists():
        raise RuntimeError(f"LaTeX render did not produce {built_pdf}")
    if built_pdf.resolve() != output_path.resolve():
        shutil.copyfile(built_pdf, output_path)


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _font_properties() -> FontProperties:
    preferred_paths = [
        "/mnt/c/Windows/Fonts/msyh.ttc",
        "/mnt/c/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]
    for path in preferred_paths:
        if Path(path).exists():
            return FontProperties(fname=path)
    preferred = [
        "Noto Sans CJK SC",
        "Noto Sans CJK JP",
        "Microsoft YaHei",
        "SimHei",
        "WenQuanYi Zen Hei",
        "Arial Unicode MS",
        "DejaVu Sans",
    ]
    available = {font.name for font in fontManager.ttflist}
    for name in preferred:
        if name in available:
            return FontProperties(family=name)
    return FontProperties()


def _wrap_text(text: str, width: int = 88) -> str:
    return "\n".join(textwrap.wrap(text, width=width, break_long_words=False)) or text


if __name__ == "__main__":
    main()
