# 星屿图集 · AstrBot 插件（astrbot_plugin_img_api）

**星屿图集**（插件目录 `astrbot_plugin_img_api`）把兼容 img-api 的图床接入 AstrBot：在聊天里发随机图，在 WebUI 里浏览图库、查看统计、管理连接设置。

> 这是一个社区插件，与图床站点没有隶属关系。请遵守站点的使用规则与速率限制。
>
> 仓库：[Shou-Lu/imager-tool](https://github.com/Shou-Lu/imager-tool)

## 接口文档与地址

本插件**不内置默认 API 地址**，`base_url` 默认留空；未填写时插件不会发起任何图床请求。

- 我提供的接口文档：https://imgapi.bixygames.com/docs
- 可以直接填写我提供的 API：`https://imgapi.bixygames.com`（站点根地址或完整 `/api/v1` 地址都可以）
- 也可以填写兼容 img-api 的自建实例地址
- 安装后在 AstrBot 插件配置或 WebUI「设置 → 连接与认证」中填写 `base_url` 即可

```text
https://imgapi.bixygames.com
# 或
https://imgapi.bixygames.com/api/v1
```

## 功能

- **随机图**：支持 V1 / V2 图库、单标签/多标签、`any`/`all` 匹配、WebP / PNG / 原图输出（PNG 与原图需 Token）。
- **AI 工具**：内置随机图、图库搜索、图库统计、图片详情、用户头像 5 个 LLM function-calling 工具，可由模型自动调用。
- **平台适配**：支持 aiocqhttp、QQ 官方、Telegram、微信、飞书、Discord、Slack、KOOK、Satori 等主流图片平台。
- **头像排除**：随机图默认跳过 `#avatar` 标签的图片，避免把头像图当作随机图调用。
- **图库浏览**：分页列表、搜索、标签筛选、图片详情与统计。
- **WebUI 面板**：仿梦星小图床后台的侧栏面板与深色配色，支持亮/暗主题切换、随机图预览、图库网格、一键复制链接/Markdown/ID、原图查看与下载、连接测试、在线修改配置。
- **Token 传递方式可切换**：默认按正式形式 `/api/v1?token=xxx` 调用，也可改用 `Authorization` 请求头；返回聊天/WebUI 的 URL 会自动去掉 token。
- **友好错误**：对 401 / 403 / 404 / 429 / 5xx 和网络超时给出中文提示。
- **限流保护**：随机图指令带可配置冷却时间，统计/健康检查带短缓存。
- **主题适配**：WebUI 跟随 AstrBot 的亮色 / 暗色主题。

> 图床上传、审核、V2 提交等操作目前没有公开 API，仍请在站点后台完成；本插件聚焦浏览、随机图、详情、统计与头像。

## 环境要求

| 项目 | 要求 |
| --- | --- |
| AstrBot | `>= 4.26.0, < 5`（需要插件可视化视图与 `astrbot.api.web`） |
| Python | `>= 3.10` |
| 依赖 | `httpx`（见 `requirements.txt`，AstrBot 通常已自带） |

> AstrBot 4.26.0–4.28.x 只扫描插件的 `pages/` 目录，4.29+ 同时支持 `views/` 与 `pages/`。本插件使用 `pages/gallery`，因此在 4.26+ 的所有版本都能加载视图。

## 安装

1. 将整个 `astrbot_plugin_img_api` 目录放到 AstrBot 的 `data/plugins/` 下，或直接克隆仓库：

   ```bash
   git clone https://github.com/Shou-Lu/imager-tool.git data/plugins/astrbot_plugin_img_api
   ```

2. 安装依赖（如果 AstrBot 未自动安装）：

   ```bash
   pip install -r requirements.txt
   ```

3. 在 AstrBot WebUI 的「插件」页重载插件。
4. 打开插件配置，填写 `base_url`（默认留空，必填）；需要 PNG/原图/非公开列表时再填写 `token`。
5. 在插件卡片中进入「图库面板」即可使用 WebUI。

> 通过 Dashboard 上传 ZIP 时，`metadata.yaml` 必须位于压缩包根目录，不能多套一层 `astrbot_plugin_img_api/` 文件夹，也不能用反斜杠路径。本仓库源码已经是扁平结构，直接打包根目录内容即可；`metadata.yaml` 必须位于 ZIP 根目录。

使用我提供的站点时，Token 创建入口：`https://imgapi.bixygames.com/admin/profile` → 「API Token」→「创建 Token」。
自建实例请使用 `{base_url}/admin/profile`。
Token 只在创建弹窗显示一次，请立即保存。

## 指令

| 指令 | 别名 | 说明 |
| --- | --- | --- |
| `/随机图` | `/来张图`、`/img`、`/pic`、`/randimg` | 随机获取一张图片 |
| `/图库列表` | `/图库`、`/imglib` | 分页浏览图库 |
| `/图库搜索 <关键词>` | `/搜索图片`、`/imgsearch` | 按标题、来源或标签搜索图库 |
| `/图库信息 <ID>` | `/图片信息` | 查看单张图片详情 |
| `/图库统计` | `/图库状态` | 查看图片总数、大小、标签与站点状态 |
| `/图库标签` | `/图库标签列表` | 列出图库标签 |
| `/图库头像 [用户名] [png] [尺寸]` | `/图片头像` | 获取用户头像；不填用户名时使用 Token 用户/站长头像 |
| `/图库帮助` | `/图库菜单`、`/imghelp`、`/图库指令` | 查看帮助 |

## AI 工具

插件注册了 5 个 LLM function-calling 工具，模型可在对话中自动调用：

| 工具 | 用途 |
| --- | --- |
| `imgapi_random_image` | 随机获取并发送一张图片，支持标签、V1/V2、WebP/PNG/原图 |
| `imgapi_search_gallery` | 按关键词或标签搜索图库，返回图片列表 |
| `imgapi_gallery_stats` | 获取图片数量、大小、标签和站点状态 |
| `imgapi_image_info` | 根据图片 ID 返回详情 |
| `imgapi_user_avatar` | 获取并发送用户头像 |

工具与 `/随机图`、`/图库搜索`、`/图库统计`、`/图库信息`、`/图库头像` 共用同一套配置、Token、排除标签和错误处理。

## 支持平台与标签

- **平台**：`aiocqhttp`、`qq_official`、`qq_official_webhook`、`telegram`、`wecom`、`wecom_ai_bot`、`lark`、`dingtalk`、`discord`、`slack`、`kook`、`vocechat`、`weixin_official_account`、`weixin_oc`、`satori`、`misskey`、`line`、`matrix`、`mattermost`
- **标签**：`图片`、`随机图`、`图库`、`图床`、`img-api`、`壁纸`

### 随机图参数

```text
/随机图 [标签] [v1|v2] [webp|png|origin] [any|all] [id:图片ID]
```

- 标签：`dragon`、`dragon,fish`；也可以用 `tag:dragon` 明确指定标签。
- 图库：`v1` = 全部已审核图片；`v2` = 只包含已加入 V2 图库的图片。
- 格式：`webp`（默认，无 Token 可用）、`png`（需要 Token）、`origin`（原图，需要 Token）。
- 匹配：`any`（默认，任意标签）或 `all`（同时包含全部标签）。
- 指定图片：`id:6deaf10d7044a377e4a2f91a` 或 `#6deaf10d7044a377e4a2f91a`；指定 ID 会按 ID 直接返回，不受排除列表影响。
- `#avatar` 默认被排除，直接请求 `avatar` 标签会得到提示；可在 `exclude_tags` 中调整。

示例：

```text
/随机图
/随机图 dragon
/随机图 dragon,fish all v2
/随机图 v2 png
/随机图 id:6deaf10d7044a377e4a2f91a origin
/图库列表 2 wolf
/图库信息 6deaf10d7044a377e4a2f91a
/图库头像 alice png 256
```

### 发送方式

- `send_mode = bytes`（默认）：插件带 Token 下载图片，再以图片消息发送。兼容性最好，聊天消息里不会出现 Token。
- `send_mode = url`：直接把图片 URL 交给平台。此模式不携带 Token，无法发送原图；请使用 `bytes` 模式。

## 配置

配置既可以在 AstrBot 的插件配置面板修改，也可以在「图库面板 → 设置」中修改；两者读写的是同一份配置
（`data/config/astrbot_plugin_img_api_config.json`）。

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `base_url` | 空（必填） | 图床 API 地址；不内置默认值，留空时不会发起请求。可填站点根或完整 `/api/v1` 地址 |
| `token` | 空 | API Token；原图、非公开列表、更高限流需要 |
| `token_mode` | `query` | Token 传递方式：`query`（`?token=`）/ `header` / `both` |
| `default_gallery` | `v1` | 默认图库：`v1` / `v2` |
| `default_type` | `webp` | 默认输出：`webp` / `png` / `origin`（PNG/原图需 Token） |
| `tag_mode` | `any` | 多标签匹配：`any` / `all` |
| `send_mode` | `bytes` | 聊天发送方式：`bytes` / `url` |
| `allow_origin` | `true` | 关闭后原图不可用 |
| `show_meta` | `true` | 在图片消息前附带图库、尺寸、标签、ID |
| `exclude_tags` | `["avatar"]` | 随机图排除的标签；默认跳过 `#avatar` |
| `exclude_retries` | `5` | 命中排除标签后的重抽次数，1–10 |
| `request_timeout` | `20` | 请求超时（秒），3–120 |
| `retry_times` | `1` | 网络错误 / 5xx 重试次数，0–3 |
| `max_image_mb` | `12` | 单张图片下载上限（MB），1–64 |
| `cooldown_seconds` | `3` | 同一会话同一用户的随机图冷却，0 表示关闭 |
| `cache_ttl` | `60` | 统计/健康检查缓存（秒），0 表示不缓存 |
| `command_page_size` | `8` | `/图库列表` 每页条数，1–20 |
| `webui_page_size` | `24` | WebUI 图库每页条数，6–60 |
| `max_tags` | `5` | 单次请求最多保留的标签数量，1–10 |

## WebUI 面板

插件详情页 →「图库面板」，采用仿站点后台的侧栏布局，包含四个标签页：

- **浏览**：V1/V2 切换、标签快捷选择、匹配方式、输出格式、指定 ID、随机图预览；支持复制链接/Markdown/ID、查看原图、下载。
- **图库**：搜索标题/来源/标签、标签筛选、分页网格；点击图片查看完整元数据。
- **设置**：站点地址、Token（留空保持原值，可勾选清除）、Token 传递方式、默认图库/格式/匹配方式、发送方式、随机图排除标签、超时、重试、大小上限、冷却、缓存、分页大小。
- **关于**：版本、站点地址、复制 API 文档与 Token 管理地址。

> 「图库」页在站点未公开列表时需要 Token；没有 Token 时会显示 401 提示和 Token 管理地址。

## Token、限流与原图

- Token 支持 `Authorization: Bearer <token>`、`X-API-Token` 或 `?token=`；本插件默认使用 **`?token=`**，即 `GET /api/v1?token=xxx`。
- 可在 `token_mode` 中切换为 `header`（只发 `Authorization`）或 `both`；站点支持请求头时建议用 `header`，降低 URL 被日志/代理记录的风险。
- 未认证时只能获取 WebP；PNG 和原图都需要 Token。插件在未配置 Token 时会直接拒绝 `png`/`origin` 请求（400），不调用上游，也不会静默降级或返回 404。
- 默认额度：游客 **200 次/分钟**（按 IP），注册用户 **2000 次/分钟**（按账户）。
- 超过额度返回 `429`；同一 Token 10 分钟内多次触发限流可能被临时封禁（默认 12 小时），封禁期间返回 `403`。
- 插件收到 429 时会读取 `Retry-After` 并提示等待时间；不会自动重试 429。

## API 调用形式

`base_url` 默认留空。填写后随机图使用正式的 `/api/v1` 路径；下面以我提供的站点为例：

```text
GET https://imgapi.bixygames.com/api/v1?token=xxx
GET https://imgapi.bixygames.com/api/v1?format=json&token=xxx&tag=dragon
GET https://imgapi.bixygames.com/api/v1?id=<IMAGE_ID>&type=png&token=xxx
GET https://imgapi.bixygames.com/api/v1?id=<IMAGE_ID>&type=webp&token=xxx
GET https://imgapi.bixygames.com/api/v1?id=<IMAGE_ID>&type=origin&token=xxx
```

- 未配置 `base_url` 时，插件不会发起任何请求；聊天指令和 WebUI 会提示先到设置中填写 API 地址。
- `type=png` 与 `type=origin` 都必须携带有效 Token；未配置时插件直接返回 400，不调用上游。
- 在 WebUI 中选择「原图」下载但未配置 Token 时，会返回明确提示，不再静默下载 WebP。

- `base_url` 既可以填站点根 `https://imgapi.bixygames.com`，也可以填完整的 `https://imgapi.bixygames.com/api/v1`，插件会归一化，不会重复拼接路径。
- V2 图库是显式选项，选择 `v2` 时才会调用 `/api/v2`；默认始终走 `/api/v1`。
- Token 只用于服务端请求；插件在把数据返回给聊天/WebUI 前，会从所有 URL 中移除 `token` 参数，复制出来的链接不含 Token。

### 鉴权错误不会踢出登录页

图床的 `401 / 403 / 404 / 429` 不会原样返回给 Dashboard。AstrBot 前端的 axios 拦截器会把同源 `/api/*` 的 `401` 当成 Dashboard 会话过期并跳转 `/auth/login`，所以插件把所有上游图床错误统一映射为 `400`，在 WebUI 面板内显示提示：

- 图床 Token 缺失/无效：提示检查插件 Token，并明确说明不是 AstrBot 登录失效。
- 列表未公开：提示填写 Token，不会退出登录。
- 无匹配图片 / 限流 / 上游 5xx：同样显示文字提示，不触发页面跳转。

只有 AstrBot 自身会话真的过期时，Dashboard 才会正常跳转登录页。

## 安全说明

- Token 存放在插件配置中，`_conf_schema.json` 已将其标记为 `secret`；WebUI 只回显 `前4位******后4位`。
- 所有图片下载只允许访问 `base_url` 对应的主机，避免被上游 JSON 引导到任意地址。
- WebUI 后端只通过 AstrBot Bridge 暴露，不直接使用 Dashboard Cookie；设置写入依赖 Dashboard 登录态。
- 默认使用 `?token=` 调用；插件返回聊天/WebUI 的 URL 会自动移除 token，复制出来的链接不含 Token。
- URL 参数可能被上游、代理或调试日志记录；如果站点支持请求头，建议设置 `token_mode=header`。
- 如果 Token 泄漏：到图床个人中心吊销并重新创建，然后更新插件配置。

## 开发

```bash
ruff check .
ruff format --check .
```

目录结构：

```text
astrbot_plugin_img_api/
├── main.py                     # 插件入口：指令 + Web API
├── metadata.yaml               # 插件元数据
├── _conf_schema.json           # 可视化配置
├── requirements.txt
├── logo.png
├── pyproject.toml              # ruff 配置
├── README.md
├── LICENSE                     # AGPL-3.0
├── .gitignore
├── bixyimg/
│   ├── client.py               # img-api 异步客户端
│   └── util.py                 # 参数解析、格式化、错误提示
├── .astrbot-plugin/i18n/       # 中英文文案
│   ├── zh-CN.json
│   └── en-US.json
└── pages/gallery/              # 插件可视化视图（AstrBot 4.26+ 均支持）
    ├── index.html
    ├── app.js
    └── style.css
```

## 常见问题

**安装后提示“尚未配置图床 API 地址”？**
插件不内置默认接口。到插件配置或 WebUI「设置 → 连接与认证」填写 `base_url`，例如我提供的 `https://imgapi.bixygames.com`；接口文档：https://imgapi.bixygames.com/docs。填写前插件不会发起任何图床请求。

**为什么随机图不会出现 `#avatar`？**
随机图默认排除 `exclude_tags` 中的标签（默认 `avatar`），连续抽到排除标签会重新抽取；全部命中会返回友好提示。可在配置或 WebUI 设置里修改/清空排除列表。

**URL 里为什么带 `token=`？**
默认 `token_mode=query`，按站点正式形式 `/api/v1?token=xxx` 调用。返回给聊天/WebUI 的链接会自动去掉 token；如果不想让 token 出现在请求 URL，把 `token_mode` 改成 `header`。

**想把随机图链接给 HMCL 等外部客户端？**
直接使用你填写的 `base_url`，例如 `https://imgapi.bixygames.com/api/v1?token=xxx`（或 `https://imgapi.bixygames.com/api/v1?type=png&token=xxx`）。这类客户端无法设置请求头，只能把 Token 放在 URL 中，请妥善保管链接。

**`/图库列表` 提示 Token / 401？**
站点默认不公开列表。到图床个人中心创建 Token 并填入插件配置即可。插件会把上游 401 映射为面板内提示，不会把你踢到 AstrBot 登录页。

**`/随机图 v2` 没有图片？**
V2 图库是独立列表，只有被加入 V2 的图片才会出现；可以先用 `/随机图` 浏览 V1，或在站点 `/admin/v2` 提交。

**为什么原图 / PNG 不可用或报错？**
未配置有效 Token，或原图被 `allow_origin` 关闭。当前线上行为是：无 Token 时 `type=png` 会被降级/重定向为 WebP，`type=origin` 也无法获取。插件因此把 PNG 和原图都设为 Token 依赖；未配置时直接拒绝（400），不调用上游，也不会静默返回 WebP。配置 Token 后即可使用 `type=png` 和 `type=origin`。

**WebUI 里为什么选不了「PNG」或「原图」？**
未配置 Token 时，插件会禁用 PNG/原图格式和「查看原图」按钮，并在设置中提示先填写 Token，避免出现上游 404 或面板异常。

**`type=png` 需要 Token 吗？**
需要。当前线上 `type=png` 在无 Token 时会降级/重定向为 WebP，所以插件把 PNG 和原图都设为 Token 依赖；配置 Token 后会通过 `type=png` 获取真正的 PNG。

**返回 403 / 429？**
403 通常是 Token 因频繁触发限流被临时封禁；429 是当前请求过快。按提示等待，或使用 Token 提高额度。

**WebUI 面板没有出现？**
确认 AstrBot 版本 `>= 4.26.0`，插件已启用，`pages/gallery/index.html` 存在；然后重载插件并刷新插件详情页。

**WebUI 图片加载失败？**
检查浏览器/服务器能否访问图床域名；如果使用自建实例，确认 HTTPS 证书有效、`base_url` 正确。

**修改设置后聊天指令没变化？**
WebUI 保存会立即重建客户端；如果修改的是 AstrBot 插件配置面板，需要点击保存并重载插件。

## 许可

本项目使用 **GNU Affero General Public License v3.0（AGPL-3.0）**。详见 [LICENSE](LICENSE)。

如果你通过网络向用户提供本插件的修改版本，请按 AGPL-3.0 的要求提供对应源码。
