"""Async HTTP client for the 梦星的小图床 (img-api) public API.

The client is deliberately framework agnostic so it can be exercised without a
running AstrBot instance.

API reference: https://imgapi.bixygames.com/docs
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
from typing import Any
from urllib.parse import (
    parse_qsl,
    quote,
    urlencode,
    urljoin,
    urlparse,
    urlsplit,
    urlunsplit,
)

import httpx

USER_AGENT = "AstrBot-Plugin-ImgApi/1.0"
NOT_CONFIGURED_MESSAGE = "尚未配置图床 API 地址，请到插件设置中填写。"
MAX_ERROR_TEXT = 300
MAX_TOKEN_LENGTH = 512
MAX_REDIRECTS = 3
MAX_JSON_BYTES = 2 * 1024 * 1024
ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
TOKEN_MODES = ("query", "header", "both")
# C0/C1 controls, zero-width characters and bidi overrides. These can be used
# to spoof chat messages, break logs or hide text in the WebUI.
CONTROL_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f"
    r"\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]"
)
TOKEN_QUERY_RE = re.compile(r"(?i)([?&]token=)[^&#\s]*")


class ImgApiError(Exception):
    """A user-facing error raised by the img-api client."""

    def __init__(
        self,
        message: str,
        *,
        status: int = 0,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.retry_after = retry_after


def sanitize_text(value: Any, max_length: int = 200) -> str:
    """Strip control/zero-width characters and collapse whitespace.

    Used for values that come from the image bed or from chat input before they
    are rendered into chat messages, logs or the WebUI.
    """
    text = CONTROL_RE.sub("", str(value or ""))
    text = " ".join(text.split())
    return text[:max_length]


def redact_secrets(text: Any, token: str | None = None) -> str:
    """Remove tokens from free-form text (URLs, upstream error bodies, logs)."""
    value = TOKEN_QUERY_RE.sub(r"\1***", str(text or ""))
    cleaned = clean_token(token)
    if len(cleaned) >= 6:
        value = value.replace(cleaned, mask_token(cleaned))
    return value


def _is_private_host(host: str) -> bool:
    """True for loopback/link-local/private addresses and local hostnames."""
    name = (host or "").strip().lower().strip("[]")
    if not name:
        return True
    if name == "localhost" or name.endswith((".localhost", ".local", ".internal")):
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def sanitize_media_url(base_url: str, value: Any) -> str:
    """Validate a media URL before it is rendered in a browser/chat client.

    Allows same-site URLs and public CDNs, but rejects credentials, tokens,
    non-http(s) schemes, HTTPS-to-HTTP downgrades and private/loopback hosts
    that differ from the configured site. This prevents a compromised image
    bed from turning the Dashboard into an internal-network probe.
    """
    if not isinstance(value, str):
        return ""
    value = sanitize_text(value, 2000)
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        base = urlsplit(base_url)
    except ValueError:
        return ""
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https") or not parsed.hostname:
        return ""
    if parsed.username or parsed.password:
        return ""
    if (base.scheme or "").lower() == "https" and scheme != "https":
        return ""
    host = parsed.hostname.lower()
    if host != (base.hostname or "").lower() and _is_private_host(host):
        return ""
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() != "token"
    ]
    return urlunsplit((scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def clean_token(value: str | None) -> str:
    """Normalise a token pasted by the user.

    Tolerates ``Bearer``, ``?token=`` and ``token=`` prefixes so users can paste
    either a bare token or a full query fragment. Control characters, fragments
    and extra query parameters are discarded so a pasted URL cannot smuggle
    additional parameters into every request.
    """
    token = CONTROL_RE.sub("", str(value or "")).strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    token = token.lstrip("?&").strip()
    if token.lower().startswith("token="):
        token = token[6:].strip()
    token = "".join(token.split())
    token = token.split("&", 1)[0].split("#", 1)[0].strip()
    return token[:MAX_TOKEN_LENGTH]


def mask_token(token: str | None) -> str:
    """Return a display-safe token preview, never the complete token."""
    token = clean_token(token)
    if not token:
        return ""
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}{'*' * 6}{token[-4:]}"


def normalize_base_url(value: str | None) -> str:
    """Validate and normalise a site base URL.

    Empty input stays empty: the plugin must not silently fall back to a
    hard-coded image bed. Accepts ``img.example.com`` (scheme is added) and
    preserves a path prefix for deployments behind a reverse proxy.
    """
    raw = CONTROL_RE.sub("", str(value or "")).strip()
    if not raw:
        return ""
    if any(char.isspace() for char in raw):
        raise ValueError("图床地址不能包含空格或换行。")
    if not re.match(r"^https?://", raw, re.IGNORECASE):
        raw = "https://" + raw.lstrip("/")

    parsed = urlparse(raw)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("图床地址的端口不合法。") from exc
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        raise ValueError("图床地址必须是合法的 http(s) URL。")
    if parsed.username or parsed.password:
        raise ValueError("图床地址不能包含用户名或密码。")
    if parsed.query or parsed.fragment:
        raise ValueError("图床地址不能包含查询参数或锚点，请只填写站点根地址。")
    if port and not (1 <= port <= 65535):
        raise ValueError("图床地址的端口不合法。")
    if len(raw) > 500:
        raise ValueError("图床地址过长。")

    path = parsed.path.rstrip("/")
    # Accept both the site root and the canonical API endpoint, e.g.
    # https://imgapi.bixygames.com/api/v1 -> https://imgapi.bixygames.com
    for suffix in ("/api/v1.png", "/api/v2.png", "/api/v1", "/api/v2"):
        if path.endswith(suffix):
            path = path[: -len(suffix)].rstrip("/")
            break
    return f"{parsed.scheme.lower()}://{parsed.netloc}{path}"


def sanitize_image_id(value: str | None) -> str:
    """Validate a 24-ish character image id before putting it in a URL."""
    image_id = (value or "").strip()
    if image_id and not ID_RE.match(image_id):
        raise ValueError("图片 ID 格式不正确。")
    return image_id


def sanitize_tags(
    value: str | None,
    *,
    max_tags: int = 8,
    max_length: int = 32,
) -> str:
    """Normalise a comma separated tag query.

    Tags are sent as query parameters by httpx, so no URL escaping is needed.
    We only bound the input to keep accidental giant queries out.
    """
    raw = (value or "").replace("，", ",")
    seen: list[str] = []
    for part in raw.split(","):
        tag = sanitize_text(part, max_length)
        if not tag:
            continue
        if tag not in seen:
            seen.append(tag)
        if len(seen) >= max_tags:
            break
    return ",".join(seen)


def guess_image_mime(data: bytes) -> str:
    """Best-effort image MIME detection for responses missing a content type."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:12] in (b"ftypavif", b"ftypavis"):
        return "image/avif"
    return "image/webp"


class ImgApiClient:
    """Small async wrapper around the public img-api endpoints."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = "",
        *,
        timeout: float = 20.0,
        max_bytes: int = 12 * 1024 * 1024,
        retries: int = 1,
        token_mode: str = "query",
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.token = clean_token(token)
        mode = (token_mode or "").strip().lower()
        # The site accepts both ?token= and Authorization; query is the
        # canonical form requested by the deployment.
        self.token_mode = mode if mode in TOKEN_MODES else "query"
        self.timeout = max(3.0, min(float(timeout), 120.0))
        self.max_bytes = max(256 * 1024, int(max_bytes))
        self.retries = max(0, min(int(retries), 3))
        self._closed = False
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            # Redirects are followed manually so a cross-origin ``Location``
            # can never receive the token or the Authorization header.
            follow_redirects=False,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )

    # ------------------------------------------------------------------
    # Low level helpers
    # ------------------------------------------------------------------
    @property
    def site_url(self) -> str:
        return self.base_url

    @staticmethod
    def _origin(url: str) -> tuple[str, str, int | None] | None:
        """Return a comparable (scheme, host, port) tuple or ``None``."""
        try:
            parsed = urlparse(url)
            scheme = (parsed.scheme or "").lower()
            host = (parsed.hostname or "").lower()
            port = parsed.port
        except ValueError:
            return None
        if scheme == "https":
            port = port or 443
        elif scheme == "http":
            port = port or 80
        else:
            return None
        if not host:
            return None
        return scheme, host, port

    def _check_url(self, url: str) -> str:
        """Only ever talk to the configured site (or URLs it returned)."""
        if not self.base_url:
            raise ImgApiError(NOT_CONFIGURED_MESSAGE, status=400)
        try:
            parsed = urlparse(url)
        except ValueError as exc:
            raise ImgApiError("图片地址格式不正确。") from exc
        if parsed.username or parsed.password:
            raise ImgApiError("拒绝访问带账号信息的图片地址。")
        if self._origin(url) is None or self._origin(url) != self._origin(
            self.base_url
        ):
            raise ImgApiError("拒绝访问非配置站点的地址。")
        # Fragments are never sent to the server; drop them so logs and
        # payloads stay clean.
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))

    def _url(self, path: str) -> str:
        if not self.base_url:
            raise ImgApiError(NOT_CONFIGURED_MESSAGE, status=400)
        if path.startswith(("http://", "https://")):
            return self._check_url(path)
        return f"{self.base_url}/{path.lstrip('/')}"

    def _headers(self, accept: str = "application/json") -> dict[str, str]:
        headers = {"Accept": accept}
        if self.token and self.token_mode in ("header", "both"):
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _with_token(self, url: str) -> str:
        """Append ``?token=`` when the configured token mode asks for it."""
        if not self.token or self.token_mode not in ("query", "both"):
            return url
        parts = urlsplit(url)
        query = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() != "token"
        ]
        query.append(("token", self.token))
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )

    @staticmethod
    def _strip_token(value: str) -> str:
        """Remove ``token`` from URLs before they reach chat/WebUI payloads."""
        if not isinstance(value, str) or not value.lower().startswith(
            ("http://", "https://")
        ):
            return value
        parts = urlsplit(value)
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        filtered = [(key, item) for key, item in pairs if key.lower() != "token"]
        if len(filtered) == len(pairs):
            return value
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(filtered),
                parts.fragment,
            )
        )

    def _sanitize_payload(self, value: Any) -> Any:
        """Recursively strip query tokens from API payloads."""
        if isinstance(value, dict):
            return {key: self._sanitize_payload(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._sanitize_payload(item) for item in value]
        if isinstance(value, str):
            return redact_secrets(self._strip_token(value), self.token)
        return value

    def safe_media_url(self, value: Any) -> str:
        """Return an http(s) URL that is safe to hand to the WebUI."""
        return sanitize_media_url(self.base_url, value)

    def _error_from_body(
        self,
        status: int,
        headers: Any,
        body: bytes,
    ) -> ImgApiError:
        message = ""
        # Never decode an unbounded error page into memory.
        text = body[:16384].decode("utf-8", errors="replace").strip()
        if text:
            try:
                payload = json.loads(text)
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                for key in ("message", "error", "detail"):
                    value = payload.get(key)
                    if isinstance(value, str) and value.strip():
                        message = value.strip()
                        break
            if not message and "<html" not in text.lower():
                message = text[:MAX_ERROR_TEXT]
        if not message:
            message = f"图床接口返回 HTTP {status}。"

        retry_after = sanitize_text(headers.get("retry-after"), 32) or None
        return ImgApiError(
            sanitize_text(redact_secrets(message, self.token), MAX_ERROR_TEXT),
            status=status,
            retry_after=retry_after,
        )

    async def _send_with_redirects(
        self,
        url: str,
        params: dict[str, Any] | None,
        headers: dict[str, str],
    ) -> httpx.Response:
        """GET with manual, same-origin-only redirect handling."""
        current = url
        current_params = params
        for _ in range(MAX_REDIRECTS + 1):
            response = await self._client.get(
                current,
                params=current_params,
                headers=headers,
            )
            if not response.is_redirect:
                return response
            location = response.headers.get("location")
            if not location:
                return response
            # ``_check_url`` rejects cross-site redirects, so neither the
            # Authorization header nor a query token can be forwarded to a
            # third-party host.
            target = self._check_url(urljoin(str(response.url), location))
            current = self._with_token(target)
            current_params = None
        raise ImgApiError("图床返回了过多跳转，已停止请求。")

    async def _request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        accept: str = "application/json",
    ) -> httpx.Response:
        url = self._url(path)
        request_params = dict(params or {})
        if self.token and self.token_mode in ("query", "both"):
            request_params["token"] = self.token
        for attempt in range(self.retries + 1):
            try:
                response = await self._send_with_redirects(
                    url,
                    request_params or None,
                    self._headers(accept),
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt < self.retries:
                    await asyncio.sleep(0.4 * (attempt + 1))
                    continue
                raise ImgApiError("连接图床服务失败，请检查网络或图床地址。") from exc

            if response.status_code >= 500 and attempt < self.retries:
                await asyncio.sleep(0.4 * (attempt + 1))
                continue
            if 300 <= response.status_code < 400:
                raise ImgApiError("图床返回了无法跟随的跳转。")
            if response.status_code >= 400:
                raise self._error_from_body(
                    response.status_code,
                    response.headers,
                    response.content,
                )
            return response

        raise ImgApiError("图床服务暂时不可用。")

    async def _json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        response = await self._request(path, params)
        if len(response.content) > MAX_JSON_BYTES:
            raise ImgApiError("图床返回的数据过大，已停止解析。")
        try:
            data = response.json()
        except ValueError as exc:
            raise ImgApiError("图床返回了无法解析的数据。") from exc
        if not isinstance(data, (dict, list)):
            raise ImgApiError("图床返回了意外的数据格式。")
        return data

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def health(self) -> dict[str, Any]:
        data = await self._json("/health")
        return data if isinstance(data, dict) else {"ok": False}

    async def stats(self) -> dict[str, Any]:
        data = await self._json("/stats")
        if not isinstance(data, dict):
            raise ImgApiError("图库统计格式异常。")
        return data

    async def random_image(
        self,
        gallery: str = "v1",
        *,
        image_id: str | None = None,
        tag: str | None = None,
        tag_mode: str | None = None,
        image_type: str | None = None,
        exclude_tags: set[str] | None = None,
        max_attempts: int = 1,
    ) -> dict[str, Any]:
        """Return image metadata from ``/api/v1`` or ``/api/v2``.

        ``format=json`` is used for metadata. When ``exclude_tags`` is set,
        random picks containing those tags are discarded and retried; this is
        how avatar-tagged images stay out of the random pool. An explicit
        ``image_id`` is always returned as-is.
        """
        endpoint = "/api/v2" if gallery == "v2" else "/api/v1"
        if image_type == "origin" and not self.token:
            raise ImgApiError(
                "未配置 Token，无法请求原图；PNG 和 WebP 仍可使用。",
                status=400,
            )
        params: dict[str, Any] = {"format": "json"}
        if image_id:
            params["id"] = sanitize_image_id(image_id)
        if tag:
            params["tag"] = sanitize_tags(tag, max_tags=10)
        if tag_mode in ("any", "all"):
            params["tagMode"] = tag_mode
        if image_type in ("webp", "png", "origin"):
            params["type"] = image_type

        excluded = {
            sanitize_text(item, 32).lower()
            for item in (exclude_tags or set())
            if sanitize_text(item, 32)
        }
        if tag and excluded:
            requested = {
                sanitize_text(item, 32).lower()
                for item in tag.split(",")
                if sanitize_text(item, 32)
            }
            blocked = sorted(requested & excluded)
            if blocked:
                names = "、".join(f"#{item}" for item in blocked)
                raise ImgApiError(
                    f"标签 {names} 已被排除，请换一个标签或调整排除列表。",
                    status=400,
                )

        attempts = 1 if image_id or not excluded else max(1, min(int(max_attempts), 10))
        last_excluded: dict[str, Any] | None = None

        for _ in range(attempts):
            data = await self._json(endpoint, params)
            if not isinstance(data, dict) or not data.get("id"):
                raise ImgApiError("图床没有返回有效的图片数据。")

            result = self._sanitize_payload(dict(data))
            result.setdefault("gallery", gallery)
            if image_id or not excluded:
                return result

            raw_tags = result.get("tags") or []
            if isinstance(raw_tags, str):
                raw_tags = raw_tags.split(",")
            tags = {
                sanitize_text(item, 32).lower()
                for item in raw_tags
                if sanitize_text(item, 32)
            }
            if not (tags & excluded):
                return result
            last_excluded = result

        if last_excluded is not None:
            names = "、".join(f"#{item}" for item in sorted(excluded))
            raise ImgApiError(
                f"连续 {attempts} 次随机都命中了已排除标签（{names}），"
                "请稍后重试或调整排除列表。",
                status=404,
            )
        raise ImgApiError("图床没有返回有效的图片数据。")

    async def list_images(
        self,
        *,
        page: int = 1,
        page_size: int = 40,
        query: str | None = None,
        tag: str | None = None,
        tag_mode: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {
            "page": max(1, int(page)),
            "pageSize": max(1, min(int(page_size), 200)),
        }
        if query:
            params["q"] = sanitize_text(query, 100)
        if tag:
            params["tag"] = sanitize_tags(tag, max_tags=10)
        if tag_mode in ("any", "all"):
            params["tagMode"] = tag_mode
        return self._sanitize_payload(await self._json("/list", params))

    async def info(self, image_id: str) -> dict[str, Any]:
        image_id = sanitize_image_id(image_id)
        if not image_id:
            raise ValueError("请提供图片 ID。")
        data = await self._json(f"/info/{quote(image_id, safe='')}")
        if not isinstance(data, dict) or not data.get("id"):
            raise ImgApiError("没有找到这张图片。")
        return self._sanitize_payload(data)

    def avatar_url(
        self,
        *,
        user: str | None = None,
        image_type: str = "jpg",
        size: int = 512,
    ) -> str:
        if not self.base_url:
            raise ImgApiError(NOT_CONFIGURED_MESSAGE, status=400)
        params: dict[str, Any] = {
            "size": max(32, min(int(size), 1024)),
        }
        if user:
            params["user"] = sanitize_text(user, 64)
        if image_type == "png":
            params["type"] = "png"
        return self._with_token(f"{self.base_url}/api/avatar?{urlencode(params)}")

    async def fetch_bytes(self, url: str) -> tuple[bytes, str]:
        """Download an image with a hard size limit and a token mode-aware request."""
        url = self._with_token(self._check_url(url))
        response: httpx.Response | None = None
        try:
            for _ in range(MAX_REDIRECTS + 1):
                request = self._client.build_request(
                    "GET",
                    url,
                    headers=self._headers("image/*"),
                )
                response = await self._client.send(request, stream=True)
                if not response.is_redirect:
                    break
                location = response.headers.get("location")
                if not location:
                    break
                # Same-origin only: tokens never follow a redirect off-site.
                target = self._check_url(urljoin(str(response.url), location))
                await response.aclose()
                response = None
                url = self._with_token(target)

            if response is None:
                raise ImgApiError("图床返回了过多跳转，已停止请求。")
            if response.is_redirect:
                raise ImgApiError("图床返回了无法跟随的跳转。")

            if response.status_code >= 400:
                body = await response.aread()
                raise self._error_from_body(
                    response.status_code,
                    response.headers,
                    body,
                )

            content_length = response.headers.get("content-length", "")
            if content_length.isdigit() and int(content_length) > self.max_bytes:
                raise ImgApiError(
                    f"图片超过 {self.max_bytes / 1024 / 1024:.1f} MB 的限制。"
                )

            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > self.max_bytes:
                    raise ImgApiError(
                        f"图片超过 {self.max_bytes / 1024 / 1024:.1f} MB 的限制。"
                    )
                chunks.append(chunk)

            content_type = sanitize_text(
                response.headers.get("content-type", "").split(";")[0],
                50,
            ).lower()
            data = b"".join(chunks)
            if not data:
                raise ImgApiError("图床返回了空图片。")
            if content_type.startswith(("text/", "application/json", "image/svg")):
                raise ImgApiError("图床返回了非图片内容，请检查 Token 是否有效。")
            head = data[:256].lstrip().lower()
            if head.startswith((b"<!doctype", b"<html", b"<?xml", b"<svg", b"{", b"[")):
                raise ImgApiError("图床返回了非图片内容，请检查 Token 是否有效。")
            if not re.fullmatch(r"image/[a-z0-9.+-]{1,30}", content_type):
                content_type = guess_image_mime(data)
            return data, content_type
        finally:
            if response is not None:
                await response.aclose()

    async def fetch_image_bytes(
        self,
        image: dict[str, Any],
        *,
        prefer_origin: bool = False,
    ) -> tuple[bytes, str]:
        """Download an image referenced by metadata, honouring PNG/WebP/origin.

        ``type=png`` returns a PNG URL in ``url`` while ``webp`` still points at
        the WebP variant, so the URL order must follow ``variant``. Preferring
        ``webp`` unconditionally would silently turn PNG downloads into WebP.
        """
        if prefer_origin and not self.token:
            raise ImgApiError("未配置 Token，无法获取原图。", status=400)
        variant = str(image.get("variant") or "").strip().lower()
        if prefer_origin:
            keys = ["origin", "url", "webp"]
        elif variant == "png":
            keys = ["url", "webp", "origin"]
        else:
            keys = ["webp", "url", "origin"]

        candidates: list[str] = []
        for key in keys:
            value = image.get(key)
            if (
                isinstance(value, str)
                and value.startswith(("http://", "https://"))
                and value not in candidates
            ):
                candidates.append(value)

        if not candidates and image.get("id"):
            gallery = image.get("gallery", "v1")
            api = "/api/v2" if gallery == "v2" else "/api/v1"
            candidates.append(
                f"{self.base_url}{api}?id={quote(str(image['id']))}&type=webp"
            )

        if not candidates:
            raise ImgApiError("图片地址为空，无法下载。")

        last_error: ImgApiError | None = None
        for url in candidates:
            try:
                return await self.fetch_bytes(url)
            except ImgApiError as exc:
                last_error = exc
                continue
        raise last_error or ImgApiError("图片下载失败。")

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._client.aclose()
