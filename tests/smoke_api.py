"""Live smoke test for the img-api client.

Run from the plugin directory:

    python tests/smoke_api.py

This does not need a running AstrBot instance. Set ``BIXY_TOKEN`` in the
environment to additionally exercise token-only endpoints (origin/list).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bixyimg.client import (  # noqa: E402
    ImgApiClient,
    ImgApiError,
    normalize_base_url,
    sanitize_image_id,
    sanitize_tags,
)
from bixyimg.util import (  # noqa: E402
    build_caption,
    format_bytes,
    friendly_error,
    normalize_list_payload,
)

PASSED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"[ok] {name} {detail}".rstrip())
        return
    print(f"[FAIL] {name} {detail}".rstrip())
    raise AssertionError(name)


async def main() -> None:
    print(f"base url: {normalize_base_url('imgapi.bixygames.com/')}")
    check(
        "normalize_base_url",
        normalize_base_url("imgapi.bixygames.com/") == "https://imgapi.bixygames.com",
    )
    check("normalize_base_url.empty", normalize_base_url("") == "")
    check(
        "sanitize_tags",
        sanitize_tags(" avatar，dragon, avatar ,,") == "avatar,dragon",
        sanitize_tags(" avatar，dragon, avatar ,,"),
    )
    check("sanitize_image_id", sanitize_image_id("6deaf10d7044a377e4a2f91a") != "")

    token = os.environ.get("BIXY_TOKEN", "")

    empty_client = ImgApiClient("")
    try:
        try:
            await empty_client.health()
        except ImgApiError as exc:
            check(
                "unconfigured.client",
                exc.status == 400 and "配置" in exc.message,
                f"{exc.status} {exc.message}",
            )
        else:
            check("unconfigured.client", False, "expected ImgApiError")
    finally:
        await empty_client.close()

    client = ImgApiClient("https://imgapi.bixygames.com", token=token, timeout=20)
    try:
        health = await client.health()
        check("health", bool(health.get("ok")), str(health))

        stats = await client.stats()
        check("stats.count", int(stats.get("count", 0)) > 0, str(stats.get("count")))
        check("stats.tags", isinstance(stats.get("tags"), list), str(stats.get("tags")))
        print(
            "      site:",
            stats.get("siteName"),
            "| size:",
            format_bytes(stats.get("totalSize")),
        )

        image = await client.random_image("v1")
        check("random.v1.id", bool(image.get("id")), str(image.get("id")))
        check("random.v1.url", bool(image.get("url")), str(image.get("url")))
        print("      caption:", build_caption(image, "v1"))

        data, content_type = await client.fetch_image_bytes(image)
        check("download", len(data) > 1000, f"{len(data)} bytes, {content_type}")
        check("download.mime", content_type.startswith("image/"), content_type)

        if stats.get("tags"):
            first_tag = next(
                (tag for tag in stats["tags"] if str(tag).lower() != "avatar"),
                stats["tags"][0],
            )
            tagged = await client.random_image("v1", tag=str(first_tag))
            check(
                "random.tag",
                str(first_tag).lower()
                in [str(t).lower() for t in tagged.get("tags", [])],
                f"tag={first_tag} tags={tagged.get('tags')}",
            )

        for _ in range(3):
            picked = await client.random_image(
                "v1", exclude_tags={"avatar"}, max_attempts=8
            )
            check(
                "random.exclude.avatar",
                "avatar" not in [str(tag).lower() for tag in picked.get("tags", [])],
                f"id={picked.get('id')} tags={picked.get('tags')}",
            )

        try:
            await client.random_image("v1", tag="avatar", exclude_tags={"avatar"})
        except ImgApiError as exc:
            check(
                "random.exclude.tag.blocked",
                exc.status == 400 and "排除" in exc.message,
                f"{exc.status} {exc.message}",
            )
        else:
            check("random.exclude.tag.blocked", False, "expected ImgApiError")

        info = await client.info(image["id"])
        check("info", info.get("id") == image["id"], info.get("title", ""))

        avatar, avatar_type = await client.fetch_bytes(client.avatar_url(size=64))
        check("avatar", len(avatar) > 100, f"{len(avatar)} bytes, {avatar_type}")

        try:
            await client.random_image("v1", tag="__definitely_missing_tag__")
        except ImgApiError as exc:
            check("missing.tag.404", exc.status == 404, f"{exc.status} {exc.message}")
        else:
            check("missing.tag.404", False, "expected ImgApiError")

        try:
            listing = await client.list_images(page=1, page_size=3)
        except ImgApiError as exc:
            check("list", exc.status == 401, f"{exc.status} {friendly_error(exc)}")
        else:
            items, total = normalize_list_payload(listing, client.site_url)
            check("list", bool(items), f"{len(items)} items, total={total}")

        if token:
            listing = await client.list_images(page=1, page_size=3)
            items, total = normalize_list_payload(listing, client.site_url)
            check("list.token", bool(items), f"{len(items)} items, total={total}")

            origin = await client.random_image("v1", image_type="origin")
            check(
                "origin.token",
                bool(origin.get("origin")),
                str(origin.get("origin")),
            )
            origin_data, origin_type = await client.fetch_image_bytes(
                origin, prefer_origin=True
            )
            check(
                "origin.download",
                len(origin_data) > 1000,
                f"{len(origin_data)} bytes, {origin_type}",
            )
    finally:
        await client.close()

    print(f"\n{len(PASSED)} checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
