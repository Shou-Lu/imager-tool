"""Unit tests for token delivery, URL sanitisation and avatar exclusion.

No network access: requests go through ``httpx.MockTransport``.

    python tests/unit_client.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bixyimg.client import (  # noqa: E402
    ImgApiClient,
    ImgApiError,
    normalize_base_url,
)

BASE = "https://imgapi.bixygames.com"
SECRET = "img_secret_value"
PASSED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"[ok] {name} {detail}".rstrip())
        return
    print(f"[FAIL] {name} {detail}".rstrip())
    raise AssertionError(name)


def image_payload(tags: list[str], token_in_url: bool = False) -> dict:
    suffix = "&token=" + SECRET if token_in_url else ""
    return {
        "id": "a" * 24,
        "url": f"{BASE}/api/v1?id={'a' * 24}&type=webp{suffix}",
        "origin": f"{BASE}/api/v1?id={'a' * 24}&type=origin{suffix}",
        "webp": f"{BASE}/api/v1?id={'a' * 24}&type=webp{suffix}",
        "width": 1024,
        "height": 768,
        "tags": tags,
        "watermarked": False,
        "variant": "webp",
        "originRequiresToken": True,
    }


async def make_client(mode: str, handler) -> ImgApiClient:
    client = ImgApiClient(BASE, token=SECRET, token_mode=mode)
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )
    return client


async def test_token_modes() -> None:
    for mode in ("query", "header", "both"):
        captured: dict[str, str | None] = {}

        def handler(
            request: httpx.Request, mode=mode, captured=captured
        ) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"ok": True, "count": 1})

        client = await make_client(mode, handler)
        try:
            await client.health()
        finally:
            await client.close()

        url = captured.get("url") or ""
        auth = captured.get("auth")
        if mode == "query":
            check("token.query.url", f"token={SECRET}" in url, url)
            check("token.query.no.header", auth is None)
        elif mode == "header":
            check("token.header.url", "token=" not in url, url)
            check("token.header.auth", auth == f"Bearer {SECRET}", str(auth))
        else:
            check("token.both.url", f"token={SECRET}" in url, url)
            check("token.both.auth", auth == f"Bearer {SECRET}", str(auth))

    check(
        "base_url.api.endpoint",
        normalize_base_url(f"{BASE}/api/v1") == BASE,
        normalize_base_url(f"{BASE}/api/v1"),
    )


async def test_sanitize_payload() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=image_payload(["fish"], token_in_url=True))

    client = await make_client("query", handler)
    try:
        result = await client.random_image("v1")
    finally:
        await client.close()

    check("sanitize.request.token", f"token={SECRET}" in captured["url"])
    for key in ("url", "origin", "webp"):
        value = str(result.get(key) or "")
        check(f"sanitize.{key}", "token=" not in value, value)


async def test_exclude_avatar() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        payload = (
            image_payload(["avatar"]) if len(calls) == 1 else image_payload(["fish"])
        )
        return httpx.Response(200, json=payload)

    client = await make_client("query", handler)
    try:
        result = await client.random_image(
            "v1", exclude_tags={"avatar"}, max_attempts=3
        )
    finally:
        await client.close()

    check(
        "exclude.retry",
        len(calls) == 2 and result.get("tags") == ["fish"],
        f"calls={len(calls)} tags={result.get('tags')}",
    )


async def test_exclude_all_blocked() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=image_payload(["avatar"]))

    client = await make_client("query", handler)
    try:
        try:
            await client.random_image("v1", exclude_tags={"avatar"}, max_attempts=3)
        except ImgApiError as exc:
            check(
                "exclude.all.404",
                exc.status == 404 and len(calls) == 3,
                f"{exc.status} calls={len(calls)} {exc.message}",
            )
        else:
            check("exclude.all.404", False, "expected ImgApiError")
    finally:
        await client.close()


async def test_exclude_requested_tag() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=image_payload(["avatar"]))

    client = await make_client("query", handler)
    try:
        try:
            await client.random_image(
                "v1", tag="avatar", exclude_tags={"avatar"}, max_attempts=3
            )
        except ImgApiError as exc:
            check(
                "exclude.requested.tag",
                exc.status == 400 and not calls and "排除" in exc.message,
                f"{exc.status} calls={len(calls)} {exc.message}",
            )
        else:
            check("exclude.requested.tag", False, "expected ImgApiError")
    finally:
        await client.close()


async def test_fetch_variant() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "type=png" in str(request.url):
            return httpx.Response(
                200,
                content=b"\x89PNG\r\n\x1a\n" + b"\x00" * 128,
                headers={"content-type": "image/png"},
            )
        return httpx.Response(
            200,
            content=b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 128,
            headers={"content-type": "image/webp"},
        )

    client = await make_client("query", handler)
    try:
        png = image_payload(["fish"])
        png["variant"] = "png"
        png["url"] = f"{BASE}/api/v1?id={'a' * 24}&type=png"
        png["webp"] = f"{BASE}/api/v1?id={'a' * 24}&type=webp"
        _, png_type = await client.fetch_image_bytes(png)
        check("fetch.png.url", "type=png" in calls[-1], calls[-1])
        check("fetch.png.content_type", png_type == "image/png", png_type)
        check("fetch.png.token", f"token={SECRET}" in calls[-1], calls[-1])

        origin = image_payload(["fish"])
        origin["variant"] = "origin"
        origin["origin"] = f"{BASE}/api/v1?id={'a' * 24}&type=origin"
        origin["url"] = f"{BASE}/api/v1?id={'a' * 24}&type=origin"
        await client.fetch_image_bytes(origin, prefer_origin=True)
        check("fetch.origin.url", "type=origin" in calls[-1], calls[-1])
        check("fetch.origin.token", f"token={SECRET}" in calls[-1], calls[-1])

        fallback = image_payload(["fish"])
        fallback["variant"] = "webp"
        fallback["origin"] = None
        await client.fetch_image_bytes(fallback, prefer_origin=True)
        check("fetch.origin.fallback", "type=webp" in calls[-1], calls[-1])
    finally:
        await client.close()


async def test_image_download_token() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            content=b"\x00" * 2048,
            headers={"content-type": "image/webp"},
        )

    client = await make_client("query", handler)
    try:
        data, content_type = await client.fetch_bytes(
            f"{BASE}/api/v1?id={'a' * 24}&type=webp"
        )
        avatar_url = client.avatar_url(user="alice", image_type="png", size=128)
    finally:
        await client.close()

    check("download.token", f"token={SECRET}" in captured["url"], captured["url"])
    check("download.bytes", len(data) == 2048 and content_type == "image/webp")
    check("avatar.token", f"token={SECRET}" in avatar_url, avatar_url)


async def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    await test_token_modes()
    await test_sanitize_payload()
    await test_exclude_avatar()
    await test_exclude_all_blocked()
    await test_exclude_requested_tag()
    await test_fetch_variant()
    await test_image_download_token()
    print(f"\n{len(PASSED)} checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
