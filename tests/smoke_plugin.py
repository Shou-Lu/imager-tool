"""Offline-ish smoke test for main.py with a minimal AstrBot stub.

It still talks to the live img-api site, so it validates the full plugin path:
config -> client -> chat handler / Web API -> response.

Run from the plugin directory:

    python tests/smoke_plugin.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Minimal AstrBot stubs
# ---------------------------------------------------------------------------
class Plain:
    type = "plain"

    def __init__(self, text: str = "") -> None:
        self.text = text

    def __repr__(self) -> str:
        return f"Plain({self.text!r})"


class Image:
    type = "image"

    def __init__(self, data: bytes | None = None, url: str | None = None) -> None:
        self.data = data
        self.url = url

    @classmethod
    def fromBytes(cls, data: bytes) -> Image:
        return cls(data=data)

    @classmethod
    def fromURL(cls, url: str) -> Image:
        return cls(url=url)

    def __repr__(self) -> str:
        size = len(self.data) if self.data else 0
        return f"Image(bytes={size}, url={self.url!r})"


class AstrBotConfig(dict):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.saved = False

    def save_config(self, *args, **kwargs) -> None:
        self.saved = True

    async def save_config_async(self, *args, **kwargs) -> bool:
        self.saved = True
        return True


class Query:
    def __init__(self, values: dict | None = None) -> None:
        self.values = values or {}

    def get(self, key, default=None, type=None):
        if key not in self.values:
            return default
        value = self.values[key]
        if type is not None:
            try:
                return type(value)
            except (TypeError, ValueError):
                return default
        return value

    def getlist(self, key):
        return [self.values[key]] if key in self.values else []


class Request:
    def __init__(self) -> None:
        self.query = Query()
        self._json: dict = {}
        self.username = "tester"
        self.method = "GET"
        self.path = "/api/v1/plugins/extensions/test"
        self.plugin_name = "astrbot_plugin_img_api"

    async def json(self, default=None):
        return self._json if self._json is not None else default

    async def body(self) -> bytes:
        return b""

    async def form(self):
        return Query()


class Context:
    def __init__(self) -> None:
        self.apis: list[tuple] = []

    def register_web_api(self, route, handler, methods, desc) -> None:
        self.apis.append((route, handler, methods, desc))


class Star:
    def __init__(self, context, config=None) -> None:
        self.context = context
        self.config = config or {}
        self.logger = logging.getLogger("stub.plugin")


class Filter:
    def command(self, name, alias=None, priority=0):
        def decorator(func):
            func.__command__ = name
            return func

        return decorator


def json_response(data=None, *, status_code=200, headers=None):
    return {"status_code": status_code, "data": data, "headers": headers or {}}


def error_response(message, *, status_code=400, data=None, headers=None):
    return {
        "status_code": status_code,
        "error": message,
        "data": data,
        "headers": headers or {},
    }


def stream_response(
    content,
    *,
    content_type="text/event-stream",
    status_code=200,
    headers=None,
):
    return {
        "status_code": status_code,
        "content": content,
        "content_type": content_type,
        "headers": headers or {},
    }


REQUEST = Request()


def install_stubs() -> None:
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event = types.ModuleType("astrbot.api.event")
    star = types.ModuleType("astrbot.api.star")
    web = types.ModuleType("astrbot.api.web")
    components = types.ModuleType("astrbot.api.message_components")

    api.AstrBotConfig = AstrBotConfig
    api.logger = logging.getLogger("stub.astrbot")
    api.message_components = components

    event.AstrMessageEvent = object
    event.filter = Filter()

    star.Context = Context
    star.Star = Star

    web.request = REQUEST
    web.json_response = json_response
    web.error_response = error_response
    web.stream_response = stream_response

    components.Plain = Plain
    components.Image = Image

    astrbot.api = api
    astrbot.logger = api.logger

    sys.modules.update(
        {
            "astrbot": astrbot,
            "astrbot.api": api,
            "astrbot.api.event": event,
            "astrbot.api.star": star,
            "astrbot.api.web": web,
            "astrbot.api.message_components": components,
        }
    )


class FakeEvent:
    def __init__(self, text: str = "") -> None:
        self.message_str = text
        self.unified_msg_origin = "test:group:1"

    def get_message_str(self) -> str:
        return self.message_str

    def get_sender_id(self) -> str:
        return "42"

    def get_sender_name(self) -> str:
        return "tester"

    def plain_result(self, text: str):
        return Plain(text)

    def chain_result(self, chain):
        return list(chain)


PASSED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"[ok] {name} {detail}".rstrip())
        return
    print(f"[FAIL] {name} {detail}".rstrip())
    raise AssertionError(name)


def find_api(context: Context, suffix: str, method: str = "GET"):
    for route, handler, methods, _desc in context.apis:
        if route.endswith(f"/{suffix}") and method in methods:
            return handler
    raise AssertionError(f"route not registered: {suffix} {method}")


async def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    install_stubs()
    sys.path.insert(0, str(ROOT))

    spec = importlib.util.spec_from_file_location("bixy_main", ROOT / "main.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["bixy_main"] = module
    spec.loader.exec_module(module)

    context = Context()
    config = AstrBotConfig(
        {
            "base_url": "https://imgapi.bixygames.com",
            "token": "",
            "default_gallery": "v1",
            "default_type": "webp",
            "send_mode": "bytes",
            "show_meta": True,
            "allow_origin": True,
            "cooldown_seconds": 0,
        }
    )
    plugin = module.BixyImgPlugin(context, config)

    check("web.routes", len(context.apis) == 9, f"{len(context.apis)} routes")
    check(
        "web.route.names",
        all(route.startswith("/astrbot_plugin_img_api/") for route, *_ in context.apis),
    )

    overview = await plugin.api_overview()
    check("api.overview", overview["data"]["ok"], str(overview["data"]["site_error"]))
    check("api.overview.count", overview["data"]["site"]["count"] > 0)
    check("api.overview.token", overview["data"]["config"]["token_configured"] is False)

    REQUEST.query = Query({"mode": "v1", "type": "webp"})
    random_api = find_api(context, "random")
    random_result = await random_api()
    check("api.random", bool(random_result["data"]["image"]["id"]))
    check("api.random.preview", bool(random_result["data"]["preview_url"]))
    image_id = random_result["data"]["image"]["id"]

    command_results = [
        item async for item in plugin.random_image_cmd(FakeEvent("随机图 dragon"))
    ]
    command_chain = command_results[0] if command_results else []
    check(
        "command.random.image",
        isinstance(command_chain, list)
        and any(isinstance(item, Image) and item.data for item in command_chain),
        repr(command_results),
    )
    check(
        "command.random.caption",
        isinstance(command_chain, list)
        and any(
            isinstance(item, Plain) and "随机图片" in item.text
            for item in command_chain
        ),
    )
    blocked_results = [
        item async for item in plugin.random_image_cmd(FakeEvent("随机图 avatar"))
    ]
    check(
        "command.random.excludes.avatar",
        bool(blocked_results)
        and isinstance(blocked_results[0], Plain)
        and "排除" in blocked_results[0].text,
        repr(blocked_results),
    )

    origin_results = [
        item async for item in plugin.random_image_cmd(FakeEvent("随机图 origin"))
    ]
    check(
        "command.random.origin.requires.token",
        bool(origin_results)
        and isinstance(origin_results[0], Plain)
        and "Token" in origin_results[0].text,
        repr(origin_results),
    )

    png_results = [
        item async for item in plugin.random_image_cmd(FakeEvent("随机图 png"))
    ]
    check(
        "command.random.png.requires.token",
        bool(png_results)
        and isinstance(png_results[0], Plain)
        and "Token" in png_results[0].text,
        repr(png_results),
    )

    REQUEST.query = Query({"id": image_id, "mode": "v1"})
    info_api = find_api(context, "info")
    info_result = await info_api()
    check("api.info", info_result["data"]["info"]["id"] == image_id)

    REQUEST.query = Query({"id": image_id, "mode": "v1", "type": "webp"})
    download_api = find_api(context, "download")
    download_result = await download_api()
    payload = b"".join(download_result["content"])
    check("api.download", len(payload) > 1000, f"{len(payload)} bytes")
    check(
        "api.download.type",
        download_result["content_type"].startswith("image/"),
        download_result["content_type"],
    )
    check(
        "api.download.header",
        "attachment" in download_result["headers"]["Content-Disposition"],
    )

    REQUEST.query = Query({"id": image_id, "mode": "v1", "type": "png"})
    png_download = await download_api()
    check(
        "api.download.png.requires.token",
        png_download["status_code"] == 400
        and "Token" in (png_download.get("error") or ""),
        str(png_download),
    )

    REQUEST.query = Query({"mode": "v1", "type": "png"})
    png_random = await random_api()
    check(
        "api.random.png.requires.token",
        png_random["status_code"] == 400 and "Token" in (png_random.get("error") or ""),
        str(png_random),
    )

    REQUEST.query = Query({"id": image_id, "mode": "v1", "type": "origin"})
    origin_download = await download_api()
    check(
        "api.download.origin.requires.token",
        origin_download["status_code"] == 400,
        str(origin_download),
    )

    REQUEST.query = Query({"id": image_id, "mode": "v1"})
    preview_api = find_api(context, "preview")
    preview_result = await preview_api()
    check(
        "api.preview.requires.token",
        preview_result["status_code"] == 400
        and "Token" in (preview_result.get("error") or ""),
        str(preview_result),
    )

    config["allow_origin"] = False
    preview_result = await preview_api()
    check(
        "api.preview.origin.disabled",
        preview_result["status_code"] == 400
        and "关闭" in (preview_result.get("error") or ""),
        str(preview_result),
    )
    config["allow_origin"] = True

    REQUEST.query = Query({"mode": "v1", "type": "origin"})
    origin_random = await random_api()
    check(
        "api.random.origin.requires.token",
        origin_random["status_code"] == 400
        and "Token" in (origin_random.get("error") or ""),
        str(origin_random),
    )

    REQUEST.query = Query({"page": "1", "page_size": "3"})
    library_api = find_api(context, "library")
    library_result = await library_api()
    check(
        "api.library.requires.token",
        library_result["status_code"] == 400
        and "Token" in (library_result.get("error") or ""),
        str(library_result),
    )

    REQUEST.query = Query()
    REQUEST._json = {
        "base_url": "https://imgapi.bixygames.com",
        "default_gallery": "v2",
        "default_type": "png",
        "request_timeout": 15,
        "max_image_mb": 10,
        "show_meta": False,
    }
    settings_api = find_api(context, "settings", "POST")
    settings_result = await settings_api()
    check("api.settings.save", settings_result["data"]["saved"] is True)
    check("api.settings.persist", config["default_gallery"] == "v2")
    check("api.settings.saved", config.saved is True)
    check(
        "api.settings.token.masked",
        settings_result["data"]["config"]["token_masked"] == "",
    )

    health_api = find_api(context, "health")
    health_result = await health_api()
    check("api.health", health_result["data"]["ok"] is True, str(health_result["data"]))

    help_results = [item async for item in plugin.help_cmd(FakeEvent("图库帮助"))]
    check("command.help", bool(help_results) and "随机图" in help_results[0].text)

    blank_context = Context()
    blank_plugin = module.BixyImgPlugin(blank_context, AstrBotConfig({}))
    blank_overview = await blank_plugin.api_overview()
    check(
        "api.overview.unconfigured",
        blank_overview["data"]["ok"] is False
        and "配置" in (blank_overview["data"].get("site_error") or ""),
        str(blank_overview["data"].get("site_error")),
    )
    blank_results = [
        item async for item in blank_plugin.random_image_cmd(FakeEvent("随机图"))
    ]
    check(
        "command.random.unconfigured",
        bool(blank_results)
        and isinstance(blank_results[0], Plain)
        and "配置" in blank_results[0].text,
        repr(blank_results),
    )
    await blank_plugin.terminate()

    await plugin.terminate()
    print(f"\n{len(PASSED)} checks passed.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
