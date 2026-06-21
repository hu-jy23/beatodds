#!/usr/bin/env python3
"""Render an agent research trajectory into a human-readable HTML slide deck."""

from __future__ import annotations

import argparse
import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EvidenceReview:
    index: int
    source: str
    agent_output: str
    materials: str
    summary: str
    reasoning: str
    selection: str
    assessment: str
    rejected: str
    next_step: str


def main() -> None:
    parser = argparse.ArgumentParser(
        description="把 China harness 的 full_trajectory.md 渲染成研究过程 HTML PPT。"
    )
    parser.add_argument("--workspace", required=True, help="Agent run workspace path")
    parser.add_argument("--output", default="", help="Output HTML path")
    parser.add_argument("--title", default="研究过程复盘")
    parser.add_argument("--max-reviews", type=int, default=12)
    args = parser.parse_args()

    run_dir = Path(args.workspace)
    output = Path(args.output) if args.output else run_dir / "research_process_ppt.html"
    output.parent.mkdir(parents=True, exist_ok=True)

    reviews = _parse_reviews(run_dir / "full_trajectory.md")[: args.max_reviews]
    report_data = _read_json(run_dir / "forecast_report.json")
    audit_text = _read_text(run_dir / "audit.md")
    html_text = _render_html(
        title=args.title,
        run_dir=run_dir,
        reviews=reviews,
        report_data=report_data,
        audit_text=audit_text,
    )
    output.write_text(html_text, encoding="utf-8")
    print(f"html={output}")
    print(f"reviews={len(reviews)}")
    print("status=ok")


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_reviews(path: Path) -> list[EvidenceReview]:
    text = _read_text(path)
    if not text.strip():
        return []
    sections = re.split(r"(?m)^## Evidence Review\s+(\d+)\s*$", text)
    reviews: list[EvidenceReview] = []
    for i in range(1, len(sections), 2):
        index = int(sections[i])
        body = sections[i + 1]
        reviews.append(
            EvidenceReview(
                index=index,
                source=_first_line(body, r"(?m)^Source：(.+)$") or "未命名 source",
                agent_output=_section(body, "Agent 输出"),
                materials=_section(body, "实际阅读材料"),
                summary=_section(body, "材料摘录或压缩摘要"),
                reasoning=_section(body, "可展示推理札记"),
                selection=_section(body, "Source 选择说明"),
                assessment=_section(body, "评估"),
                rejected=_section(body, "拒绝或降权材料"),
                next_step=_section(body, "下一步搜索决策"),
            )
        )
    return reviews


def _first_line(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""


def _section(text: str, title: str) -> str:
    pattern = rf"(?ms)^### {re.escape(title)}\s*\n(.*?)(?=^### |\Z)"
    match = re.search(pattern, text)
    if not match:
        return ""
    return match.group(1).strip()


def _render_html(
    *,
    title: str,
    run_dir: Path,
    reviews: list[EvidenceReview],
    report_data: dict[str, Any],
    audit_text: str,
) -> str:
    p_m = _num(report_data.get("p_m"))
    p_f = _num(report_data.get("p_f"))
    delta = _num(report_data.get("p_m_delta"))
    verdict = str(report_data.get("mispricing_verdict") or "unknown")
    trade = report_data.get("paper_trade_view") or {}
    trade_direction = trade.get("direction", "unknown") if isinstance(trade, dict) else "unknown"
    audit_lines = [
        line.strip("- ").strip()
        for line in audit_text.splitlines()
        if line.startswith("- ")
        and any(key in line for key in ("知乎", "微博", "雪球", "T1", "T2"))
    ][:8]

    slides = [
        _slide(
            "研究过程 PPT",
            f"""
            <p class="eyebrow">Trajectory-first deck</p>
            <h1>{_e(title)}</h1>
            <p class="lead">{_e(run_dir.as_posix())}</p>
            <div class="metric-grid">
              {_metric("p_m", p_m)}
              {_metric("p_f", p_f)}
              {_metric("delta", delta)}
              {_metric("trade", str(trade_direction))}
            </div>
            """,
            "cover",
        ),
        _slide(
            "本 deck 的对象",
            """
            <h2>展示 agent 的研究过程</h2>
            <p class="lead">这里展示的是每轮 source 候选、筛选、正文深读、降权和下一步决策。</p>
            <div class="callout">它不是 forecast report 正文的 slide 化版本。</div>
            """,
        ),
        _slide(
            "最终判断作为导航",
            f"""
            <h2>结论只作为导航</h2>
            <div class="verdict">{_e(verdict)}</div>
            <p>paper trade: <strong>{_e(str(trade_direction))}</strong></p>
            <p>后面每页说明这个结论如何从候选池和正文筛选中得到。</p>
            """,
        ),
    ]

    if audit_lines:
        slides.append(
            _slide(
                "T1/T2 验收摘要",
                "<h2>T1/T2 验收摘要</h2>"
                + "<ul>"
                + "".join(f"<li>{_e(line)}</li>" for line in audit_lines)
                + "</ul>",
            )
        )

    slides.extend(_review_slide(review) for review in reviews)
    slides.append(
        _slide(
            "过程审计结论",
            """
            <h2>过程审计结论</h2>
            <ul>
              <li>每个 selected 平台都应有正文处理记录。</li>
              <li>高互动材料必须经过机制链检查。</li>
              <li>低质量正文、旧帖、概念股噪声要明确降权。</li>
              <li>下一步搜索必须由已读 evidence 推出。</li>
            </ul>
            """,
        )
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_e(title)}</title>
  <style>{_css()}</style>
</head>
<body>
  <main class="deck">
    {''.join(slides)}
  </main>
  <script>
    const slides = [...document.querySelectorAll('.slide')];
    let index = 0;
    function show(i) {{
      index = Math.max(0, Math.min(slides.length - 1, i));
      slides.forEach((slide, n) => slide.classList.toggle('active', n === index));
      document.body.dataset.slide = String(index + 1);
    }}
    document.addEventListener('keydown', event => {{
      if (['ArrowRight', 'PageDown', ' '].includes(event.key)) show(index + 1);
      if (['ArrowLeft', 'PageUp'].includes(event.key)) show(index - 1);
    }});
    show(0);
  </script>
</body>
</html>
"""


def _review_slide(review: EvidenceReview) -> str:
    use_or_downweight = "\n\n".join(
        part for part in [review.assessment, review.rejected] if part.strip()
    )
    return _slide(
        f"Evidence Review {review.index}",
        f"""
        <p class="eyebrow">Evidence Review {review.index}</p>
        <h2>{_e(review.source)}</h2>
        <div class="two-col">
          <section>
            <h3>为什么细读</h3>
            {_paragraph(review.selection or review.agent_output)}
            <h3>实际读到</h3>
            {_paragraph(review.summary)}
            <h3>候选池入口</h3>
            {_paragraph(review.materials)}
          </section>
          <section>
            <h3>agent 判断</h3>
            {_paragraph(review.reasoning)}
            <h3>使用 / 降权</h3>
            {_paragraph(use_or_downweight)}
            <h3>下一步</h3>
            {_paragraph(review.next_step)}
          </section>
        </div>
        """,
    )


def _slide(title: str, body: str, extra_class: str = "") -> str:
    return f'<section class="slide {extra_class}" aria-label="{_e(title)}">{body}</section>'


def _metric(label: str, value: Any) -> str:
    if isinstance(value, float):
        text = f"{value:.3f}"
    else:
        text = str(value)
    return f'<div class="metric"><span>{_e(label)}</span><strong>{_e(text)}</strong></div>'


def _num(value: Any) -> float | str:
    try:
        return float(value)
    except (TypeError, ValueError):
        return "n/a"


def _paragraph(text: str) -> str:
    clean = text.strip()
    if not clean:
        return "<p class=\"muted\">未记录</p>"
    parts = [part.strip() for part in re.split(r"\n{2,}", clean) if part.strip()]
    return "".join(f"<p>{_e(part)}</p>" for part in parts)


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _css() -> str:
    return """
    :root {
      --ink: #172018;
      --muted: #5f6b5e;
      --paper: #f7f1e4;
      --card: #fffaf0;
      --green: #194d33;
      --gold: #c48a31;
      --line: rgba(23, 32, 24, 0.16);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background:
        radial-gradient(circle at 14% 10%, rgba(196, 138, 49, 0.18), transparent 30%),
        linear-gradient(135deg, #f9f2df, #edf3e5 62%, #dfe9d8);
      color: var(--ink);
      font-family: "Noto Serif CJK SC", "Source Han Serif SC", "Microsoft YaHei", serif;
      min-height: 100vh;
      overflow: hidden;
    }
    .deck { width: 100vw; height: 100vh; position: relative; }
    .slide {
      display: none;
      width: min(1180px, calc(100vw - 72px));
      height: min(720px, calc(100vh - 72px));
      overflow: auto;
      position: absolute;
      inset: 50% auto auto 50%;
      transform: translate(-50%, -50%);
      padding: 52px;
      border: 1px solid var(--line);
      border-radius: 30px;
      background: rgba(255, 250, 240, 0.92);
      box-shadow: 0 30px 90px rgba(34, 48, 35, 0.18);
    }
    .slide.active { display: block; animation: lift 260ms ease-out; }
    @keyframes lift {
      from { opacity: 0; transform: translate(-50%, -47%); }
      to { opacity: 1; transform: translate(-50%, -50%); }
    }
    .cover {
      background:
        linear-gradient(145deg, rgba(25, 77, 51, 0.96), rgba(34, 87, 63, 0.92)),
        var(--card);
      color: #fff8e8;
    }
    h1, h2, h3 { margin: 0; line-height: 1.12; }
    h1 { font-size: clamp(42px, 6vw, 76px); letter-spacing: -0.04em; max-width: 920px; }
    h2 { font-size: clamp(28px, 4vw, 48px); letter-spacing: -0.03em; margin-bottom: 22px; }
    h3 { font-size: 18px; margin: 22px 0 8px; color: var(--green); }
    p, li { font-size: 17px; line-height: 1.75; }
    ul { padding-left: 24px; }
    .lead { font-size: 22px; color: var(--muted); max-width: 860px; }
    .cover .lead, .cover .eyebrow { color: rgba(255, 248, 232, 0.75); }
    .eyebrow {
      margin: 0 0 18px;
      text-transform: uppercase;
      letter-spacing: 0.15em;
      font-size: 13px;
      color: var(--gold);
      font-weight: 700;
    }
    .metric-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      margin-top: 44px;
    }
    .metric {
      border: 1px solid rgba(255, 248, 232, 0.24);
      border-radius: 20px;
      padding: 18px;
      background: rgba(255, 255, 255, 0.1);
    }
    .metric span { display: block; color: rgba(255, 248, 232, 0.7); font-size: 13px; }
    .metric strong { display: block; font-size: 29px; margin-top: 8px; }
    .verdict {
      display: inline-block;
      padding: 14px 18px;
      border-radius: 999px;
      background: #173f2c;
      color: #fff8e8;
      font-size: 28px;
      font-weight: 800;
    }
    .callout {
      margin-top: 30px;
      padding: 26px;
      border-radius: 24px;
      background: #193d2c;
      color: #fff8e8;
      font-size: 24px;
      font-weight: 700;
    }
    .two-col {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 28px;
    }
    .two-col section {
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 20px 22px;
      background: rgba(255, 255, 255, 0.42);
    }
    .muted { color: var(--muted); }
    body::after {
      content: attr(data-slide);
      position: fixed;
      right: 28px;
      bottom: 20px;
      color: rgba(23, 32, 24, 0.45);
      font-weight: 800;
    }
    @media (max-width: 820px) {
      body { overflow: auto; }
      .slide {
        position: static;
        transform: none;
        width: 100%;
        height: auto;
        min-height: 100vh;
        border-radius: 0;
        padding: 30px 22px;
      }
      .slide.active { animation: none; }
      .two-col, .metric-grid { grid-template-columns: 1fr; }
    }
    """


if __name__ == "__main__":
    main()
