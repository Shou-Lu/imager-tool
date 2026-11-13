"""Formatting, parsing and error-message helpers shared by chat and WebUI."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from .client import (
    ImgApiError,
    redact_secrets,
    sanitize_image_id,
    sanitize_media_url,
    sanitize_tags,
    sanitize_text,
)

DEFAULT_PAGE_SIZE = 24


def clamp_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(number, high))


def clamp_float(value: Any, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(number, high))


def format_bytes(value: Any) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "-"
    if size <= 0:
        return "0 B"
    units = ("B", "KB", "MB", "GB", "TB")
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    if index == 0:
        return f"{int(size)} {units[index]}"
    return f"{size:.2f} {units[index]}"


def mime_to_ext(content_type: str) -> str:
    mapping = {
        "image/webp": "webp",
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/gif": "gif",
        "image/avif": "avif",
    }
    return mapping.get((content_type or "").split(";")[0].strip().lower(), "img")


def format_datetime(value: Any) -> str:
    if not value:
        return "-"
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return sanitize_text(text, 19)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        parsed = parsed.astimezone()
    except (OSError, ValueError):
        pass
    return parsed.strftime("%Y-%m-%d %H:%M")


def tags_of(image: dict[str, Any]) -> list[str]:
    tags = image.get("tags")
    if isinstance(tags, str):
        tags = [item.strip() for item in tags.split(",")]
    if not isinstance(tags, list):
        return []
    return [tag for tag in (sanitize_text(item, 32) for item in tags) if tag]


def build_caption(image: dict[str, Any], gallery: str = "v1") -> str:
    label = "V2 接口图库" if gallery == "v2" else "V1 全部图库"
    parts = [f"🖼️ 随机图片 · {label}"]
    width, height = image.get("width"), image.get("height")
    if width and height:
        parts.append(f"{width}×{height}")
    variant = sanitize_text(image.get("variant"), 16).lower()
    if variant:
        parts.append(
            {"png": "PNG", "origin": "原图", "webp": "WebP"}.get(variant, variant)
        )
    tags = tags_of(image)[:4]
    if tags:
        parts.append(" ".join(f"#{tag}" for tag in tags))
    image_id = sanitize_text(image.get("id"), 64)
    if image_id:
        parts.append(f"ID {image_id}")
    if image.get("watermarked"):
        parts.append("含水印")
    return " · ".join(parts)


def friendly_error(exc: Exception) -> str:
    """Turn client exceptions into short messages suitable for chat/UI."""
    if isinstance(exc, ImgApiError):
        status = exc.status
        detail = sanitize_text(redact_secrets(exc.message or ""), 200)
        if status == 429:
            if exc.retry_after:
                return f"请求过于频繁，请约 {exc.retry_after} 秒后再试。"
            return "请求过于频繁，请稍后再试。"
        if status == 401:
            return (
                f"图床鉴权失败：{detail or 'Token 无效或未配置'}。"
                "请在插件设置中检查 Token（这不是 AstrBot 登录失效）。"
            )
        if status == 403:
            return f"图床拒绝了请求：{detail or 'Token 被封禁或权限不足'}。"
        if status == 404:
            return detail or "没有找到符合条件的图片。"
        if status >= 500:
            return "图床服务暂时不可用，请稍后再试。"
        return detail or f"图床接口返回错误（HTTP {status}）。"
    if isinstance(exc, ValueError):
        return sanitize_text(redact_secrets(str(exc)), 200)
    return "操作失败，请稍后再试。"


def parse_random_args(
    raw: str,
    *,
    gallery: str,
    image_type: str,
    tag_mode: str,
    max_tags: int = 5,
) -> dict[str, Any]:
    """Parse ``/随机图 avatar,dragon v2 png all id:xxx`` style arguments."""
    options: dict[str, Any] = {
        "gallery": gallery if gallery in ("v1", "v2") else "v1",
        "type": image_type if image_type in ("webp", "png", "origin") else "webp",
        "tag_mode": tag_mode if tag_mode in ("any", "all") else "any",
        "tag": "",
        "id": "",
    }
    tags: list[str] = []
    for token in (raw or "").replace("，", ",").split():
        low = token.lower()
        if low in ("v1", "v2"):
            options["gallery"] = low
        elif low in ("webp", "png", "origin"):
            options["type"] = low
        elif low in ("any", "all"):
            options["tag_mode"] = low
        elif low.startswith("id:"):
            options["id"] = token[3:].strip()
        elif low.startswith("tag:"):
            tags.append(token[4:])
        elif low.startswith("#") and len(token) > 1:
            options["id"] = token[1:]
        else:
            tags.append(token)

    if options["id"]:
        options["id"] = sanitize_image_id(options["id"])
    options["tag"] = sanitize_tags(",".join(tags), max_tags=max_tags)
    return options


def parse_list_args(raw: str) -> tuple[int, str]:
    """Parse ``/图库列表 2 wolf`` into ``(page, tags)``."""
    page = 1
    tags: list[str] = []
    for token in (raw or "").replace("，", ",").split():
        if token.isdigit() and page == 1 and not tags:
            page = max(1, int(token))
        else:
            tags.append(token)
    return page, sanitize_tags(",".join(tags))


def parse_avatar_args(raw: str) -> dict[str, Any]:
    """Parse ``/图库头像 alice png 256``."""
    result: dict[str, Any] = {"user": "", "type": "jpg", "size": 512}
    for token in (raw or "").split():
        low = token.lower()
        if low in ("png", "jpg", "jpeg"):
            result["type"] = "png" if low == "png" else "jpg"
        elif token.isdigit():
            result["size"] = clamp_int(token, 32, 1024, 512)
        elif not result["user"]:
            result["user"] = sanitize_text(token, 64)
    return result


def _absolute(base_url: str, value: Any, prefix: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    value = sanitize_text(value, 2000)
    if value.startswith(("http://", "https://")):
        return sanitize_media_url(base_url, value)
    return sanitize_media_url(base_url, f"{base_url}/{prefix}/{value.lstrip('/')}")


def normalize_list_item(item: Any, base_url: str) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    image_id = sanitize_text(item.get("id") or item.get("_id"), 64)
    if not image_id:
        return None

    webp = _absolute(
        base_url,
        item.get("webp") or item.get("webpUrl") or item.get("thumb"),
        "w",
    )
    origin = _absolute(
        base_url,
        item.get("origin") or item.get("originUrl") or item.get("url"),
        "i",
    )
    preview = (
        webp or origin or (f"{base_url}/api/v1?id={quote(image_id, safe='')}&type=webp")
    )

    return {
        "id": image_id,
        "title": sanitize_text(item.get("title"), 120),
        "source": sanitize_text(item.get("source"), 200),
        "tags": tags_of(item)[:8],
        "width": clamp_int(item.get("width"), 0, 100000, 0) or None,
        "height": clamp_int(item.get("height"), 0, 100000, 0) or None,
        "size": clamp_int(item.get("size") or item.get("webpSize"), 0, 2**53, 0)
        or None,
        "webp": webp,
        "origin": origin,
        "preview": preview,
        "created_at": sanitize_text(
            item.get("createdAt") or item.get("created_at"), 40
        ),
    }


def normalize_list_payload(
    payload: Any,
    base_url: str,
) -> tuple[list[dict[str, Any]], int]:
    """Accept the documented list shape plus a few likely variants."""
    raw_items: list[Any] = []
    total = 0

    if isinstance(payload, list):
        raw_items = payload
        total = len(payload)
    elif isinstance(payload, dict):
        for key in ("items", "list", "images", "records", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                raw_items = value
                break
            if isinstance(value, dict):
                for nested_key in ("items", "list", "images", "records"):
                    nested = value.get(nested_key)
                    if isinstance(nested, list):
                        raw_items = nested
                        break
                if raw_items:
                    break
        for key in ("total", "count", "totalCount"):
            value = payload.get(key)
            if isinstance(value, (int, float)):
                total = int(value)
                break
        if not total:
            total = len(raw_items)

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_items:
        normalized = normalize_list_item(raw, base_url)
        if normalized and normalized["id"] not in seen:
            seen.add(normalized["id"])
            items.append(normalized)
    return items, total


def looks_like_image_id(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z0-9_-]{6,64}$", value or ""))
