"""AstrBot plugin: 梦星小图床 (img-api) integration with chat commands + WebUI.

API docs: https://imgapi.bixygames.com/docs
"""

from __future__ import annotations

import asyncio
import base64
import functools
import sys
import time
from pathlib import Path
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.api.web import error_response, json_response, request, stream_response

# The plugin can be imported as ``data.plugins.<name>.main`` (relative imports
# work) or as a plain ``main`` module in tests. Support both.
try:
    from .bixyimg.client import (
        ImgApiClient,
        ImgApiError,
        clean_token,
        mask_token,
        normalize_base_url,
        redact_secrets,
        sanitize_image_id,
        sanitize_tags,
        sanitize_text,
    )
    from .bixyimg.util import (
        build_caption,
        clamp_int,
        format_bytes,
        format_datetime,
        friendly_error,
        mime_to_ext,
        normalize_list_payload,
        parse_avatar_args,
        parse_list_args,
        parse_random_args,
        tags_of,
    )
except ImportError:  # pragma: no cover - only used outside a package context
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from bixyimg.client import (
        ImgApiClient,
        ImgApiError,
        clean_token,
        mask_token,
        normalize_base_url,
        redact_secrets,
        sanitize_image_id,
        sanitize_tags,
        sanitize_text,
    )
    from bixyimg.util import (
        build_caption,
        clamp_int,
        format_bytes,
        format_datetime,
        friendly_error,
        mime_to_ext,
        normalize_list_payload,
        parse_avatar_args,
        parse_list_args,
        parse_random_args,
        tags_of,
    )

PLUGIN_NAME = "astrbot_plugin_img_api"
PLUGIN_VERSION = "1.0.0"
DOCS_URL = "https://imgapi.bixygames.com/docs"

RANDOM_COMMAND_NAMES = ("随机图", "来张图", "img", "pic", "randimg")
LIST_COMMAND_NAMES = ("图库列表", "图库", "imglib")
SEARCH_COMMAND_NAMES = ("图库搜索", "搜索图片", "imgsearch")
INFO_COMMAND_NAMES = ("图库信息", "图片信息")
AVATAR_COMMAND_NAMES = ("图库头像", "图片头像")


class BixyImgPlugin(Star):
    """梦星小图床插件主类。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context, config)
        self.config = config if config is not None else {}
        self._client = self._make_client()
        self._stats_cache: tuple[float, dict[str, Any]] | None = None
        self._health_cache: tuple[float, dict[str, Any]] | None = None
        # key: unified_msg_origin + sender_id -> monotonic timestamp
        self._cooldowns: dict[str, float] = {}
        self._register_web_apis()

    # ------------------------------------------------------------------
    # Configuration and client lifecycle
    # ------------------------------------------------------------------
    def _cfg_int(self, key: str, default: int, low: int, high: int) -> int:
        return clamp_int(self.config.get(key, default), low, high, default)

    def _cfg_bool(self, key: str, default: bool) -> bool:
        value = self.config.get(key, default)
        return value if isinstance(value, bool) else default

    def _default_gallery(self) -> str:
        value = str(self.config.get("default_gallery", "v1")).lower()
        return value if value in ("v1", "v2") else "v1"

    def _default_type(self) -> str:
        value = str(self.config.get("default_type", "webp")).lower()
        if value in ("png", "origin") and not self._format_available(value):
            return "webp"
        return value if value in ("webp", "png", "origin") else "webp"

    def _default_tag_mode(self) -> str:
        value = str(self.config.get("tag_mode", "any")).lower()
        return value if value in ("any", "all") else "any"

    def _token_mode(self) -> str:
        value = str(self.config.get("token_mode", "query")).lower()
        return value if value in ("query", "header", "both") else "query"

    def _exclude_tags(self) -> set[str]:
        raw = self.config.get("exclude_tags", ["avatar"])
        if isinstance(raw, str):
            parts = raw.replace("，", ",").split(",")
        elif isinstance(raw, (list, tuple, set)):
            parts = [str(item) for item in raw]
        else:
            parts = []
        return {
            tag
            for tag in (sanitize_text(item, 32).lstrip("#").lower() for item in parts)
            if tag
        }

    def _exclude_retries(self) -> int:
        return self._cfg_int("exclude_retries", 5, 1, 10)

    def _origin_available(self) -> bool:
        """Origin needs both a token and the allow_origin switch."""
        return bool(self._client.token) and self._cfg_bool("allow_origin", True)

    def _format_available(self, image_type: str) -> bool:
        """PNG and origin both require a token on the live site."""
        if image_type == "png":
            return bool(self._client.token)
        if image_type == "origin":
            return self._origin_available()
        return True

    def _require_format(self, image_type: str) -> None:
        if image_type == "png" and not self._client.token:
            raise ImgApiError(
                "PNG 需要有效 Token；未配置时只能使用 WebP。",
                status=400,
            )
        if image_type == "origin":
            self._require_origin()

    def _require_origin(self) -> None:
        if not self._cfg_bool("allow_origin", True):
            raise ImgApiError(
                "原图输出已在插件配置中关闭，请使用 WebP 或 PNG。",
                status=400,
            )
        if not self._client.token:
            raise ImgApiError(
                "原图需要有效 Token，请先在插件配置中填写 Token；"
                "未配置时只能使用 WebP（PNG 也需要 Token）。",
                status=400,
            )

    async def _fetch_selected(
        self,
        image: dict[str, Any],
        image_type: str,
    ) -> tuple[bytes, str]:
        """Fetch the requested variant; origin never falls back to WebP."""
        if image_type == "origin":
            origin = image.get("origin")
            if not isinstance(origin, str) or not origin.startswith(
                ("http://", "https://")
            ):
                raise ImgApiError(
                    "图床没有返回原图，请检查 Token 是否有效。",
                    status=400,
                )
            return await self._client.fetch_bytes(origin)
        return await self._client.fetch_image_bytes(image)

    def _max_tags(self) -> int:
        return self._cfg_int("max_tags", 5, 1, 10)

    def _friendly(self, exc: Exception) -> str:
        """Error text that can never contain the configured token."""
        return redact_secrets(friendly_error(exc), self._client.token)

    def _make_client(self) -> ImgApiClient:
        options = {
            "token": self.config.get("token") or "",
            "token_mode": self._token_mode(),
            "timeout": self._cfg_int("request_timeout", 20, 3, 120),
            "max_bytes": int(self._cfg_int("max_image_mb", 12, 1, 64) * 1024 * 1024),
            "retries": self._cfg_int("retry_times", 1, 0, 3),
        }
        try:
            return ImgApiClient(str(self.config.get("base_url") or ""), **options)
        except ValueError:
            self.logger.warning("插件中的图床地址无效，请到插件设置中修正。")
            return ImgApiClient("", **options)

    async def _replace_client(self) -> None:
        old = self._client
        self._client = self._make_client()
        self._stats_cache = None
        self._health_cache = None
        try:
            await old.close()
        except Exception:  # pragma: no cover - best effort cleanup
            pass

    def _public_config(self) -> dict[str, Any]:
        """Configuration safe to expose to the WebUI (token is always masked)."""
        return {
            "base_url": self._client.site_url,
            "token_configured": bool(self._client.token),
            "token_masked": mask_token(self._client.token),
            "token_mode": self._token_mode(),
            "base_url_secure": self._client.site_url.lower().startswith("https://"),
            "token_mode_secure": self._token_mode() == "header",
            "origin_available": self._origin_available(),
            "png_available": bool(self._client.token),
            "default_gallery": self._default_gallery(),
            "default_type": self._default_type(),
            "tag_mode": self._default_tag_mode(),
            "send_mode": (
                "url"
                if str(self.config.get("send_mode", "bytes")) == "url"
                else "bytes"
            ),
            "request_timeout": self._cfg_int("request_timeout", 20, 3, 120),
            "retry_times": self._cfg_int("retry_times", 1, 0, 3),
            "max_image_mb": self._cfg_int("max_image_mb", 12, 1, 64),
            "cooldown_seconds": self._cfg_int("cooldown_seconds", 3, 0, 3600),
            "cache_ttl": self._cfg_int("cache_ttl", 60, 0, 3600),
            "show_meta": self._cfg_bool("show_meta", True),
            "allow_origin": self._cfg_bool("allow_origin", True),
            "webui_page_size": self._cfg_int("webui_page_size", 24, 6, 60),
            "command_page_size": self._cfg_int("command_page_size", 8, 1, 20),
            "max_tags": self._max_tags(),
            "exclude_tags": sorted(self._exclude_tags()),
            "exclude_retries": self._exclude_retries(),
            "docs_url": (
                f"{self._client.site_url}/docs" if self._client.site_url else DOCS_URL
            ),
        }

    # ------------------------------------------------------------------
    # Caching
    # ------------------------------------------------------------------
    async def _get_stats(self, *, force: bool = False) -> dict[str, Any]:
        ttl = self._cfg_int("cache_ttl", 60, 0, 3600)
        now = time.monotonic()
        if (
            not force
            and ttl > 0
            and self._stats_cache
            and now - self._stats_cache[0] < ttl
        ):
            return self._stats_cache[1]
        data = await self._client.stats()
        self._stats_cache = (now, data)
        return data

    async def _get_health(self, *, force: bool = False) -> dict[str, Any]:
        ttl = min(self._cfg_int("cache_ttl", 60, 0, 3600), 30)
        now = time.monotonic()
        if (
            not force
            and ttl > 0
            and self._health_cache
            and now - self._health_cache[0] < ttl
        ):
            return self._health_cache[1]
        data = await self._client.health()
        self._health_cache = (now, data)
        return data

    # ------------------------------------------------------------------
    # Web API registration
    # ------------------------------------------------------------------
    def _register_web_apis(self) -> None:
        register = getattr(self.context, "register_web_api", None)
        if not callable(register):
            logger.warning(
                "当前 AstrBot 版本不支持插件 Web API，图床 WebUI 面板将不可用。"
            )
            return

        routes = (
            ("overview", self.api_overview, ["GET"], "图床概览"),
            ("random", self.api_random, ["GET"], "随机图片"),
            ("preview", self.api_preview, ["GET"], "原图预览"),
            ("download", self.api_download, ["GET"], "下载图片"),
            ("library", self.api_library, ["GET"], "图库列表"),
            ("info", self.api_info, ["GET"], "图片详情"),
            ("health", self.api_health, ["GET"], "健康检查"),
            ("settings", self.api_settings_get, ["GET"], "读取设置"),
            ("settings", self.api_settings_save, ["POST"], "保存设置"),
        )
        for suffix, handler, methods, desc in routes:
            try:
                register(
                    f"/{PLUGIN_NAME}/{suffix}",
                    self._guard(handler),
                    methods,
                    desc,
                )
            except Exception:  # pragma: no cover - defensive for older cores
                self.logger.exception("注册 Web API 失败: %s", suffix)

    def _guard(self, handler):
        """Convert plugin/API exceptions into bridge-friendly error responses."""

        @functools.wraps(handler)
        async def wrapper(*args, **kwargs):
            try:
                return await handler(*args, **kwargs)
            except ImgApiError as exc:
                self.logger.warning("图床接口错误: %s", self._friendly(exc))
                # Never forward upstream 401/403/404/429 to the Dashboard:
                # AstrBot's axios interceptor treats any same-origin /api/* 401
                # as an expired Dashboard session and redirects to /auth/login.
                # Upstream image-bed auth failures are a plugin concern, so map
                # them to 400 and let the WebUI render the message.
                return error_response(
                    self._friendly(exc),
                    status_code=400,
                    headers={"Cache-Control": "no-store"},
                )
            except ValueError as exc:
                return error_response(
                    sanitize_text(str(exc), 200),
                    status_code=400,
                    headers={"Cache-Control": "no-store"},
                )
            except Exception:
                self.logger.exception("图床 Web API 处理失败")
                return error_response(
                    "插件处理请求失败，请查看 AstrBot 日志。",
                    status_code=500,
                    headers={"Cache-Control": "no-store"},
                )

        return wrapper

    # ------------------------------------------------------------------
    # Web API handlers
    # ------------------------------------------------------------------
    async def api_overview(self):
        if not self._client.site_url:
            message = "尚未配置图床 API 地址，请到插件设置中填写。"
            return json_response(
                {
                    "ok": False,
                    "site": None,
                    "site_error": message,
                    "health": None,
                    "health_error": message,
                    "tags": [],
                    "config": self._public_config(),
                    "version": PLUGIN_VERSION,
                },
                headers={"Cache-Control": "no-store"},
            )

        force = str(request.query.get("force") or "").lower() in ("1", "true", "yes")
        stats_result, health_result = await asyncio.gather(
            self._get_stats(force=force),
            self._get_health(force=force),
            return_exceptions=True,
        )

        site: dict[str, Any] | None = None
        site_error: str | None = None
        if isinstance(stats_result, Exception):
            site_error = self._friendly(stats_result)
            self.logger.warning("读取图库统计失败: %s", site_error)
        else:
            site = stats_result

        health: dict[str, Any] | None = None
        health_error: str | None = None
        if isinstance(health_result, Exception):
            health_error = self._friendly(health_result)
        else:
            health = health_result

        return json_response(
            {
                "ok": site is not None,
                "site": site,
                "site_error": site_error,
                "health": health,
                "health_error": health_error,
                "tags": (site or {}).get("tags", []),
                "config": self._public_config(),
                "version": PLUGIN_VERSION,
            },
            headers={"Cache-Control": "no-store"},
        )

    async def api_random(self):
        mode = str(request.query.get("mode") or self._default_gallery()).lower()
        if mode not in ("v1", "v2"):
            mode = self._default_gallery()

        image_type = str(request.query.get("type") or self._default_type()).lower()
        if image_type not in ("webp", "png", "origin"):
            image_type = self._default_type()

        if image_type in ("png", "origin"):
            # Block before touching the upstream API: PNG and origin need a
            # token, otherwise the site downgrades/redirects and the WebUI sees
            # a 404 or a broken image.
            self._require_format(image_type)

        tag = sanitize_tags(request.query.get("tag"), max_tags=self._max_tags())
        tag_mode = str(
            request.query.get("tag_mode") or self._default_tag_mode()
        ).lower()
        if tag_mode not in ("any", "all"):
            tag_mode = "any"

        image_id = sanitize_image_id(request.query.get("id"))
        image = await self._client.random_image(
            mode,
            image_id=image_id or None,
            tag=tag or None,
            tag_mode=tag_mode,
            image_type=image_type,
            exclude_tags=self._exclude_tags(),
            max_attempts=self._exclude_retries(),
        )

        if image_type == "png" and str(image.get("variant") or "").lower() != "png":
            raise ImgApiError(
                "图床没有返回 PNG，请检查 Token 是否有效。",
                status=400,
            )
        if image_type == "origin" and not image.get("origin"):
            raise ImgApiError(
                "图床没有返回原图，请检查 Token 是否有效。",
                status=400,
            )

        preview = (
            image.get("webp")
            if image_type == "origin"
            else (image.get("url") or image.get("webp"))
        )
        return json_response(
            {
                "gallery": mode,
                "image": image,
                "preview_url": self._client.safe_media_url(preview),
                "notice": None,
            }
        )

    async def api_preview(self):
        image_id = sanitize_image_id(request.query.get("id"))
        if not image_id:
            raise ValueError("请提供图片 ID。")

        mode = str(request.query.get("mode") or self._default_gallery()).lower()
        if mode not in ("v1", "v2"):
            mode = "v1"

        allow_origin = self._cfg_bool("allow_origin", True)
        if not allow_origin or not self._client.token:
            # Do not call /api/v1?type=origin at all without a token.
            self._require_origin()
        image = await self._client.random_image(
            mode,
            image_id=image_id,
            image_type="origin",
        )
        if not image.get("origin"):
            raise ImgApiError(
                "图床没有返回原图，请检查 Token 是否有效。",
                status=400,
            )
        data, content_type = await self._fetch_selected(image, "origin")

        # Keep the JSON payload (base64) inside a sane size.
        limit = min(self._cfg_int("max_image_mb", 12, 1, 64), 6) * 1024 * 1024
        if len(data) > limit:
            raise ImgApiError(
                f"图片约 {format_bytes(len(data))}，超过在线预览限制，请使用「下载」按钮。",
                status=413,
            )

        notice = None
        encoded = base64.b64encode(data).decode("ascii")
        return json_response(
            {
                "data_url": f"data:{content_type};base64,{encoded}",
                "content_type": content_type,
                "bytes": len(data),
                "image": image,
                "notice": notice,
            }
        )

    async def api_download(self):
        """Stream an image back to the Dashboard so downloads keep the token private."""
        image_id = sanitize_image_id(request.query.get("id"))
        if not image_id:
            raise ValueError("请提供图片 ID。")

        mode = str(request.query.get("mode") or self._default_gallery()).lower()
        if mode not in ("v1", "v2"):
            mode = "v1"

        image_type = str(
            request.query.get("type") or ("origin" if self._client.token else "webp")
        ).lower()
        if image_type not in ("webp", "png", "origin"):
            image_type = "webp"
        if image_type in ("png", "origin"):
            self._require_format(image_type)

        image = await self._client.random_image(
            mode, image_id=image_id, image_type=image_type
        )
        if image_type == "png" and str(image.get("variant") or "").lower() != "png":
            raise ImgApiError(
                "图床没有返回 PNG，请检查 Token 是否有效。",
                status=400,
            )
        if image_type == "origin" and not image.get("origin"):
            raise ImgApiError(
                "图床没有返回原图，请检查 Token 是否有效。",
                status=400,
            )
        data, content_type = await self._fetch_selected(image, image_type)
        filename = f"{image_id}.{mime_to_ext(content_type)}"
        return stream_response(
            iter([data]),
            content_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    async def api_library(self):
        page = clamp_int(request.query.get("page"), 1, 100000, 1)
        page_size = clamp_int(
            request.query.get("page_size"),
            1,
            100,
            self._cfg_int("webui_page_size", 24, 6, 60),
        )
        query = sanitize_text(request.query.get("q"), 100)
        tag = sanitize_tags(request.query.get("tag"), max_tags=self._max_tags())
        tag_mode = str(
            request.query.get("tag_mode") or self._default_tag_mode()
        ).lower()
        if tag_mode not in ("any", "all"):
            tag_mode = "any"

        payload = await self._client.list_images(
            page=page,
            page_size=page_size,
            query=query or None,
            tag=tag or None,
            tag_mode=tag_mode,
        )
        items, total = normalize_list_payload(payload, self._client.site_url)
        has_next = page * page_size < total if total else len(items) >= page_size
        return json_response(
            {
                "items": items,
                "total": total,
                "page": page,
                "page_size": page_size,
                "has_prev": page > 1,
                "has_next": has_next,
            }
        )

    async def api_info(self):
        image_id = sanitize_image_id(request.query.get("id"))
        if not image_id:
            raise ValueError("请提供图片 ID。")
        info = await self._client.info(image_id)

        preview = self._client.safe_media_url(
            info.get("webpUrl") or info.get("url") or ""
        )
        if not preview:
            preview = f"{self._client.site_url}/api/v1?id={image_id}&type=webp"
        return json_response(
            {"info": info, "preview_url": preview},
            headers={"Cache-Control": "no-store"},
        )

    async def api_health(self):
        started = time.perf_counter()
        health = await self._client.health()
        latency = round((time.perf_counter() - started) * 1000, 1)
        return json_response(
            {
                "ok": bool(health.get("ok", True)),
                "health": health,
                "latency_ms": latency,
                "config": self._public_config(),
            },
            headers={"Cache-Control": "no-store"},
        )

    async def api_settings_get(self):
        return json_response(
            {"config": self._public_config()},
            headers={"Cache-Control": "no-store"},
        )

    async def api_settings_save(self):
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是 JSON 对象。")

        if "base_url" in payload:
            self.config["base_url"] = normalize_base_url(
                str(payload.get("base_url") or "")
            )

        token_value = payload.get("token")
        if isinstance(token_value, str) and token_value.strip():
            cleaned_token = clean_token(token_value)
            if not cleaned_token:
                raise ValueError("Token 格式不正确。")
            self.config["token"] = cleaned_token
        if payload.get("clear_token") is True:
            self.config["token"] = ""

        for key, allowed in (
            ("default_gallery", {"v1", "v2"}),
            ("default_type", {"webp", "png", "origin"}),
            ("tag_mode", {"any", "all"}),
            ("send_mode", {"bytes", "url"}),
            ("token_mode", {"query", "header", "both"}),
        ):
            value = payload.get(key)
            if isinstance(value, str) and value.lower() in allowed:
                self.config[key] = value.lower()

        for key in ("show_meta", "allow_origin"):
            value = payload.get(key)
            if isinstance(value, bool):
                self.config[key] = value

        if "request_timeout" in payload:
            self.config["request_timeout"] = clamp_int(
                payload.get("request_timeout"), 3, 120, 20
            )
        if "retry_times" in payload:
            self.config["retry_times"] = clamp_int(payload.get("retry_times"), 0, 3, 1)
        if "max_image_mb" in payload:
            self.config["max_image_mb"] = clamp_int(
                payload.get("max_image_mb"), 1, 64, 12
            )
        if "cooldown_seconds" in payload:
            self.config["cooldown_seconds"] = clamp_int(
                payload.get("cooldown_seconds"), 0, 3600, 3
            )
        if "cache_ttl" in payload:
            self.config["cache_ttl"] = clamp_int(payload.get("cache_ttl"), 0, 3600, 60)
        if "webui_page_size" in payload:
            self.config["webui_page_size"] = clamp_int(
                payload.get("webui_page_size"), 6, 60, 24
            )
        if "command_page_size" in payload:
            self.config["command_page_size"] = clamp_int(
                payload.get("command_page_size"), 1, 20, 8
            )
        if "max_tags" in payload:
            self.config["max_tags"] = clamp_int(payload.get("max_tags"), 1, 10, 5)

        if "exclude_tags" in payload:
            raw = payload.get("exclude_tags")
            if isinstance(raw, str):
                parts = raw.replace("，", ",").split(",")
            elif isinstance(raw, (list, tuple)):
                parts = [str(item) for item in raw]
            else:
                parts = []
            exclude: list[str] = []
            for part in parts:
                tag = sanitize_text(part, 32).lstrip("#").lower()
                if tag and tag not in exclude:
                    exclude.append(tag)
            self.config["exclude_tags"] = exclude[:20]
        if "exclude_retries" in payload:
            self.config["exclude_retries"] = clamp_int(
                payload.get("exclude_retries"), 1, 10, 5
            )

        save_async = getattr(self.config, "save_config_async", None)
        if callable(save_async):
            await save_async()
        else:
            save_sync = getattr(self.config, "save_config", None)
            if not callable(save_sync):
                raise ValueError("当前配置对象不支持保存。")
            save_sync()

        await self._replace_client()
        # Audit trail without ever writing the token itself.
        self.logger.info(
            "插件配置已更新: base_url=%s token_mode=%s token_configured=%s",
            self._client.site_url or "(empty)",
            self._token_mode(),
            bool(self._client.token),
        )
        return json_response(
            {"saved": True, "config": self._public_config()},
            headers={"Cache-Control": "no-store"},
        )

    # ------------------------------------------------------------------
    # Chat command helpers
    # ------------------------------------------------------------------
    def _command_args(self, event: AstrMessageEvent, *names: str) -> str:
        try:
            text = event.get_message_str()
        except Exception:
            text = getattr(event, "message_str", "") or ""
        text = (text or "").strip()
        if not text:
            return ""
        text = text.lstrip("/!！。")
        parts = text.split(None, 1)
        lowered = {name.lower() for name in names}
        if parts and parts[0].lower() in lowered:
            return parts[1].strip() if len(parts) > 1 else ""
        return text

    def _cooldown_remaining(self, event: AstrMessageEvent) -> float:
        seconds = self._cfg_int("cooldown_seconds", 3, 0, 3600)
        if seconds <= 0:
            return 0.0
        key = f"{event.unified_msg_origin}:{event.get_sender_id()}"
        now = time.monotonic()
        last = self._cooldowns.get(key)
        if last is not None and now - last < seconds:
            return seconds - (now - last)
        self._cooldowns[key] = now
        if len(self._cooldowns) > 4096:
            cutoff = now - seconds - 60
            self._cooldowns = {
                item_key: value
                for item_key, value in self._cooldowns.items()
                if value >= cutoff
            }
        return 0.0

    def _format_gallery_items(
        self,
        items: list[dict[str, Any]],
        total: int,
        page: int,
        page_size: int,
        heading: str = "图库列表",
    ) -> list[str]:
        lines = [f"📚 {heading} · 第 {page} 页 · 共 {total} 张"]
        for item in items[:page_size]:
            pieces = [f"• {item['id']}"]
            if item["title"]:
                pieces.append(item["title"])
            if item["width"] and item["height"]:
                pieces.append(f"{item['width']}×{item['height']}")
            if item["tags"]:
                pieces.append(" ".join(f"#{tag}" for tag in item["tags"][:3]))
            lines.append("  ".join(pieces))
        return lines

    # ------------------------------------------------------------------
    # Chat commands
    # ------------------------------------------------------------------
    @filter.command("随机图", alias={"来张图", "img", "pic", "randimg"})
    async def random_image_cmd(self, event: AstrMessageEvent):
        """随机获取一张图床图片。用法: /随机图 [标签] [v1|v2] [webp|png|origin] [any|all] [id:图片ID]"""
        remaining = self._cooldown_remaining(event)
        if remaining > 0:
            yield event.plain_result(f"⏳ 慢一点啦，{remaining:.1f} 秒后再试。")
            return

        args = self._command_args(event, *RANDOM_COMMAND_NAMES)
        try:
            options = parse_random_args(
                args,
                gallery=self._default_gallery(),
                image_type=self._default_type(),
                tag_mode=self._default_tag_mode(),
                max_tags=self._max_tags(),
            )
            send_mode = str(self.config.get("send_mode", "bytes"))
            if options["type"] in ("png", "origin"):
                if not self._client.token:
                    yield event.plain_result(
                        "❌ PNG 和原图需要有效 Token；未配置时只能使用 WebP。"
                    )
                    return
                if options["type"] == "origin" and not self._cfg_bool(
                    "allow_origin", True
                ):
                    yield event.plain_result("❌ 原图输出已在插件配置中关闭。")
                    return
                if send_mode == "url":
                    yield event.plain_result(
                        "❌ URL 发送模式无法携带 Token，PNG/原图请改用 bytes 模式。"
                    )
                    return

            image = await self._client.random_image(
                options["gallery"],
                image_id=options["id"] or None,
                tag=options["tag"] or None,
                tag_mode=options["tag_mode"],
                image_type=options["type"],
                exclude_tags=self._exclude_tags(),
                max_attempts=self._exclude_retries(),
            )
            if (
                options["type"] == "png"
                and str(image.get("variant") or "").lower() != "png"
            ):
                raise ImgApiError(
                    "图床没有返回 PNG，请检查 Token 是否有效。",
                    status=400,
                )
            if options["type"] == "origin" and not image.get("origin"):
                raise ImgApiError(
                    "图床没有返回原图，请检查 Token 是否有效。",
                    status=400,
                )

            caption = build_caption(image, options["gallery"])

            components: list[Any] = []
            if self._cfg_bool("show_meta", True):
                components.append(Comp.Plain(caption))

            if send_mode == "url":
                url = self._client.safe_media_url(image.get("url") or image.get("webp"))
                if not url:
                    raise ImgApiError("图床没有返回安全的图片地址。")
                components.append(Comp.Image.fromURL(url))
            else:
                data, _ = await self._fetch_selected(image, options["type"])
                components.append(Comp.Image.fromBytes(data))

            yield event.chain_result(components)
            self.logger.info(
                "随机图已发送: id=%s gallery=%s type=%s",
                sanitize_text(image.get("id"), 64),
                options["gallery"],
                options["type"],
            )
        except (ImgApiError, ValueError) as exc:
            yield event.plain_result(f"❌ {self._friendly(exc)}")
        except Exception:
            self.logger.exception("随机图指令执行失败")
            yield event.plain_result("❌ 获取图片失败，请查看 AstrBot 日志。")

    @filter.command("图库列表", alias={"图库", "imglib"})
    async def list_images_cmd(self, event: AstrMessageEvent):
        """分页浏览图库。用法: /图库列表 [页码] [标签]"""
        args = self._command_args(event, *LIST_COMMAND_NAMES)
        page, tags = parse_list_args(args)
        page_size = self._cfg_int("command_page_size", 8, 1, 20)

        try:
            payload = await self._client.list_images(
                page=page,
                page_size=page_size,
                tag=tags or None,
                tag_mode=self._default_tag_mode(),
            )
            items, total = normalize_list_payload(payload, self._client.site_url)
            if not items:
                yield event.plain_result("📭 这一页没有图片。")
                return

            lines = self._format_gallery_items(
                items, total, page, page_size, "图库列表"
            )
            has_next = page * page_size < total if total else len(items) >= page_size
            if has_next:
                suffix = f" {tags}" if tags else ""
                lines.append(f"➡️ /图库列表 {page + 1}{suffix}")
            yield event.plain_result("\n".join(lines))
        except (ImgApiError, ValueError) as exc:
            yield event.plain_result(f"❌ {self._friendly(exc)}")
        except Exception:
            self.logger.exception("图库列表指令执行失败")
            yield event.plain_result("❌ 读取图库失败，请查看 AstrBot 日志。")

    @filter.command("图库搜索", alias={"搜索图片", "imgsearch"})
    async def search_images_cmd(self, event: AstrMessageEvent):
        """按关键词搜索图库。用法: /图库搜索 <关键词>"""
        query = self._command_args(event, *SEARCH_COMMAND_NAMES).strip()
        if not query:
            yield event.plain_result("用法: /图库搜索 <关键词>")
            return

        page_size = self._cfg_int("command_page_size", 8, 1, 20)
        try:
            payload = await self._client.list_images(
                page=1,
                page_size=page_size,
                query=query,
            )
            items, total = normalize_list_payload(payload, self._client.site_url)
            if not items:
                yield event.plain_result("📭 没有找到匹配的图片。")
                return
            lines = self._format_gallery_items(items, total, 1, page_size, "图库搜索")
            yield event.plain_result("\n".join(lines))
        except (ImgApiError, ValueError) as exc:
            yield event.plain_result(f"❌ {self._friendly(exc)}")
        except Exception:
            self.logger.exception("图库搜索指令执行失败")
            yield event.plain_result("❌ 搜索图库失败，请查看 AstrBot 日志。")

    @filter.command("图库统计", alias={"图库状态"})
    async def stats_cmd(self, event: AstrMessageEvent):
        """查看图库统计和站点状态。用法: /图库统计"""
        try:
            site = await self._get_stats()
            site_name = sanitize_text(site.get("siteName"), 80) or "图床"
            lines = [f"🏷️ {site_name} 统计"]
            lines.append(f"🖼️ 图片总数: {site.get('count', '-')}")

            total_size = site.get("totalSize")
            webp_size = site.get("webpSize")
            if total_size is not None:
                size_line = f"💾 总大小: {format_bytes(total_size)}"
                if webp_size is not None:
                    size_line += f"（WebP {format_bytes(webp_size)}）"
                lines.append(size_line)
            elif site.get("totalSizeMB") is not None:
                lines.append(f"💾 总大小: {site['totalSizeMB']} MB")

            tags = [
                sanitize_text(tag, 32)
                for tag in (site.get("tags") or [])[:20]
                if sanitize_text(tag, 32)
            ]
            lines.append("🏷️ 标签: " + ("、".join(tags) if tags else "暂无"))
            if site.get("newest"):
                lines.append(f"🕒 最新图片: {format_datetime(site['newest'])}")

            try:
                health = await self._get_health()
                if health.get("ok"):
                    lines.append(
                        f"✅ 服务状态: 正常（站点计数 {health.get('count', '-')}）"
                    )
                else:
                    lines.append("⚠️ 服务状态: 异常")
            except Exception:
                lines.append("⚠️ 服务状态: 无法获取")

            yield event.plain_result("\n".join(lines))
        except (ImgApiError, ValueError) as exc:
            yield event.plain_result(f"❌ {self._friendly(exc)}")
        except Exception:
            self.logger.exception("图库统计指令执行失败")
            yield event.plain_result("❌ 读取统计失败，请查看 AstrBot 日志。")

    @filter.command("图库信息", alias={"图片信息"})
    async def info_cmd(self, event: AstrMessageEvent):
        """查看单张图片的详细信息。用法: /图库信息 <图片ID>"""
        args = self._command_args(event, *INFO_COMMAND_NAMES).strip()
        if not args:
            yield event.plain_result("用法: /图库信息 <图片ID>")
            return
        image_id = args.split()[0]
        if image_id.lower().startswith("id:"):
            image_id = image_id[3:]
        elif image_id.startswith("#"):
            image_id = image_id[1:]
        try:
            info = await self._client.info(image_id)
        except (ImgApiError, ValueError) as exc:
            yield event.plain_result(f"❌ {self._friendly(exc)}")
            return
        except Exception:
            self.logger.exception("图库信息指令执行失败")
            yield event.plain_result("❌ 读取图片信息失败，请查看 AstrBot 日志。")
            return

        lines = [f"🖼️ {sanitize_text(info.get('title'), 120) or '图片详情'}"]
        lines.append(f"ID: {sanitize_text(info.get('id'), 64)}")
        if info.get("width") and info.get("height"):
            lines.append(f"尺寸: {info['width']}×{info['height']}")
        if info.get("size") is not None:
            lines.append(f"大小: {format_bytes(info['size'])}")
        tags = tags_of(info)
        if tags:
            lines.append("标签: " + " ".join(f"#{tag}" for tag in tags))
        if info.get("source"):
            lines.append(f"来源: {sanitize_text(info['source'], 200)}")
        if info.get("mime"):
            lines.append(f"格式: {sanitize_text(info['mime'], 32)}")
        if info.get("status"):
            lines.append(f"状态: {sanitize_text(info['status'], 32)}")
        if info.get("createdAt"):
            lines.append(f"上传时间: {format_datetime(info['createdAt'])}")
        if info.get("watermarked") is not None:
            lines.append(f"接口水印: {'是' if info.get('watermarked') else '否'}")
        direct_url = self._client.safe_media_url(info.get("url"))
        if direct_url:
            lines.append(f"直链: {direct_url}")
        api_url = self._client.safe_media_url(info.get("apiUrl"))
        if api_url:
            lines.append(f"API: {api_url}")
        elif info.get("id"):
            image_id = sanitize_text(info.get("id"), 64)
            lines.append(f"API: {self._client.site_url}/api/v1?id={image_id}&type=webp")
        yield event.plain_result("\n".join(lines))

    @filter.command("图库标签", alias={"图库标签列表"})
    async def tags_cmd(self, event: AstrMessageEvent):
        """列出图库当前已有的标签。用法: /图库标签"""
        try:
            site = await self._get_stats()
            tags = [str(tag) for tag in (site.get("tags") or []) if str(tag).strip()]
            excluded = self._exclude_tags()
            available = [tag for tag in tags if tag.lower() not in excluded]
            if not available:
                if excluded:
                    names = "、".join(f"#{item}" for item in sorted(excluded))
                    yield event.plain_result(
                        f"🏷️ 当前没有可用于随机图的标签（已排除 {names}）。"
                    )
                else:
                    yield event.plain_result("🏷️ 图库暂时没有标签。")
                return
            shown = available[:60]
            lines = ["🏷️ 图库标签"]
            lines.append(" · ".join(f"#{tag}" for tag in shown))
            if len(available) > len(shown):
                lines.append(f"…… 共 {len(available)} 个标签")
            if excluded:
                lines.append(
                    "已排除: " + " ".join(f"#{item}" for item in sorted(excluded))
                )
            lines.append("提示: /随机图 标签1,标签2")
            yield event.plain_result("\n".join(lines))
        except (ImgApiError, ValueError) as exc:
            yield event.plain_result(f"❌ {self._friendly(exc)}")
        except Exception:
            self.logger.exception("图库标签指令执行失败")
            yield event.plain_result("❌ 读取标签失败，请查看 AstrBot 日志。")

    @filter.command("图库头像", alias={"图片头像"})
    async def avatar_cmd(self, event: AstrMessageEvent):
        """获取图床用户头像。用法: /图库头像 [用户名] [png] [尺寸]"""
        args = self._command_args(event, *AVATAR_COMMAND_NAMES)
        options = parse_avatar_args(args)
        try:
            url = self._client.avatar_url(
                user=options["user"] or None,
                image_type=options["type"],
                size=options["size"],
            )
            data, _ = await self._client.fetch_bytes(url)
            components: list[Any] = []
            if self._cfg_bool("show_meta", True):
                owner = options["user"] or "当前用户/站长"
                components.append(Comp.Plain(f"👤 {owner} 的头像"))
            components.append(Comp.Image.fromBytes(data))
            yield event.chain_result(components)
        except (ImgApiError, ValueError) as exc:
            yield event.plain_result(f"❌ {self._friendly(exc)}")
        except Exception:
            self.logger.exception("图库头像指令执行失败")
            yield event.plain_result("❌ 获取头像失败，请查看 AstrBot 日志。")

    @filter.command("图库帮助", alias={"图库菜单", "imghelp", "图库指令"})
    async def help_cmd(self, event: AstrMessageEvent):
        """查看星屿图集插件的指令帮助。"""
        lines = [
            "🖼️ 星屿图集（梦星小图床）",
            "",
            "随机图:",
            "  /随机图 [标签] [v1|v2] [webp|png|origin] [any|all]",
            "  示例: /随机图 avatar,dragon v2 png all",
            "  指定图片: /随机图 id:图片ID",
            "",
            "图库:",
            "  /图库列表 [页码] [标签]",
            "  /图库搜索 <关键词>",
            "  /图库信息 <图片ID>",
            "  /图库统计",
            "  /图库标签",
            "  /图库头像 [用户名] [png] [尺寸]",
            "",
            "AI 工具（由模型自动调用）:",
            "  随机图 / 图库搜索 / 图库统计 / 图片详情 / 用户头像",
            "",
            "提示:",
            "  · 原图（origin）和 PNG 需要先在插件配置中填写 Token。",
            "  · V2 图库只包含已加入 V2 的图片，可能与 V1 数量不同。",
            "  · 更多设置请打开 AstrBot 插件页中的「图库面板」。",
        ]
        yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------
    # LLM tools (function calling)
    # ------------------------------------------------------------------
    @filter.llm_tool(name="imgapi_random_image")
    async def llm_random_image(
        self,
        event: AstrMessageEvent,
        tag: str = "",
        gallery: str = "",
        image_type: str = "",
        image_id: str = "",
    ) -> str:
        """从图床随机获取一张图片并直接发送给用户。

        Args:
            tag(string): 可选标签，多个标签用英文逗号分隔，例如 dragon,fish。
            gallery(string): 可选图库，v1 表示全部图库，v2 表示 V2 接口图库。
            image_type(string): 可选输出格式，webp、png 或 origin。
            image_id(string): 可选图片 ID，填写后直接获取指定图片。
        """
        if not self._client.site_url:
            return "尚未配置图床 API 地址，请先在插件设置中填写。"
        try:
            gallery_value = str(gallery).strip().lower()
            if gallery_value not in ("v1", "v2"):
                gallery_value = self._default_gallery()
            type_value = str(image_type).strip().lower()
            if type_value not in ("webp", "png", "origin"):
                type_value = self._default_type()
            if type_value in ("png", "origin"):
                self._require_format(type_value)

            wait = self._cooldown_remaining(event)
            if wait > 0:
                return f"请稍等 {wait:.1f} 秒后再请求随机图。"

            tag_value = sanitize_tags(tag, max_tags=self._max_tags())
            image_id_value = sanitize_image_id(image_id)
            image = await self._client.random_image(
                gallery_value,
                image_id=image_id_value or None,
                tag=tag_value or None,
                tag_mode=self._default_tag_mode(),
                image_type=type_value,
                exclude_tags=self._exclude_tags(),
                max_attempts=self._exclude_retries(),
            )
            if type_value == "png" and str(image.get("variant") or "").lower() != "png":
                return "图床没有返回 PNG，请检查 Token 是否有效。"
            if type_value == "origin" and not image.get("origin"):
                return "图床没有返回原图，请检查 Token 是否有效。"

            data, _ = await self._fetch_selected(image, type_value)
            components: list[Any] = []
            if self._cfg_bool("show_meta", True):
                components.append(Comp.Plain(build_caption(image, gallery_value)))
            components.append(Comp.Image.fromBytes(data))
            await event.send(event.chain_result(components))

            tags = ", ".join(tags_of(image)) or "无"
            return (
                f"已发送图片 id={image.get('id')}，"
                f"格式={image.get('variant') or type_value}，标签={tags}。"
            )
        except (ImgApiError, ValueError) as exc:
            return f"获取图片失败：{self._friendly(exc)}"
        except Exception:
            self.logger.exception("LLM 随机图工具执行失败")
            return "获取图片失败，请稍后再试。"

    @filter.llm_tool(name="imgapi_search_gallery")
    async def llm_search_gallery(
        self,
        event: AstrMessageEvent,
        query: str = "",
        tag: str = "",
        page: int = 1,
    ) -> str:
        """搜索图库并返回匹配的图片列表。

        Args:
            query(string): 搜索关键词，匹配标题、来源或标签。
            tag(string): 可选标签筛选，多个标签用英文逗号分隔。
            page(number): 页码，从 1 开始。
        """
        if not self._client.site_url:
            return "尚未配置图床 API 地址，请先在插件设置中填写。"
        query_value = sanitize_text(query, 100)
        tag_value = sanitize_tags(tag, max_tags=self._max_tags())
        if not query_value and not tag_value:
            return "请提供搜索关键词或标签。"

        page_value = clamp_int(page, 1, 1000, 1)
        page_size = min(self._cfg_int("command_page_size", 8, 1, 20), 10)
        try:
            payload = await self._client.list_images(
                page=page_value,
                page_size=page_size,
                query=query_value or None,
                tag=tag_value or None,
                tag_mode=self._default_tag_mode(),
            )
            items, total = normalize_list_payload(payload, self._client.site_url)
            if not items:
                return "没有找到匹配的图片。"
            return "\n".join(
                self._format_gallery_items(
                    items, total, page_value, page_size, "图库搜索"
                )
            )
        except (ImgApiError, ValueError) as exc:
            return f"搜索图库失败：{self._friendly(exc)}"
        except Exception:
            self.logger.exception("LLM 图库搜索工具执行失败")
            return "搜索图库失败，请稍后再试。"

    @filter.llm_tool(name="imgapi_gallery_stats")
    async def llm_gallery_stats(self, event: AstrMessageEvent) -> str:
        """获取图库统计信息，包括图片数量、大小、标签和站点状态。"""
        if not self._client.site_url:
            return "尚未配置图床 API 地址，请先在插件设置中填写。"
        try:
            site = await self._get_stats(force=True)
            site_name = sanitize_text(site.get("siteName"), 80) or "图床"
            lines = [f"🏷️ {site_name} 统计"]
            lines.append(f"🖼️ 图片总数: {site.get('count', '-')}")
            if site.get("totalSize") is not None:
                size_line = f"💾 总大小: {format_bytes(site['totalSize'])}"
                if site.get("webpSize") is not None:
                    size_line += f"（WebP {format_bytes(site['webpSize'])}）"
                lines.append(size_line)
            tags = [
                sanitize_text(tag, 32)
                for tag in (site.get("tags") or [])[:20]
                if sanitize_text(tag, 32)
            ]
            lines.append("🏷️ 标签: " + ("、".join(tags) if tags else "暂无"))
            if site.get("newest"):
                lines.append(f"🕒 最新图片: {format_datetime(site['newest'])}")
            try:
                health = await self._get_health(force=True)
                lines.append(
                    "✅ 服务状态: 正常" if health.get("ok") else "⚠️ 服务状态: 异常"
                )
            except Exception:
                lines.append("⚠️ 服务状态: 无法获取")
            return "\n".join(lines)
        except (ImgApiError, ValueError) as exc:
            return f"读取统计失败：{self._friendly(exc)}"
        except Exception:
            self.logger.exception("LLM 图库统计工具执行失败")
            return "读取统计失败，请稍后再试。"

    @filter.llm_tool(name="imgapi_image_info")
    async def llm_image_info(
        self,
        event: AstrMessageEvent,
        image_id: str = "",
    ) -> str:
        """根据图片 ID 获取图片详细信息。

        Args:
            image_id(string): 图片 ID。
        """
        if not self._client.site_url:
            return "尚未配置图床 API 地址，请先在插件设置中填写。"
        image_id_value = sanitize_image_id(image_id)
        if not image_id_value:
            return "请提供图片 ID。"
        try:
            info = await self._client.info(image_id_value)
        except (ImgApiError, ValueError) as exc:
            return f"读取图片信息失败：{self._friendly(exc)}"
        except Exception:
            self.logger.exception("LLM 图片详情工具执行失败")
            return "读取图片信息失败，请稍后再试。"

        lines = [f"🖼️ {sanitize_text(info.get('title'), 120) or '图片详情'}"]
        lines.append(f"ID: {sanitize_text(info.get('id'), 64)}")
        if info.get("width") and info.get("height"):
            lines.append(f"尺寸: {info['width']}×{info['height']}")
        if info.get("size") is not None:
            lines.append(f"大小: {format_bytes(info['size'])}")
        tags = tags_of(info)
        if tags:
            lines.append("标签: " + " ".join(f"#{tag}" for tag in tags))
        if info.get("source"):
            lines.append(f"来源: {sanitize_text(info['source'], 200)}")
        if info.get("status"):
            lines.append(f"状态: {sanitize_text(info['status'], 32)}")
        if info.get("createdAt"):
            lines.append(f"上传时间: {format_datetime(info['createdAt'])}")
        direct_url = self._client.safe_media_url(info.get("url"))
        if direct_url:
            lines.append(f"直链: {direct_url}")
        api_url = self._client.safe_media_url(info.get("apiUrl"))
        if api_url:
            lines.append(f"API: {api_url}")
        return "\n".join(lines)

    @filter.llm_tool(name="imgapi_user_avatar")
    async def llm_user_avatar(
        self,
        event: AstrMessageEvent,
        user: str = "",
        size: int = 512,
        image_type: str = "jpg",
    ) -> str:
        """获取图床用户头像并发送给用户。

        Args:
            user(string): 可选用户名；留空时使用 Token 用户或站长头像。
            size(number): 头像尺寸，32 到 1024，默认 512。
            image_type(string): 头像格式，jpg 或 png。
        """
        if not self._client.site_url:
            return "尚未配置图床 API 地址，请先在插件设置中填写。"
        try:
            user_value = sanitize_text(user, 64)
            type_value = str(image_type).strip().lower()
            if type_value not in ("jpg", "png"):
                type_value = "jpg"
            url = self._client.avatar_url(
                user=user_value or None,
                image_type=type_value,
                size=clamp_int(size, 32, 1024, 512),
            )
            data, _ = await self._client.fetch_bytes(url)
            await event.send(event.chain_result([Comp.Image.fromBytes(data)]))
            return f"已发送 {user_value or '当前用户/站长'} 的头像。"
        except (ImgApiError, ValueError) as exc:
            return f"获取头像失败：{self._friendly(exc)}"
        except Exception:
            self.logger.exception("LLM 头像工具执行失败")
            return "获取头像失败，请稍后再试。"

    async def terminate(self):
        """插件卸载/停用时关闭 HTTP 连接池。"""
        try:
            await self._client.close()
        except Exception:
            pass
