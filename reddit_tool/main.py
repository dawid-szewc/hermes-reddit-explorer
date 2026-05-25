"""
Reddit Tool — browse Reddit without an API key.

Provides: hot/new/top posts, search, post comments, subreddit info,
subreddit suggestions, and front-page browsing.

No credentials required — uses Reddit's public JSON endpoints.
Drop this file in tools/ and add "reddit-*" names to toolsets.py.
"""

import json
import re
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional dependency check
# ---------------------------------------------------------------------------

def _check_requests() -> bool:
    try:
        import requests  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Shared helpers (ported 1:1 from OpenWebUI version)
# ---------------------------------------------------------------------------

HEADERS = {"User-Agent": "HermesAgent-RedditTool/1.0 (educational tool)"}


def _get(url: str, params: dict = None):
    import requests
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def _format_number(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}k"
    return str(n)


def _media_block(post: dict) -> str:
    lines = []
    hint = post.get("post_hint", "")
    url = post.get("url", "")
    preview = post.get("preview", {})
    media = post.get("media", {})
    secure_media = post.get("secure_media", {})
    gallery = post.get("is_gallery", False)
    gallery_data = post.get("gallery_data", {})
    media_metadata = post.get("media_metadata", {})

    if hint == "image" or (
        url and re.search(r"\.(jpg|jpeg|png|gif|webp)(\?.*)?$", url, re.I)
    ):
        lines.append(f"\n> 🖼️ **Image**\n\n![post image]({url})\n")
    elif hint == "hosted:video":
        video_url = (
            (secure_media or {}).get("reddit_video", {}).get("fallback_url")
            or (media or {}).get("reddit_video", {}).get("fallback_url")
            or url
        )
        lines.append(f"\n> 🎬 **Video** — [▶ Watch on Reddit]({video_url})\n")
    elif hint == "rich:video":
        embed_url = (
            (secure_media or {}).get("oembed", {}).get("url")
            or (media or {}).get("oembed", {}).get("url")
            or url
        )
        thumb = (secure_media or {}).get("oembed", {}).get("thumbnail_url") or (
            media or {}
        ).get("oembed", {}).get("thumbnail_url", "")
        provider = (secure_media or {}).get("oembed", {}).get("provider_name", "External")
        lines.append(f"\n> 🎥 **{provider} Video** — [▶ Open Video]({embed_url})\n")
        if thumb:
            lines.append(f"![thumbnail]({thumb})\n")
    elif gallery and gallery_data:
        items = gallery_data.get("items", [])[:4]
        lines.append(f"\n> 🖼️ **Gallery** ({len(gallery_data.get('items', []))} images)\n")
        for item in items:
            mid = item.get("media_id", "")
            meta = media_metadata.get(mid, {})
            src = meta.get("s", {})
            img_url = src.get("u", "").replace("&amp;", "&")
            if img_url:
                lines.append(f"![gallery image]({img_url})\n")
    elif preview:
        imgs = preview.get("images", [])
        if imgs:
            src = imgs[0].get("source", {})
            img_url = src.get("url", "").replace("&amp;", "&")
            if img_url:
                lines.append(f"\n> 🔗 **Preview**\n\n![preview]({img_url})\n")

    return "\n".join(lines)


def _format_post(post: dict, index: int = None, show_media: bool = True) -> str:
    d = post.get("data", post)
    title = d.get("title", "No title")
    author = d.get("author", "[deleted]")
    subreddit = d.get("subreddit", "")
    score = _format_number(d.get("score", 0))
    num_comments = _format_number(d.get("num_comments", 0))
    upvote_ratio = int(d.get("upvote_ratio", 0) * 100)
    flair = d.get("link_flair_text", "")
    is_nsfw = d.get("over_18", False)
    is_spoiler = d.get("spoiler", False)
    permalink = f"https://reddit.com{d.get('permalink', '')}"
    selftext = d.get("selftext", "")
    url = d.get("url", "")
    post_hint = d.get("post_hint", "")

    prefix = f"**{index}.** " if index is not None else ""
    nsfw_tag = " 🔞`NSFW`" if is_nsfw else ""
    spoiler_tag = " 🙈`SPOILER`" if is_spoiler else ""
    flair_tag = f" `{flair}`" if flair else ""

    lines = [
        "---",
        f"{prefix}### {title}{nsfw_tag}{spoiler_tag}{flair_tag}",
        f"👤 **u/{author}** · 📌 **r/{subreddit}** · ⬆️ **{score}** ({upvote_ratio}%) · 💬 **{num_comments} comments**",
    ]

    if selftext and selftext not in ("[removed]", "[deleted]"):
        preview_text = selftext[:400].strip()
        if len(selftext) > 400:
            preview_text += "…"
        lines.append(f"\n> {preview_text.replace(chr(10), chr(10) + '> ')}")

    if show_media:
        media_block = _media_block(d)
        if media_block:
            lines.append(media_block)

    if post_hint == "link" and url and "reddit.com" not in url:
        lines.append(f"\n🔗 [External link]({url})")

    lines.append(f"\n[💬 View full post & comments]({permalink})")
    return "\n".join(lines)


def _format_comment(comment: dict, depth: int = 0) -> str:
    d = comment.get("data", {})
    if not d or d.get("kind") == "more":
        return ""
    author = d.get("author", "[deleted]")
    body = d.get("body", "")
    score = _format_number(d.get("score", 0))
    if not body or body in ("[removed]", "[deleted]"):
        return ""
    indent = "  " * depth
    lines = [f"{indent}---", f"{indent}**u/{author}** · ⬆️ {score}"]
    for line in body[:500].split("\n"):
        lines.append(f"{indent}> {line}")
    if len(body) > 500:
        lines.append(f"{indent}> *(comment truncated)*")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _reddit_hot(subreddit: str, limit: int = 10) -> str:
    limit = max(1, min(25, limit))
    data = _get(
        f"https://www.reddit.com/r/{subreddit}/hot.json",
        {"limit": limit, "raw_json": 1},
    )
    if "error" in data:
        return json.dumps({"error": f"Could not fetch r/{subreddit}: {data['error']}"})
    posts = data.get("data", {}).get("children", [])
    if not posts:
        return json.dumps({"error": f"No hot posts found in r/{subreddit}."})
    lines = [f"# 🔥 Hot Posts — r/{subreddit}\n"]
    for i, post in enumerate(posts, 1):
        lines.append(_format_post(post["data"], index=i))
    lines.append(f"\n---\n*Showing {len(posts)} hot posts from [r/{subreddit}](https://reddit.com/r/{subreddit})*")
    return "\n".join(lines)


def _reddit_new(subreddit: str, limit: int = 10) -> str:
    limit = max(1, min(25, limit))
    data = _get(
        f"https://www.reddit.com/r/{subreddit}/new.json",
        {"limit": limit, "raw_json": 1},
    )
    if "error" in data:
        return json.dumps({"error": f"Could not fetch r/{subreddit}: {data['error']}"})
    posts = data.get("data", {}).get("children", [])
    if not posts:
        return json.dumps({"error": f"No new posts found in r/{subreddit}."})
    lines = [f"# 🆕 New Posts — r/{subreddit}\n"]
    for i, post in enumerate(posts, 1):
        lines.append(_format_post(post["data"], index=i))
    lines.append(f"\n---\n*Showing {len(posts)} newest posts from [r/{subreddit}](https://reddit.com/r/{subreddit})*")
    return "\n".join(lines)


def _reddit_top(subreddit: str, time_filter: str = "day", limit: int = 10) -> str:
    valid = ["hour", "day", "week", "month", "year", "all"]
    if time_filter not in valid:
        time_filter = "day"
    limit = max(1, min(25, limit))
    data = _get(
        f"https://www.reddit.com/r/{subreddit}/top.json",
        {"t": time_filter, "limit": limit, "raw_json": 1},
    )
    if "error" in data:
        return json.dumps({"error": f"Could not fetch r/{subreddit}: {data['error']}"})
    posts = data.get("data", {}).get("children", [])
    if not posts:
        return json.dumps({"error": f"No top posts found in r/{subreddit}."})
    time_labels = {
        "hour": "Past Hour", "day": "Today", "week": "This Week",
        "month": "This Month", "year": "This Year", "all": "All Time",
    }
    label = time_labels.get(time_filter, time_filter)
    lines = [f"# 🏆 Top Posts — r/{subreddit} · {label}\n"]
    for i, post in enumerate(posts, 1):
        lines.append(_format_post(post["data"], index=i))
    lines.append(f"\n---\n*Top posts from [r/{subreddit}](https://reddit.com/r/{subreddit}) — {label}*")
    return "\n".join(lines)


def _reddit_search(
    subreddit: str, query: str,
    sort: str = "relevance", time_filter: str = "all", limit: int = 10
) -> str:
    limit = max(1, min(25, limit))
    params = {
        "q": query, "sort": sort, "t": time_filter,
        "limit": limit, "raw_json": 1,
    }
    if subreddit.lower() != "all":
        params["restrict_sr"] = "1"
    data = _get(f"https://www.reddit.com/r/{subreddit}/search.json", params)
    if "error" in data:
        return json.dumps({"error": f"Search failed: {data['error']}"})
    posts = data.get("data", {}).get("children", [])
    if not posts:
        return json.dumps({"error": f"No results found for '{query}' in r/{subreddit}."})
    scope = f"r/{subreddit}" if subreddit.lower() != "all" else "all of Reddit"
    lines = [f'# 🔍 Search: "{query}" in {scope}\n']
    for i, post in enumerate(posts, 1):
        lines.append(_format_post(post["data"], index=i))
    lines.append(f"\n---\n*{len(posts)} results for '{query}' in {scope}*")
    return "\n".join(lines)


def _reddit_comments(
    post_id: str, subreddit: str, limit: int = 15, sort: str = "top"
) -> str:
    limit = max(1, min(50, limit))
    data = _get(
        f"https://www.reddit.com/r/{subreddit}/comments/{post_id}.json",
        {"limit": limit, "sort": sort, "raw_json": 1, "depth": 3},
    )
    if isinstance(data, dict) and "error" in data:
        return json.dumps({"error": f"Could not load comments: {data['error']}"})
    if not isinstance(data, list) or len(data) < 2:
        return json.dumps({"error": "Unexpected response format from Reddit."})

    post_listing = data[0].get("data", {}).get("children", [])
    if not post_listing:
        return json.dumps({"error": "Could not find post data."})
    post = post_listing[0].get("data", {})
    comment_listing = data[1].get("data", {}).get("children", [])

    lines = [
        _format_post(post, show_media=True),
        f"\n---\n## 💬 Top Comments ({sort})\n",
    ]
    count = 0
    for child in comment_listing:
        if child.get("kind") == "t1":
            comment_str = _format_comment(child, depth=0)
            if comment_str:
                lines.append(comment_str)
                count += 1
                replies = child.get("data", {}).get("replies", {})
                if isinstance(replies, dict):
                    for reply in replies.get("data", {}).get("children", [])[:3]:
                        if reply.get("kind") == "t1":
                            reply_str = _format_comment(reply, depth=1)
                            if reply_str:
                                lines.append(reply_str)
    if count == 0:
        lines.append("*No comments yet.*")
    return "\n".join(lines)


def _reddit_subreddit_info(subreddit: str) -> str:
    data = _get(f"https://www.reddit.com/r/{subreddit}/about.json", {"raw_json": 1})
    if "error" in data:
        return json.dumps({"error": f"Could not load info for r/{subreddit}: {data['error']}"})
    d = data.get("data", {})
    if not d:
        return json.dumps({"error": f"Subreddit r/{subreddit} not found or is private."})

    name = d.get("display_name_prefixed", f"r/{subreddit}")
    title = d.get("title", "")
    desc = d.get("public_description", "") or d.get("description", "")[:500]
    subscribers = _format_number(d.get("subscribers", 0))
    active = _format_number(d.get("active_user_count", 0))
    nsfw = "🔞 NSFW" if d.get("over18") else "✅ SFW"
    community_icon = (
        d.get("community_icon", "").split("?")[0] if d.get("community_icon") else ""
    )
    banner = (
        d.get("banner_background_image", "").split("?")[0]
        if d.get("banner_background_image") else ""
    )
    lang = d.get("lang", "en")
    url = f"https://reddit.com/r/{d.get('display_name', subreddit)}"

    lines = [f"# 🏠 {name}"]
    if community_icon:
        lines.append(f"![icon]({community_icon})")
    if banner:
        lines.append(f"![banner]({banner})")
    if title:
        lines.append(f"### {title}")
    if desc:
        lines.append(f"\n{desc[:600]}")
    lines += [
        "\n| Stat | Value |",
        "|------|-------|",
        f"| 👥 Members | **{subscribers}** |",
        f"| 🟢 Online now | **{active}** |",
        f"| 🌍 Language | `{lang}` |",
        f"| 🔒 Content | {nsfw} |",
        f"| 🔗 Link | [{name}]({url}) |",
    ]

    rules_data = _get(f"https://www.reddit.com/r/{subreddit}/about/rules.json")
    if rules_data and "rules" in rules_data:
        rules = rules_data["rules"][:5]
        if rules:
            lines.append("\n### 📜 Rules")
            for i, rule in enumerate(rules, 1):
                lines.append(
                    f"{i}. **{rule.get('short_name', '')}** — {rule.get('description', '')[:150]}"
                )
    return "\n".join(filter(None, lines))


def _reddit_suggest(topic: str) -> str:
    TOPIC_SUBREDDIT_MAP = {
        "news": ["worldnews", "news", "UpliftingNews"],
        "technology": ["technology", "tech", "gadgets", "hardware"],
        "programming": ["programming", "learnprogramming", "webdev", "Python"],
        "ai": ["artificial", "MachineLearning", "LocalLLaMA", "singularity"],
        "gaming": ["gaming", "pcgaming", "NintendoSwitch"],
        "science": ["science", "physics", "biology", "space"],
        "finance": ["personalfinance", "investing", "stocks", "wallstreetbets"],
        "movies": ["movies", "MovieSuggestions", "horror", "scifi"],
        "music": ["Music", "listentothis", "WeAreTheMusicMakers"],
        "sports": ["sports", "nba", "nfl", "soccer", "formula1"],
        "food": ["food", "Cooking", "recipes", "AskCulinary"],
        "travel": ["travel", "solotravel", "backpacking"],
        "funny": ["funny", "memes", "ProgrammerHumor"],
        "ask": ["AskReddit", "NoStupidQuestions", "explainlikeimfive"],
        "diy": ["DIY", "woodworking", "3Dprinting", "electronics"],
    }

    data = _get(
        "https://www.reddit.com/subreddits/search.json",
        {"q": topic, "limit": 8, "raw_json": 1},
    )

    results = []
    if data and "data" in data:
        for child in data["data"].get("children", []):
            d = child.get("data", {})
            name = d.get("display_name", "")
            title = d.get("title", "")
            desc = (d.get("public_description") or "")[:150]
            subs = _format_number(d.get("subscribers", 0))
            active = _format_number(d.get("active_user_count", 0))
            nsfw = "🔞" if d.get("over18") else ""
            if name:
                results.append((name, title, desc, subs, active, nsfw))

    topic_lower = topic.lower()
    local_suggestions = []
    for key, subs in TOPIC_SUBREDDIT_MAP.items():
        if key in topic_lower or topic_lower in key:
            local_suggestions = subs[:3]
            break

    lines = [f"# 🗺️ Subreddits for: **{topic}**\n"]
    if results:
        lines.append("## 🔍 Search Results\n")
        for name, title, desc, subs, active, nsfw in results[:6]:
            lines.append(f"### r/{name} {nsfw}")
            if title:
                lines.append(f"*{title}*")
            lines.append(f"👥 **{subs} members** · 🟢 **{active} online**")
            if desc:
                lines.append(f"> {desc}")
            lines.append(f"🔗 [Visit r/{name}](https://reddit.com/r/{name})\n")
    if local_suggestions:
        lines.append("\n## 💡 Also Try\n")
        for sub in local_suggestions:
            lines.append(f"- [r/{sub}](https://reddit.com/r/{sub})")
    if not results and not local_suggestions:
        lines.append(f"No specific subreddits found for '{topic}'. Try searching r/all.")
    return "\n".join(lines)


def _reddit_frontpage(feed: str = "popular", limit: int = 10) -> str:
    valid_feeds = ["popular", "all", "best"]
    if feed not in valid_feeds:
        feed = "popular"
    limit = max(1, min(25, limit))
    data = _get(f"https://www.reddit.com/r/{feed}.json", {"limit": limit, "raw_json": 1})
    if "error" in data:
        return json.dumps({"error": f"Could not load Reddit feed: {data['error']}"})
    posts = data.get("data", {}).get("children", [])
    if not posts:
        return json.dumps({"error": "No posts found on the front page."})
    feed_emoji = {"popular": "🌟", "all": "🌐", "best": "✨"}
    emoji = feed_emoji.get(feed, "📋")
    lines = [f"# {emoji} Reddit Front Page — r/{feed}\n"]
    for i, post in enumerate(posts, 1):
        lines.append(_format_post(post["data"], index=i))
    lines.append(f"\n---\n*Reddit r/{feed} · {len(posts)} posts*")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

REDDIT_HOT_SCHEMA = {
    "name": "reddit_hot",
    "description": "Fetch the current HOT (trending/popular) posts from a subreddit.",
    "parameters": {
        "type": "object",
        "properties": {
            "subreddit": {
                "type": "string",
                "description": "Subreddit name without r/, e.g. 'worldnews', 'gaming'. Use 'all' for Reddit front page."
            },
            "limit": {
                "type": "integer",
                "description": "Number of posts to return (1-25, default 10).",
                "default": 10
            }
        },
        "required": ["subreddit"]
    }
}

REDDIT_NEW_SCHEMA = {
    "name": "reddit_new",
    "description": "Fetch the NEWEST / most recent posts from a subreddit.",
    "parameters": {
        "type": "object",
        "properties": {
            "subreddit": {
                "type": "string",
                "description": "Subreddit name without r/, e.g. 'technology', 'news'."
            },
            "limit": {
                "type": "integer",
                "description": "Number of posts to return (1-25, default 10).",
                "default": 10
            }
        },
        "required": ["subreddit"]
    }
}

REDDIT_TOP_SCHEMA = {
    "name": "reddit_top",
    "description": "Fetch the TOP-rated posts from a subreddit over a time period. Use for 'best of', 'most popular ever', etc.",
    "parameters": {
        "type": "object",
        "properties": {
            "subreddit": {
                "type": "string",
                "description": "Subreddit name without r/, e.g. 'science', 'funny'."
            },
            "time_filter": {
                "type": "string",
                "enum": ["hour", "day", "week", "month", "year", "all"],
                "description": "Time range (default: day).",
                "default": "day"
            },
            "limit": {
                "type": "integer",
                "description": "Number of posts (1-25, default 10).",
                "default": 10
            }
        },
        "required": ["subreddit"]
    }
}

REDDIT_SEARCH_SCHEMA = {
    "name": "reddit_search",
    "description": "Search for posts matching a query within a specific subreddit or all of Reddit. Use subreddit='all' to search everywhere.",
    "parameters": {
        "type": "object",
        "properties": {
            "subreddit": {
                "type": "string",
                "description": "Subreddit name without r/. Use 'all' to search everywhere."
            },
            "query": {
                "type": "string",
                "description": "Search keywords or phrase."
            },
            "sort": {
                "type": "string",
                "enum": ["relevance", "hot", "top", "new", "comments"],
                "description": "Sort order (default: relevance).",
                "default": "relevance"
            },
            "time_filter": {
                "type": "string",
                "enum": ["hour", "day", "week", "month", "year", "all"],
                "description": "Time range (default: all).",
                "default": "all"
            },
            "limit": {
                "type": "integer",
                "description": "Number of results (1-25, default 10).",
                "default": 10
            }
        },
        "required": ["subreddit", "query"]
    }
}

REDDIT_COMMENTS_SCHEMA = {
    "name": "reddit_comments",
    "description": "Fetch comments for a specific Reddit post. You need the post_id (short alphanumeric ID from the URL, e.g. '1abc23').",
    "parameters": {
        "type": "object",
        "properties": {
            "post_id": {
                "type": "string",
                "description": "Reddit post ID from the URL, e.g. '1abc23' from reddit.com/r/sub/comments/1abc23/..."
            },
            "subreddit": {
                "type": "string",
                "description": "Subreddit where the post lives."
            },
            "limit": {
                "type": "integer",
                "description": "Number of top-level comments (1-50, default 15).",
                "default": 15
            },
            "sort": {
                "type": "string",
                "enum": ["top", "best", "new", "controversial", "old"],
                "description": "Comment sort order (default: top).",
                "default": "top"
            }
        },
        "required": ["post_id", "subreddit"]
    }
}

REDDIT_SUBREDDIT_INFO_SCHEMA = {
    "name": "reddit_subreddit_info",
    "description": "Get info about a subreddit: description, subscriber count, active users, rules, and related stats.",
    "parameters": {
        "type": "object",
        "properties": {
            "subreddit": {
                "type": "string",
                "description": "Subreddit name without r/."
            }
        },
        "required": ["subreddit"]
    }
}

REDDIT_SUGGEST_SCHEMA = {
    "name": "reddit_suggest",
    "description": "Suggest the best subreddits for a given topic or interest. Use when the user asks 'where on Reddit can I find X' or 'what subreddit is good for Y'.",
    "parameters": {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "Topic, interest, or theme to find subreddits for, e.g. 'cooking', 'AI news', 'workout motivation'."
            }
        },
        "required": ["topic"]
    }
}

REDDIT_FRONTPAGE_SCHEMA = {
    "name": "reddit_frontpage",
    "description": "Get the Reddit front page / global feeds. Use when the user asks 'what's happening on Reddit' or 'show me Reddit' without specifying a subreddit.",
    "parameters": {
        "type": "object",
        "properties": {
            "feed": {
                "type": "string",
                "enum": ["popular", "all", "best"],
                "description": "Which feed: 'popular' (most upvoted across Reddit), 'all' (everything), 'best' (hot). Default: popular.",
                "default": "popular"
            },
            "limit": {
                "type": "integer",
                "description": "Number of posts (1-25, default 10).",
                "default": 10
            }
        },
        "required": []
    }
}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def register(registry):
    registry.register(
        name="reddit_hot",
        toolset="reddit",
        schema=REDDIT_HOT_SCHEMA,
        handler=lambda args, **kw: _reddit_hot(
            subreddit=args.get("subreddit", "all"),
            limit=args.get("limit", 10),
        ),
        check_fn=_check_requests,
    )

    registry.register(
        name="reddit_new",
        toolset="reddit",
        schema=REDDIT_NEW_SCHEMA,
        handler=lambda args, **kw: _reddit_new(
            subreddit=args.get("subreddit", "all"),
            limit=args.get("limit", 10),
        ),
        check_fn=_check_requests,
    )

    registry.register(
        name="reddit_top",
        toolset="reddit",
        schema=REDDIT_TOP_SCHEMA,
        handler=lambda args, **kw: _reddit_top(
            subreddit=args.get("subreddit", "all"),
            time_filter=args.get("time_filter", "day"),
            limit=args.get("limit", 10),
        ),
        check_fn=_check_requests,
    )

    registry.register(
        name="reddit_search",
        toolset="reddit",
        schema=REDDIT_SEARCH_SCHEMA,
        handler=lambda args, **kw: _reddit_search(
            subreddit=args.get("subreddit", "all"),
            query=args.get("query", ""),
            sort=args.get("sort", "relevance"),
            time_filter=args.get("time_filter", "all"),
            limit=args.get("limit", 10),
        ),
        check_fn=_check_requests,
    )

    registry.register(
        name="reddit_comments",
        toolset="reddit",
        schema=REDDIT_COMMENTS_SCHEMA,
        handler=lambda args, **kw: _reddit_comments(
            post_id=args.get("post_id", ""),
            subreddit=args.get("subreddit", ""),
            limit=args.get("limit", 15),
            sort=args.get("sort", "top"),
        ),
        check_fn=_check_requests,
    )

    registry.register(
        name="reddit_subreddit_info",
        toolset="reddit",
        schema=REDDIT_SUBREDDIT_INFO_SCHEMA,
        handler=lambda args, **kw: _reddit_subreddit_info(
            subreddit=args.get("subreddit", ""),
        ),
        check_fn=_check_requests,
    )

    registry.register(
        name="reddit_suggest",
        toolset="reddit",
        schema=REDDIT_SUGGEST_SCHEMA,
        handler=lambda args, **kw: _reddit_suggest(
            topic=args.get("topic", ""),
        ),
        check_fn=_check_requests,
    )

    registry.register(
        name="reddit_frontpage",
        toolset="reddit",
        schema=REDDIT_FRONTPAGE_SCHEMA,
        handler=lambda args, **kw: _reddit_frontpage(
            feed=args.get("feed", "popular"),
            limit=args.get("limit", 10),
        ),
        check_fn=_check_requests,
    )
