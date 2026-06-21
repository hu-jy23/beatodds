"""Platform-specific video source access for Chinese forecast research."""

from __future__ import annotations

import html
import json
import re
import subprocess
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from beatodds.evidence.providers.base import SearchResult

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
}


def search_bilibili_videos(
    query: str,
    max_results: int = 6,
    source_category: str = "expert_social",
    reliability_prior: float = 0.5,
) -> list[SearchResult]:
    """Search Bilibili through its in-platform web API after session warmup."""
    query = query.strip()
    if not query:
        return []
    output = []
    seen_bvids: set[str] = set()
    with httpx.Client(headers=DEFAULT_HEADERS, follow_redirects=True, timeout=20.0) as client:
        client.get("https://search.bilibili.com/all", params={"keyword": query})
        for order in ["totalrank", "click", "stow", "pubdate"]:
            response = client.get(
                "https://api.bilibili.com/x/web-interface/search/type",
                params={
                    "search_type": "video",
                    "keyword": query,
                    "page": 1,
                    "order": order,
                },
                headers={"Referer": "https://search.bilibili.com/"},
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != 0:
                raise RuntimeError(f"Bilibili search failed: {payload.get('message')}")
            for item in (payload.get("data") or {}).get("result") or []:
                bvid = item.get("bvid") or ""
                if not bvid or bvid in seen_bvids:
                    continue
                seen_bvids.add(bvid)
                title = _clean_html(item.get("title") or "")
                play_count = _to_int(item.get("play"))
                review_count = _to_int(item.get("review"))
                duration = item.get("duration") or ""
                summary = "；".join([
                    part for part in [
                        _clean_html(item.get("description") or ""),
                        f"播放 {play_count}" if play_count is not None else "",
                        f"评论 {review_count}" if review_count is not None else "",
                        f"时长 {duration}" if duration else "",
                    ]
                    if part
                ])
                candidate_score = _candidate_relevance_score(
                    query=query,
                    title=title,
                    summary=summary,
                    source=item.get("author") or "",
                    view_count=play_count,
                    comment_count=review_count,
                )
                output.append(
                    SearchResult(
                        query=query,
                        title=title,
                        summary=summary,
                        url=f"https://www.bilibili.com/video/{bvid}",
                        source=item.get("author") or "Bilibili",
                        relevance_score=candidate_score,
                        provider="bilibili_internal",
                        source_type=source_category,
                        reliability_prior=reliability_prior,
                        raw_metadata={
                            "platform": "bilibili",
                            "bvid": bvid,
                            "author": item.get("author"),
                            "play_count": play_count,
                            "comment_count": review_count,
                            "duration": duration,
                            "pubdate": item.get("pubdate"),
                            "tag": item.get("tag"),
                            "search_order": order,
                            "content_access": {
                                "search_result": True,
                                "metadata": False,
                                "comments": False,
                                "transcript": False,
                                "video_body": False,
                            },
                            "candidate_assessment": _candidate_assessment(
                                title=title,
                                view_count=play_count,
                                comment_count=review_count,
                            ),
                            "candidate_score": candidate_score,
                        },
                    )
                )
    output.sort(key=lambda item: item.relevance_score, reverse=True)
    return output[:max_results]


def inspect_bilibili_video(url: str, max_comments: int = 10) -> dict[str, Any]:
    bvid = _extract_bilibili_bvid(url)
    if not bvid:
        raise RuntimeError(f"Cannot extract BVID from URL: {url}")
    headers = {
        **DEFAULT_HEADERS,
        "Referer": f"https://www.bilibili.com/video/{bvid}/",
    }
    with httpx.Client(headers=headers, follow_redirects=True, timeout=20.0) as client:
        view_response = client.get(
            "https://api.bilibili.com/x/web-interface/view",
            params={"bvid": bvid},
        )
        view_response.raise_for_status()
        view_payload = view_response.json()
        if view_payload.get("code") != 0:
            raise RuntimeError(f"Bilibili view failed: {view_payload.get('message')}")
        data = view_payload.get("data") or {}
        aid = data.get("aid")
        cid = data.get("cid")
        comments, comments_status = _fetch_bilibili_comments(client, aid, max_comments)
        subtitles, subtitles_status = _fetch_bilibili_subtitles(client, bvid, cid)
    stats = data.get("stat") or {}
    owner = data.get("owner") or {}
    return {
        "platform": "bilibili",
        "url": f"https://www.bilibili.com/video/{bvid}",
        "bvid": bvid,
        "aid": aid,
        "cid": cid,
        "title": data.get("title") or "",
        "description": data.get("desc") or "",
        "author": owner.get("name") or "",
        "author_id": owner.get("mid"),
        "published_at": _from_unix(data.get("pubdate")),
        "duration_seconds": data.get("duration"),
        "stats": {
            "view_count": stats.get("view"),
            "like_count": stats.get("like"),
            "coin_count": stats.get("coin"),
            "favorite_count": stats.get("favorite"),
            "share_count": stats.get("share"),
            "comment_count": stats.get("reply"),
            "danmaku_count": stats.get("danmaku"),
        },
        "comments": comments,
        "comments_status": comments_status,
        "subtitles": subtitles,
        "subtitles_status": subtitles_status,
        "content_access": {
            "metadata": True,
            "comments": bool(comments),
            "transcript": bool(subtitles),
            "video_body": False,
            "requires_asr": not bool(subtitles),
        },
        "assessment": assess_video_resource(
            platform="bilibili",
            title=data.get("title") or "",
            author=owner.get("name") or "",
            view_count=stats.get("view"),
            comment_count=stats.get("reply"),
            has_transcript=bool(subtitles),
            comments_available=bool(comments),
        ),
    }


def search_youtube_videos(
    query: str,
    max_results: int = 6,
    source_category: str = "expert_social",
    reliability_prior: float = 0.45,
) -> list[SearchResult]:
    query = query.strip()
    if not query:
        return []
    proc = _run_ytdlp(
        [
            "--js-runtimes",
            "deno",
            "--flat-playlist",
            "--dump-json",
            f"ytsearch{max_results}:{query}",
        ],
        timeout=75,
    )
    results = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        item = json.loads(line)
        url = item.get("webpage_url") or item.get("url") or ""
        if not url.startswith("http"):
            video_id = item.get("id") or ""
            url = f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
        if not url:
            continue
        view_count = _to_int(item.get("view_count"))
        comment_count = _to_int(item.get("comment_count"))
        duration = _to_int(item.get("duration"))
        summary = "；".join([
            part for part in [
                _clean_whitespace(item.get("description") or ""),
                f"频道 {item.get('channel') or item.get('uploader')}"
                if item.get("channel") or item.get("uploader")
                else "",
                f"播放 {view_count}" if view_count is not None else "",
                f"评论 {comment_count}" if comment_count is not None else "",
                f"时长 {duration}s" if duration is not None else "",
            ]
            if part
        ])
        candidate_score = _candidate_relevance_score(
            query=query,
            title=item.get("title") or "",
            summary=summary,
            source=item.get("channel") or item.get("uploader") or "",
            view_count=view_count,
            comment_count=comment_count,
            verified=bool(item.get("channel_is_verified")),
        )
        bias_note = _video_bias_note(
            query=query,
            title=item.get("title") or "",
            source=item.get("channel") or item.get("uploader") or "",
        )
        results.append(
            SearchResult(
                query=query,
                title=item.get("title") or "",
                summary=summary,
                url=url,
                source=item.get("channel") or item.get("uploader") or "YouTube",
                relevance_score=candidate_score,
                provider="youtube_internal",
                source_type=source_category,
                reliability_prior=reliability_prior,
                raw_metadata={
                    "platform": "youtube",
                    "video_id": item.get("id"),
                    "channel": item.get("channel"),
                    "uploader": item.get("uploader"),
                    "channel_is_verified": item.get("channel_is_verified"),
                    "view_count": view_count,
                    "comment_count": comment_count,
                    "duration_seconds": duration,
                    "bias_note": bias_note,
                    "content_access": {
                        "search_result": True,
                        "metadata": False,
                        "comments": False,
                        "transcript": False,
                        "video_body": False,
                    },
                    "candidate_assessment": _candidate_assessment(
                        title=item.get("title") or "",
                        view_count=view_count,
                        comment_count=comment_count,
                        verified=bool(item.get("channel_is_verified")),
                    ),
                    "candidate_score": candidate_score,
                },
            )
        )
    return results[:max_results]


def inspect_youtube_video(
    url: str,
    fetch_comments: bool = False,
    comments_timeout: int = 45,
) -> dict[str, Any]:
    proc = _run_ytdlp(
        [
            "--js-runtimes",
            "deno",
            "--skip-download",
            "--dump-single-json",
            url,
        ],
        timeout=90,
    )
    data = json.loads(proc.stdout)
    subtitles = _subtitle_summary(data.get("subtitles") or {})
    automatic_captions = _subtitle_summary(data.get("automatic_captions") or {})
    comments: list[dict[str, Any]] = []
    comments_status = "not_requested"
    if fetch_comments:
        comments, comments_status = _try_fetch_youtube_comments(url, comments_timeout)
    heatmap = [
        {
            "start_time": item.get("start_time"),
            "end_time": item.get("end_time"),
            "value": item.get("value"),
        }
        for item in (data.get("heatmap") or [])[:12]
    ]
    has_transcript = bool(subtitles or automatic_captions)
    return {
        "platform": "youtube",
        "url": data.get("webpage_url") or url,
        "video_id": data.get("id"),
        "title": data.get("title") or "",
        "description": data.get("description") or "",
        "author": data.get("channel") or data.get("uploader") or "",
        "author_id": data.get("channel_id") or data.get("uploader_id"),
        "author_url": data.get("channel_url") or data.get("uploader_url"),
        "author_verified": data.get("channel_is_verified"),
        "author_follower_count": data.get("channel_follower_count"),
        "published_at": data.get("upload_date") or "",
        "duration_seconds": data.get("duration"),
        "stats": {
            "view_count": data.get("view_count"),
            "like_count": data.get("like_count"),
            "comment_count": data.get("comment_count"),
        },
        "categories": data.get("categories") or [],
        "tags": data.get("tags") or [],
        "subtitles": subtitles,
        "automatic_captions": automatic_captions,
        "comments": comments,
        "comments_status": comments_status,
        "heatmap": heatmap,
        "content_access": {
            "metadata": True,
            "comments": bool(comments),
            "transcript": has_transcript,
            "video_body": False,
            "requires_asr": not has_transcript,
        },
        "assessment": assess_video_resource(
            platform="youtube",
            title=data.get("title") or "",
            author=data.get("channel") or data.get("uploader") or "",
            view_count=data.get("view_count"),
            comment_count=data.get("comment_count"),
            has_transcript=has_transcript,
            comments_available=bool(comments),
            verified=bool(data.get("channel_is_verified")),
        ),
    }


def inspect_video_resource(
    url: str,
    max_comments: int = 10,
    fetch_youtube_comments: bool = False,
) -> dict[str, Any]:
    lowered = url.lower()
    if "bilibili.com" in lowered or "b23.tv" in lowered:
        return inspect_bilibili_video(url, max_comments=max_comments)
    if "youtube.com" in lowered or "youtu.be" in lowered:
        return inspect_youtube_video(url, fetch_comments=fetch_youtube_comments)
    raise RuntimeError(f"Unsupported video URL: {url}")


def assess_video_resource(
    platform: str,
    title: str,
    author: str,
    view_count: Any = None,
    comment_count: Any = None,
    has_transcript: bool = False,
    comments_available: bool = False,
    verified: bool = False,
) -> dict[str, Any]:
    """Return an auditable resource-level access assessment."""
    view_number = _to_int(view_count) or 0
    comment_number = _to_int(comment_count) or 0
    signals = []
    risks = []
    if view_number >= 100_000:
        signals.append("high_reach")
    elif view_number >= 5_000:
        signals.append("moderate_reach")
    else:
        risks.append("low_reach")
    if comment_number >= 500:
        signals.append("rich_comment_reaction")
    elif comment_number > 0:
        signals.append("limited_comment_reaction")
    else:
        risks.append("no_comment_reaction")
    if verified:
        signals.append("verified_author")
    if has_transcript:
        signals.append("transcript_available")
    else:
        risks.append("no_transcript_requires_asr")
    if comments_available:
        signals.append("comments_sampled")
    else:
        risks.append("comments_not_sampled")
    if _looks_polemical(title):
        risks.append("polemical_or_traffic_title")
    decision = "deep_process_required"
    if has_transcript and (comments_available or comment_number == 0):
        decision = "ready_for_resource_report"
    if view_number < 500 and comment_number < 5:
        decision = "weak_signal_only"
    return {
        "platform": platform,
        "author": author,
        "signals": signals,
        "risks": risks,
        "decision": decision,
    }


def _fetch_bilibili_comments(
    client: httpx.Client,
    aid: Any,
    max_comments: int,
) -> tuple[list[dict[str, Any]], str]:
    if not aid:
        return [], "missing_aid"
    try:
        response = client.get(
            "https://api.bilibili.com/x/v2/reply",
            params={"type": 1, "oid": aid, "pn": 1, "ps": max_comments, "sort": 2},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            return [], f"api_error:{payload.get('message')}"
        comments = []
        for item in (payload.get("data") or {}).get("replies") or []:
            member = item.get("member") or {}
            content = item.get("content") or {}
            comments.append({
                "author": member.get("uname") or "",
                "author_id": member.get("mid"),
                "like_count": item.get("like"),
                "reply_count": item.get("rcount"),
                "message": content.get("message") or "",
            })
        return comments[:max_comments], "ok" if comments else "empty"
    except Exception as exc:
        return [], f"error:{exc}"


def _fetch_bilibili_subtitles(
    client: httpx.Client,
    bvid: str,
    cid: Any,
) -> tuple[list[dict[str, Any]], str]:
    if not cid:
        return [], "missing_cid"
    try:
        response = client.get(
            "https://api.bilibili.com/x/player/v2",
            params={"bvid": bvid, "cid": cid},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            return [], f"api_error:{payload.get('message')}"
        subtitles = ((payload.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
        return subtitles, "ok" if subtitles else "empty"
    except Exception as exc:
        return [], f"error:{exc}"


def _try_fetch_youtube_comments(url: str, timeout: int) -> tuple[list[dict[str, Any]], str]:
    try:
        proc = _run_ytdlp(
            [
                "--js-runtimes",
                "deno",
                "--skip-download",
                "--dump-single-json",
                "--write-comments",
                "--extractor-args",
                "youtube:comment_sort=top",
                url,
            ],
            timeout=timeout,
        )
        data = json.loads(proc.stdout)
        comments = []
        for item in data.get("comments") or []:
            comments.append({
                "author": item.get("author") or "",
                "like_count": item.get("like_count"),
                "message": item.get("text") or "",
            })
        return comments[:20], "ok" if comments else "empty"
    except subprocess.TimeoutExpired:
        return [], "timeout"
    except Exception as exc:
        return [], f"error:{exc}"


def _run_ytdlp(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    command = ["uvx", "yt-dlp", *args]
    proc = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "yt-dlp failed").strip()[-2000:])
    return proc


def _extract_bilibili_bvid(url: str) -> str:
    match = re.search(r"(BV[0-9A-Za-z]+)", url)
    if match:
        return match.group(1)
    return ""


def _subtitle_summary(subtitles: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for lang, tracks in subtitles.items():
        if str(lang).lower() == "live_chat":
            continue
        output.append({
            "language": lang,
            "formats": sorted({
                str(track.get("ext") or track.get("format") or "")
                for track in tracks
                if track.get("ext") or track.get("format")
            }),
            "track_count": len(tracks),
        })
    return output


def _clean_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value or "")
    return _clean_whitespace(html.unescape(value))


def _clean_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _to_int(value: Any) -> int | None:
    try:
        if value in {None, "--"}:
            return None
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _from_unix(value: Any) -> str:
    number = _to_int(value)
    if number is None:
        return ""
    return datetime.fromtimestamp(number, tz=timezone.utc).isoformat()


def _candidate_assessment(
    title: str,
    view_count: int | None,
    comment_count: int | None,
    verified: bool = False,
) -> dict[str, Any]:
    return assess_video_resource(
        platform="candidate",
        title=title,
        author="",
        view_count=view_count,
        comment_count=comment_count,
        has_transcript=False,
        comments_available=False,
        verified=verified,
    )


def _candidate_relevance_score(
    query: str,
    title: str,
    summary: str,
    source: str,
    view_count: int | None,
    comment_count: int | None,
    verified: bool = False,
) -> float:
    text = f"{title} {summary}".lower()
    query_terms = [
        term.lower()
        for term in re.findall(
            r"[\u4e00-\u9fff]+[0-9]+|[0-9]+[\u4e00-\u9fff]+|[\u4e00-\u9fff]{2,}|[a-z0-9]{3,}",
            query,
        )
        if term.lower() not in {
            "分析",
            "中文",
            "视频",
            "近期",
            "比赛",
            "赛后",
            "口碑",
            "票房",
            "影评",
            "风险",
            "军事",
            "军事分析",
            "时政",
            "时政博主",
        }
    ]
    overlap = 0.0
    if query_terms:
        overlap = len([term for term in query_terms if term in text]) / len(query_terms)
    views = _to_int(view_count) or 0
    comments = _to_int(comment_count) or 0
    engagement = 0.0
    if views >= 100_000:
        engagement += 0.18
    elif views >= 10_000:
        engagement += 0.10
    elif views >= 1_000:
        engagement += 0.05
    if comments >= 1_000:
        engagement += 0.16
    elif comments >= 100:
        engagement += 0.10
    elif comments > 0:
        engagement += 0.04
    if verified:
        engagement += 0.05
    penalty = 0.04 if _looks_polemical(title) else 0.0
    penalty += _required_phrase_penalty(query=query, title=title)
    if _video_bias_note(query=query, title=title, source=source):
        penalty += 0.15
    return round(max(0.05, min(0.95, 0.35 + overlap * 0.35 + engagement - penalty)), 3)


def _required_phrase_penalty(query: str, title: str) -> float:
    requirements = {
        "哪吒2": ["哪吒2", "魔童闹海", "魔童鬧海", "ne zha 2"],
        "王楚钦": ["王楚钦"],
        "台海": ["台海", "台湾", "臺海", "臺灣"],
    }
    lowered_title = title.lower()
    penalty = 0.0
    for query_token, title_tokens in requirements.items():
        if query_token not in query:
            continue
        if not any(token.lower() in lowered_title for token in title_tokens):
            penalty += 0.2
    return penalty


def _video_bias_note(query: str, title: str, source: str) -> str:
    if not any(token in query for token in ["台海", "台湾", "臺灣"]):
        return ""
    text = f"{title} {source}"
    taiwan_media_markers = [
        "TVBS",
        "中天",
        "三立",
        "民視",
        "台灣",
        "台湾",
        "臺灣",
        "新台灣",
        "東森",
        "年代",
    ]
    if any(marker.lower() in text.lower() for marker in taiwan_media_markers):
        return "taiwan_side_or_regional_crosscheck_should_be_late_stage"
    return ""


def _looks_polemical(title: str) -> bool:
    return any(
        token in title
        for token in [
            "崩溃",
            "打脸",
            "笑喷",
            "笑噴",
            "反贼",
            "反賊",
            "造谣",
            "造謠",
            "炸裂",
            "封神",
        ]
    )


def youtube_video_id(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc.endswith("youtu.be"):
        return parsed.path.strip("/")
    return (parse_qs(parsed.query).get("v") or [""])[0]
