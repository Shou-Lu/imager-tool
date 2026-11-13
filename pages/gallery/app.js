/* 星屿图集 · AstrBot 插件可视化视图
 * 通过 window.AstrBotPluginView bridge 调用插件后端，不直接访问 Dashboard 凭证。
 */

const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;

const state = {
  config: null,
  overview: null,
  tags: [],
  current: null,
  library: {
    page: 1,
    pageSize: 24,
    total: 0,
    loaded: false,
    lastQuery: "",
    lastTag: "",
  },
  settingsDirty: false,
  randomSeq: 0,
  librarySeq: 0,
  toastTimer: null,
  activeTab: "browse",
};

let localTheme = null;

const PAGE_META = {
  browse: [
    "views.gallery.page.browse.title",
    "浏览",
    "views.gallery.page.browse.subtitle",
    "从图库随机抽取一张，支持标签、V2 与格式切换。",
  ],
  library: [
    "views.gallery.page.library.title",
    "图库",
    "views.gallery.page.library.subtitle",
    "分页查看站点已审核图片，支持搜索与标签筛选。",
  ],
  settings: [
    "views.gallery.page.settings.title",
    "设置",
    "views.gallery.page.settings.subtitle",
    "管理图床地址、Token、默认格式与请求限制。",
  ],
  about: [
    "views.gallery.page.about.title",
    "关于",
    "views.gallery.page.about.subtitle",
    "版本、接口与安全说明。",
  ],
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

/* ------------------------------------------------------------------ */
/* i18n                                                                */
/* ------------------------------------------------------------------ */
function t(key, fallback) {
  try {
    const value = bridge?.t?.(key, fallback);
    return value ?? fallback;
  } catch {
    return fallback;
  }
}

function applyI18n() {
  $$("[data-i18n]").forEach((element) => {
    if (!element.dataset.i18nFallback) {
      element.dataset.i18nFallback = element.textContent.trim();
    }
    element.textContent = t(element.dataset.i18n, element.dataset.i18nFallback);
  });

  $$("[data-i18n-placeholder]").forEach((element) => {
    if (!element.dataset.i18nPlaceholderFallback) {
      element.dataset.i18nPlaceholderFallback = element.placeholder || "";
    }
    element.placeholder = t(
      element.dataset.i18nPlaceholder,
      element.dataset.i18nPlaceholderFallback,
    );
  });

  document.title = t("views.gallery.heading", "星屿图集");
}

/* ------------------------------------------------------------------ */
/* Small helpers                                                       */
/* ------------------------------------------------------------------ */
function showToast(message, isError = false) {
  const toast = $("#toast");
  if (!toast) return;
  toast.textContent = message;
  toast.classList.toggle("error", Boolean(isError));
  toast.hidden = false;
  window.clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => {
    toast.hidden = true;
  }, 3200);
}

function setText(selector, value) {
  const element = $(selector);
  if (element) element.textContent = value;
}

function isPrivateHost(host) {
  const name = String(host || "")
    .toLowerCase()
    .replace(/^\[|\]$/g, "");
  if (!name) return true;
  if (
    name === "localhost" ||
    name.endsWith(".localhost") ||
    name.endsWith(".local") ||
    name.endsWith(".internal")
  ) {
    return true;
  }
  if (/^\d+\.\d+\.\d+\.\d+$/.test(name)) {
    const parts = name.split(".").map(Number);
    return (
      parts[0] === 0 ||
      parts[0] === 10 ||
      parts[0] === 127 ||
      (parts[0] === 169 && parts[1] === 254) ||
      (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31) ||
      (parts[0] === 192 && parts[1] === 168)
    );
  }
  if (name === "::1" || name === "::") return true;
  if (/^(fc|fd|fe8|fe9|fea|feb)/.test(name)) return true;
  return false;
}

function safeMediaUrl(value) {
  if (typeof value !== "string" || !value.trim()) return "";
  try {
    const url = new URL(value, window.location.href);
    if (url.protocol !== "http:" && url.protocol !== "https:") return "";
    if (url.username || url.password) return "";
    url.searchParams.delete("token");
    const base = state.config?.base_url;
    if (base) {
      const baseUrl = new URL(base, window.location.href);
      if (baseUrl.protocol === "https:" && url.protocol !== "https:") return "";
      if (url.hostname !== baseUrl.hostname && isPrivateHost(url.hostname)) {
        return "";
      }
    }
    return url.toString();
  } catch {
    return "";
  }
}

function setImageSource(img, value) {
  const safe = safeMediaUrl(value);
  if (!safe) {
    img.removeAttribute("src");
    img.style.display = "none";
    return false;
  }
  img.referrerPolicy = "no-referrer";
  img.style.display = "";
  img.src = safe;
  return true;
}

function updateSecurityHint(config) {
  const hint = $("#security-hint");
  if (!hint) return;
  const base = String(config?.base_url || "");
  const messages = [];
  if (base.startsWith("http://")) {
    messages.push(
      t(
        "views.gallery.settings.securityHttp",
        "当前使用明文 HTTP，Token 与图片内容可能被中间人窃听；建议改用 HTTPS 地址。",
      ),
    );
  }
  if (base && (config?.token_mode || "query") !== "header") {
    messages.push(
      t(
        "views.gallery.settings.securityToken",
        "Token 通过 URL 参数传递，可能被日志或代理记录；站点支持时建议改用 Authorization 请求头。",
      ),
    );
  }
  hint.textContent = messages.join(" ");
  hint.hidden = messages.length === 0;
}

function applyTheme(theme) {
  const value = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = value;
  const button = $("#theme-toggle");
  if (button) {
    const nextLight = value === "dark";
    button.textContent = nextLight ? "☀" : "☾";
    button.title = nextLight
      ? t("views.gallery.theme.toLight", "切换到亮色")
      : t("views.gallery.theme.toDark", "切换到暗色");
    button.setAttribute("aria-label", button.title);
  }
}

function updatePageHeading(tab = "browse") {
  const meta = PAGE_META[tab] || PAGE_META.browse;
  setText("#page-title", t(meta[0], meta[1]));
  setText("#page-subtitle", t(meta[2], meta[3]));
}

function isConfigured() {
  return Boolean(state.config?.base_url);
}

function docsUrl() {
  return (
    state.config?.docs_url || "https://imgapi.bixygames.com/docs"
  );
}

function updateDocsLink() {
  const raw = docsUrl();
  const url = safeMediaUrl(raw);
  const link = $("#docs-link");
  if (!link) return;
  if (!url) {
    link.hidden = true;
    return;
  }
  link.hidden = false;
  link.href = url;
  link.textContent = raw;
}

function openDocs() {
  const url = safeMediaUrl(docsUrl());
  if (!url) return;
  let opened = null;
  try {
    opened = window.open(url, "_blank", "noopener,noreferrer");
  } catch {
    opened = null;
  }
  if (opened) {
    try {
      opened.opener = null;
    } catch {
      /* ignore */
    }
    return;
  }
  copyText(url, t("views.gallery.settings.docsLabel", "接口文档"));
  showToast(
    t(
      "views.gallery.settings.docsBlocked",
      "已复制文档地址；如果浏览器拦截了新窗口，请粘贴访问。",
    ),
  );
}

function closeAllSelects(except) {
  $$(".select-wrap.open").forEach((wrap) => {
    if (wrap !== except) wrap._customClose?.();
  });
}

function refreshCustomSelects() {
  $$(".select-wrap").forEach((wrap) => wrap._customSync?.());
}

function enhanceSelect(select) {
  if (select.dataset.customized === "1") return;
  select.dataset.customized = "1";

  const wrap = document.createElement("div");
  wrap.className = "select-wrap";
  select.parentNode.insertBefore(wrap, select);
  wrap.appendChild(select);
  select.classList.add("native-select");
  select.tabIndex = -1;
  select.setAttribute("aria-hidden", "true");

  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "select-trigger";
  trigger.id = `${select.id}-trigger`;
  trigger.setAttribute("aria-haspopup", "listbox");
  trigger.setAttribute("aria-expanded", "false");
  const label = document.querySelector(`label[for="${select.id}"]`);
  if (label) label.htmlFor = trigger.id;
  const value = document.createElement("span");
  value.className = "select-value";
  const chevron = document.createElement("span");
  chevron.className = "select-chevron";
  chevron.textContent = "⌄";
  trigger.append(value, chevron);

  const menu = document.createElement("div");
  menu.className = "select-menu";
  menu.hidden = true;
  menu.setAttribute("role", "listbox");
  wrap.append(trigger, menu);

  const close = () => {
    wrap.classList.remove("open");
    menu.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
  };

  const sync = () => {
    const selected = select.options[select.selectedIndex];
    value.textContent = selected ? selected.textContent.trim() : "";
    trigger.disabled = select.disabled;
    menu.replaceChildren();
    Array.from(select.options).forEach((option, index) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "select-option";
      item.textContent = option.textContent.trim();
      item.disabled = option.disabled;
      item.dataset.value = option.value;
      if (index === select.selectedIndex) item.classList.add("selected");
      item.addEventListener("click", () => {
        if (option.disabled) return;
        select.value = option.value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
        sync();
        close();
      });
      menu.appendChild(item);
    });
  };

  const open = () => {
    closeAllSelects(wrap);
    wrap.classList.add("open");
    menu.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
  };

  trigger.addEventListener("click", () => {
    if (trigger.disabled) return;
    if (menu.hidden) open();
    else close();
  });
  select.addEventListener("change", sync);

  wrap._customSync = sync;
  wrap._customClose = close;
  sync();
}

function enhanceSelects() {
  $$("select").forEach(enhanceSelect);
}

function updateSetupNotice() {
  const notice = $("#setup-notice");
  if (!notice) return;
  const configured = isConfigured();
  notice.hidden = configured;

  const randomBtn = $("#random-btn");
  if (randomBtn) randomBtn.disabled = !configured;

  if (!configured) {
    state.current = null;
    const empty = $("#preview-empty");
    const text = $("#preview-empty-text");
    const img = $("#preview-img");
    if (empty) empty.hidden = false;
    if (text) {
      text.textContent = t(
        "views.gallery.setup.required",
        "请先在设置中填写图床 API 地址。",
      );
    }
    if (img) img.hidden = true;
    setText("#preview-format", "");
  } else if (!state.current) {
    const text = $("#preview-empty-text");
    if (text) {
      text.textContent = t(
        "views.gallery.preview.empty",
        "点击「换一张」加载图片",
      );
    }
  }
}

function formatBytes(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = number;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return index === 0 ? `${Math.round(size)} ${units[index]}` : `${size.toFixed(2)} ${units[index]}`;
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 19);
  try {
    return date.toLocaleString(bridge?.getLocale?.() || undefined, {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return date.toLocaleString();
  }
}

function extensionOf(contentType) {
  const map = {
    "image/webp": "webp",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/avif": "avif",
  };
  return map[contentType] || "img";
}

function createElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = text;
  return element;
}

/* ------------------------------------------------------------------ */
/* Modal / clipboard                                                   */
/* ------------------------------------------------------------------ */
function openModal(title, body) {
  const modal = $("#modal");
  $("#modal-title").textContent = title;
  const container = $("#modal-body");
  container.replaceChildren();
  if (body) container.appendChild(body);
  modal.hidden = false;
}

function closeModal() {
  const modal = $("#modal");
  if (modal) modal.hidden = true;
  const body = $("#modal-body");
  if (body) body.replaceChildren();
}

function openCopyModal(text, title) {
  const wrapper = document.createDocumentFragment();
  const note = createElement(
    "p",
    "hint",
    t("views.gallery.copy.manual", "无法直接访问剪贴板，请按 Ctrl/⌘ + C 复制下面的内容。"),
  );
  const area = createElement("textarea");
  area.readOnly = true;
  area.value = text;
  const action = createElement(
    "button",
    "btn primary",
    t("views.gallery.copy.tryAgain", "尝试复制"),
  );
  action.type = "button";
  action.style.marginTop = "10px";
  action.addEventListener("click", () => {
    area.focus();
    area.select();
    let copied = false;
    try {
      copied = document.execCommand("copy");
    } catch {
      copied = false;
    }
    if (copied) {
      closeModal();
      showToast(t("views.gallery.action.copied", "已复制"));
    } else {
      showToast(t("views.gallery.action.copiedManual", "请手动选中并复制"), true);
    }
  });

  wrapper.append(note, area, action);
  openModal(title || t("views.gallery.copy.title", "复制内容"), wrapper);
  window.setTimeout(() => {
    area.focus();
    area.select();
  }, 80);
}

async function copyText(text, label) {
  const value = String(text || "");
  if (!value) return;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      showToast(t("views.gallery.action.copied", "已复制"));
      return;
    }
  } catch {
    /* fall through to the manual dialog */
  }
  openCopyModal(value, label);
}

/* ------------------------------------------------------------------ */
/* Bridge helpers                                                      */
/* ------------------------------------------------------------------ */
async function apiGet(endpoint, params) {
  if (!bridge?.apiGet) throw new Error(t("views.gallery.error.bridge", "Bridge 不可用"));
  return bridge.apiGet(endpoint, params || {});
}

async function apiPost(endpoint, body) {
  if (!bridge?.apiPost) throw new Error(t("views.gallery.error.bridge", "Bridge 不可用"));
  return bridge.apiPost(endpoint, body || {});
}

/* ------------------------------------------------------------------ */
/* Overview / stats                                                    */
/* ------------------------------------------------------------------ */
function updateHealth(health, error) {
  const badge = $("#health-badge");
  if (!badge) return;
  badge.classList.remove("ok", "bad");
  const text = badge.querySelector("span:last-child");

  if (!isConfigured()) {
    if (text) {
      text.textContent = t("views.gallery.health.notConfigured", "未配置");
    }
    badge.title = t(
      "views.gallery.setup.required",
      "请先在设置中填写图床 API 地址。",
    );
    return;
  }

  if (health && health.ok) {
    badge.classList.add("ok");
    if (text) text.textContent = t("views.gallery.health.online", "在线");
    badge.title = health.count !== undefined ? `count: ${health.count}` : "";
  } else {
    badge.classList.add("bad");
    if (text) text.textContent = t("views.gallery.health.offline", "离线");
    badge.title = error || t("views.gallery.health.offline", "离线");
  }
}

function renderStats(site) {
  setText("#stat-count", site?.count ?? "-");
  setText("#stat-size", site?.totalSize ? formatBytes(site.totalSize) : "-");
  setText(
    "#stat-size-sub",
    site?.webpSize ? `WebP ${formatBytes(site.webpSize)}` : "",
  );
  setText("#stat-tags", Array.isArray(site?.tags) ? site.tags.length : "-");
  setText("#stat-newest", site?.newest ? formatDate(site.newest) : "-");
}

function renderTags() {
  const container = $("#tag-chips");
  if (!container) return;
  container.replaceChildren();

  const excluded = new Set(
    excludedTagsOf(state.config).map((tag) => tag.toLowerCase()),
  );
  const tags = state.tags
    .filter((tag) => !excluded.has(String(tag).toLowerCase()))
    .slice(0, 24);
  if (!tags.length) {
    container.appendChild(
      createElement("span", "subtle", t("views.gallery.tags.empty", "暂无标签")),
    );
    renderExcludeHint();
    return;
  }

  const input = $("#tag-input");
  const active = new Set(
    (input?.value || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
  );

  tags.forEach((tag) => {
    const chip = createElement("button", "chip", `#${tag}`);
    chip.type = "button";
    chip.title = tag;
    if (active.has(tag)) chip.classList.add("active");
    chip.addEventListener("click", () => toggleTag(tag));
    container.appendChild(chip);
  });

  renderExcludeHint();
}

function renderExcludeHint() {
  const hint = $("#exclude-hint");
  if (!hint) return;
  const excluded = excludedTagsOf(state.config).map((tag) => tag.toLowerCase());
  const active = ($("#tag-input")?.value || "")
    .split(",")
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);
  const blocked = excluded.filter((tag) => active.includes(tag));

  if (blocked.length) {
    hint.textContent = t(
      "views.gallery.control.tagExcluded",
      "这些标签已被排除，随机图会跳过：{tags}",
    ).replace("{tags}", blocked.map((tag) => `#${tag}`).join(" "));
    return;
  }
  hint.textContent = excluded.length
    ? t("views.gallery.control.excluded", "随机图已排除：{tags}").replace(
        "{tags}",
        excluded.map((tag) => `#${tag}`).join(" "),
      )
    : "";
}

function toggleTag(tag) {
  const input = $("#tag-input");
  if (!input) return;
  const tags = input.value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  const index = tags.indexOf(tag);
  if (index >= 0) tags.splice(index, 1);
  else tags.push(tag);
  input.value = tags.join(",");
  renderTags();
}

async function loadOverview(force = false) {
  try {
    const data = await apiGet("overview", force ? { force: 1 } : {});
    state.overview = data;
    state.config = data.config || null;
    state.tags = Array.isArray(data.tags) ? data.tags : [];
    updateDocsLink();

    renderStats(data.site);
    renderTags();
    updateHealth(data.health, data.health_error || data.site_error);

    if (!state.settingsDirty) {
      fillSettings(data.config);
    } else {
      setFormatAvailability();
    }
    updateSetupNotice();
    setText("#settings-token-state", tokenStateText(data.config));
    setText(
      "#about-base",
      data.config?.base_url || t("views.gallery.setup.notConfigured", "未配置"),
    );
    setText("#about-version", data.version || "-");

    if (data.site_error && !data.health_error) {
      showToast(data.site_error, true);
    }
    return data;
  } catch (error) {
    updateHealth(null, error.message);
    setFormatAvailability();
    updateSetupNotice();
    showToast(error.message || t("views.gallery.error.generic", "加载失败"), true);
    return null;
  }
}

function tokenStateText(config) {
  if (!config) return "";
  if (config.token_configured) {
    return t("views.gallery.settings.tokenStateOn", "已配置 Token：{token}").replace(
      "{token}",
      config.token_masked || "******",
    );
  }
  return t("views.gallery.settings.tokenStateOff", "未配置 Token（仅 WebP / 公开接口）");
}

function excludedTagsOf(config) {
  const raw = config?.exclude_tags;
  if (Array.isArray(raw)) {
    return raw.map((tag) => String(tag).trim()).filter(Boolean);
  }
  if (typeof raw === "string") {
    return raw
      .split(",")
      .map((tag) => tag.trim())
      .filter(Boolean);
  }
  return [];
}

function originAvailable() {
  if (!state.config) return false;
  if (typeof state.config.origin_available === "boolean") {
    return state.config.origin_available;
  }
  return Boolean(state.config.token_configured) && state.config.allow_origin !== false;
}

function pngAvailable() {
  if (!state.config) return false;
  if (typeof state.config.png_available === "boolean") {
    return state.config.png_available;
  }
  return Boolean(state.config.token_configured);
}

function setFormatAvailability() {
  const origin = originAvailable();
  const png = pngAvailable();

  ["#type-select", "#set-type"].forEach((selector) => {
    const select = $(selector);
    if (!select) return;
    const originOption = select.querySelector('option[value="origin"]');
    const pngOption = select.querySelector('option[value="png"]');
    if (originOption) originOption.disabled = !origin;
    if (pngOption) pngOption.disabled = !png;
    if (!origin && select.value === "origin") select.value = "webp";
    if (!png && select.value === "png") select.value = "webp";
  });

  const originButton = document.querySelector('[data-action="origin"]');
  if (originButton) {
    originButton.disabled = !origin;
    originButton.title = origin
      ? ""
      : t(
          "views.gallery.control.originNoToken",
          "PNG 和原图需要有效 Token；未配置时只能使用 WebP。",
        );
  }
  refreshCustomSelects();
  updateOriginHint();
}

/* ------------------------------------------------------------------ */
/* Browse / random                                                     */
/* ------------------------------------------------------------------ */
function activeGallery() {
  const button = $("#gallery-switch button.active");
  return button?.dataset.gallery === "v2" ? "v2" : "v1";
}

function setPreviewLoading(loading) {
  const spinner = $("#preview-spinner");
  const empty = $("#preview-empty");
  const text = $("#preview-empty-text");
  const img = $("#preview-img");

  if (loading) {
    if (spinner) spinner.hidden = false;
    if (empty) empty.hidden = false;
    if (text) text.textContent = t("views.gallery.preview.loading", "正在加载图片…");
    if (img) img.hidden = true;
  } else if (spinner) {
    spinner.hidden = true;
  }
}

function showPreviewError(message) {
  const empty = $("#preview-empty");
  const text = $("#preview-empty-text");
  const img = $("#preview-img");
  const spinner = $("#preview-spinner");
  if (spinner) spinner.hidden = true;
  if (empty) empty.hidden = false;
  if (text) text.textContent = message;
  if (img) img.hidden = true;
  state.current = null;
  renderMeta(null);
  setText("#preview-format", "");
}

function renderMeta(data) {
  const container = $("#preview-meta");
  if (!container) return;
  container.replaceChildren();
  if (!data?.image) return;

  const image = data.image;
  const meta = (label, value, mono = false) => {
    if (value === undefined || value === null || value === "") return;
    const chip = createElement("span", `meta${mono ? " mono" : ""}`);
    const labelNode = createElement("span", "", `${label}:`);
    const valueNode = createElement("b", "", String(value));
    chip.append(labelNode, document.createTextNode(" "), valueNode);
    container.appendChild(chip);
  };

  if (image.width && image.height) {
    meta(t("views.gallery.meta.dimensions", "尺寸"), `${image.width}×${image.height}`);
  }
  meta(t("views.gallery.meta.variant", "格式"), image.variant || data.gallery);
  if (Array.isArray(image.tags) && image.tags.length) {
    meta(t("views.gallery.meta.tags", "标签"), image.tags.slice(0, 6).join(", "));
  }
  meta(
    t("views.gallery.meta.watermark", "水印"),
    image.watermarked
      ? t("views.gallery.meta.yes", "有")
      : t("views.gallery.meta.no", "无"),
  );
  meta(t("views.gallery.meta.id", "ID"), image.id, true);
}

function renderPreview(data) {
  const image = data.image || {};
  const img = $("#preview-img");
  const empty = $("#preview-empty");
  const notice = $("#preview-notice");
  const url = data.preview_url || image.webp || image.url;

  if (!url) {
    showPreviewError(t("views.gallery.preview.noUrl", "图床没有返回可用的图片地址。"));
    return;
  }

  if (img) {
    img.hidden = false;
    img.alt = image.title || image.id || "random image";
    if (!setImageSource(img, url)) {
      showPreviewError(
        t("views.gallery.preview.unsafeUrl", "图床返回的图片地址不安全，已阻止加载。"),
      );
      return;
    }
    img.onerror = () => {
      showPreviewError(t("views.gallery.preview.failed", "图片加载失败，请检查网络或站点地址。"));
    };
  }
  if (empty) empty.hidden = true;

  if (notice) {
    if (data.notice) {
      notice.hidden = false;
      notice.textContent = data.notice;
    } else {
      notice.hidden = true;
      notice.textContent = "";
    }
  }

  renderMeta(data);
  const format = String(image.variant || data.gallery || "webp").toLowerCase();
  setText(
    "#preview-format",
    format === "origin"
      ? t("views.gallery.type.origin", "原图")
      : format.toUpperCase(),
  );
  setFormatAvailability();
}

function updateOriginHint() {
  const hint = $("#origin-hint");
  if (!hint) return;
  const config = state.config;
  const type = $("#type-select")?.value;

  if (!config) {
    hint.textContent = "";
    return;
  }
  if (!config.token_configured) {
    hint.textContent = t(
      "views.gallery.control.originNoToken",
      "PNG 和原图需要有效 Token；未配置时只能使用 WebP。",
    );
    return;
  }
  if (!config.allow_origin) {
    hint.textContent =
      type === "origin"
        ? t(
            "views.gallery.control.originDisabled",
            "原图输出已在插件设置中关闭，请使用 WebP/PNG。",
          )
        : "";
    return;
  }
  hint.textContent =
    type === "origin"
      ? t(
          "views.gallery.control.originReady",
          "原图请求需要 Token，图片体积可能较大。",
        )
      : "";
}

async function loadRandom() {
  if (!bridge) return;
  if (!isConfigured()) {
    updateSetupNotice();
    showPreviewError(
      t("views.gallery.setup.required", "请先在设置中填写图床 API 地址。"),
    );
    return;
  }
  const sequence = ++state.randomSeq;
  const mode = activeGallery();
  let type = $("#type-select")?.value || "webp";
  if (type === "origin" && !originAvailable()) {
    type = "webp";
    const typeSelect = $("#type-select");
    if (typeSelect) typeSelect.value = "webp";
    setFormatAvailability();
    showToast(
      t(
        "views.gallery.control.originNoToken",
        "PNG 和原图需要有效 Token；未配置时只能使用 WebP。",
      ),
      true,
    );
  }
  if (type === "png" && !pngAvailable()) {
    type = "webp";
    const typeSelect = $("#type-select");
    if (typeSelect) typeSelect.value = "webp";
    setFormatAvailability();
    showToast(
      t("views.gallery.control.pngNoToken", "PNG 需要 Token，已改用 WebP。"),
      true,
    );
  }
  const tag = $("#tag-input")?.value.trim() || "";
  const tagMode = $("#tag-mode")?.value || "any";
  const id = $("#id-input")?.value.trim() || "";
  const button = $("#random-btn");

  if (button) button.disabled = true;
  setPreviewLoading(true);

  try {
    const data = await apiGet("random", {
      mode,
      type,
      tag,
      tag_mode: tagMode,
      id,
    });
    if (sequence !== state.randomSeq) return;
    state.current = data;
    renderPreview(data);
  } catch (error) {
    if (sequence !== state.randomSeq) return;
    showPreviewError(error.message || t("views.gallery.error.generic", "加载失败"));
    showToast(error.message || t("views.gallery.error.generic", "加载失败"), true);
  } finally {
    if (sequence === state.randomSeq && button) button.disabled = false;
  }
}

async function getPreviewData() {
  if (!state.current?.image?.id) {
    throw new Error(t("views.gallery.preview.empty", "请先加载一张图片。"));
  }
  if (state.current.previewData) return state.current.previewData;

  const image = state.current.image;
  const data = await apiGet("preview", {
    id: image.id,
    mode: state.current.gallery || "v1",
  });
  state.current.previewData = data;
  return data;
}

async function openOriginal() {
  if (!state.current) return;
  if (!originAvailable()) {
    showToast(
      t(
        "views.gallery.control.originNoToken",
        "未配置 Token，原图不可用；请先在设置中填写 Token。",
      ),
      true,
    );
    return;
  }
  const holder = createElement("div", "preview-empty");
  const spinner = createElement("div", "spinner");
  holder.appendChild(spinner);
  holder.appendChild(
    createElement("p", "", t("views.gallery.preview.loading", "正在加载图片…")),
  );
  openModal(t("views.gallery.action.origin", "查看原图"), holder);

  try {
    const data = await getPreviewData();
    const wrapper = document.createDocumentFragment();
    const img = createElement("img");
    const dataUrl = typeof data.data_url === "string" ? data.data_url : "";
    if (dataUrl.startsWith("data:image/")) {
      img.src = dataUrl;
      img.alt = state.current.image.title || state.current.image.id || "original";
      wrapper.appendChild(img);
    }

    if (data.notice) {
      wrapper.appendChild(createElement("p", "notice", data.notice));
    }

    const actions = createElement("div", "actions");
    const downloadButton = createElement(
      "button",
      "btn primary",
      t("views.gallery.action.download", "下载"),
    );
    downloadButton.type = "button";
    downloadButton.addEventListener("click", () =>
      downloadImage(
        state.current?.image?.id,
        state.current?.gallery || "v1",
        "origin",
      ),
    );
    const copyButton = createElement(
      "button",
      "btn",
      t("views.gallery.action.copyData", "复制图片数据"),
    );
    copyButton.type = "button";
    copyButton.addEventListener("click", () => {
      copyText(data.data_url, t("views.gallery.action.copyData", "复制图片数据"));
    });
    actions.append(downloadButton, copyButton);
    wrapper.appendChild(actions);

    $("#modal-body").replaceChildren(wrapper);
  } catch (error) {
    const message = createElement(
      "p",
      "notice error",
      error.message || t("views.gallery.error.generic", "加载失败"),
    );
    $("#modal-body").replaceChildren(message);
  }
}

function downloadFromPreview(data) {
  if (!data?.data_url) return;
  const id = state.current?.image?.id || "image";
  const extension = extensionOf(data.content_type);
  const anchor = document.createElement("a");
  anchor.href = data.data_url;
  anchor.download = `${id}.${extension}`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  showToast(t("views.gallery.action.downloading", "已开始下载"));
}

async function downloadImage(id, mode, type) {
  if (!id) {
    showToast(t("views.gallery.preview.empty", "请先加载一张图片。"), true);
    return;
  }
  if (type === "origin" && !originAvailable()) {
    showToast(
      t(
        "views.gallery.control.originNoToken",
        "PNG 和原图需要有效 Token；未配置时只能使用 WebP。",
      ),
      true,
    );
    return;
  }
  if (type === "png" && !pngAvailable()) {
    showToast(
      t("views.gallery.control.pngNoToken", "PNG 需要 Token，请先在设置中填写。"),
      true,
    );
    return;
  }
  try {
    if (typeof bridge?.download === "function") {
      await bridge.download("download", { id, mode, type });
      showToast(t("views.gallery.action.downloading", "已开始下载"));
      return;
    }
    const data = await getPreviewData();
    downloadFromPreview(data);
  } catch (error) {
    showToast(error.message || t("views.gallery.error.generic", "下载失败"), true);
  }
}

async function downloadCurrent() {
  const image = state.current?.image;
  if (!image) {
    showToast(t("views.gallery.preview.empty", "请先加载一张图片。"), true);
    return;
  }
  const type = $("#type-select")?.value || "webp";
  if (type === "origin" && !originAvailable()) {
    showToast(
      t(
        "views.gallery.control.originNoToken",
        "PNG 和原图需要有效 Token；未配置时只能使用 WebP。",
      ),
      true,
    );
    return;
  }
  if (type === "png" && !pngAvailable()) {
    showToast(
      t("views.gallery.control.pngNoToken", "PNG 需要 Token，请先在设置中填写。"),
      true,
    );
    return;
  }
  await downloadImage(image.id, state.current.gallery || "v1", type);
}

/* ------------------------------------------------------------------ */
/* Library                                                             */
/* ------------------------------------------------------------------ */
async function loadLibrary(page) {
  if (!bridge) return;
  if (!isConfigured()) {
    updateSetupNotice();
    showLibraryNotice(
      t("views.gallery.setup.required", "请先在设置中填写图床 API 地址。"),
    );
    return;
  }
  const sequence = ++state.librarySeq;
  const pageSize = Number($("#lib-page-size")?.value) || 24;
  const query = $("#lib-q")?.value.trim() || "";
  const tag = $("#lib-tag")?.value.trim() || "";
  const notice = $("#lib-notice");
  const grid = $("#lib-grid");

  state.library.page = page;
  state.library.pageSize = pageSize;
  state.library.lastQuery = query;
  state.library.lastTag = tag;

  if (notice) notice.hidden = true;
  if (grid) {
    grid.replaceChildren();
    grid.appendChild(
      createElement(
        "div",
        "grid-empty",
        t("views.gallery.preview.loading", "正在加载图片…"),
      ),
    );
  }

  try {
    const data = await apiGet("library", {
      page,
      page_size: pageSize,
      q: query,
      tag,
      tag_mode: state.config?.tag_mode || "any",
    });
    if (sequence !== state.librarySeq) return;

    state.library.total = data.total || 0;
    state.library.loaded = true;
    renderLibrary(data);
  } catch (error) {
    if (sequence !== state.librarySeq) return;
    if (grid) {
      grid.replaceChildren(
        createElement(
          "div",
          "grid-empty",
          t("views.gallery.library.failed", "图库加载失败。"),
        ),
      );
    }
    showLibraryNotice(error.message || t("views.gallery.error.generic", "加载失败"));
  }
}

function showLibraryNotice(message) {
  const notice = $("#lib-notice");
  if (!notice) return;
  notice.replaceChildren();
  notice.classList.add("error");
  notice.hidden = false;

  const text = createElement("div", "", message);
  notice.appendChild(text);

  const base = state.config?.base_url;
  if (base) {
    const profile = `${base}/admin/profile`;
    const row = createElement("div");
    row.style.marginTop = "6px";
    row.appendChild(
      createElement(
        "span",
        "",
        t("views.gallery.library.profileHint", "Token 管理地址："),
      ),
    );
    const code = createElement("code", "", profile);
    row.appendChild(code);
    const copy = createElement("button", "btn", t("views.gallery.action.copy", "复制"));
    copy.type = "button";
    copy.style.marginLeft = "8px";
    copy.style.minHeight = "26px";
    copy.style.padding = "2px 8px";
    copy.addEventListener("click", () =>
      copyText(profile, t("views.gallery.library.profileHint", "Token 管理地址")),
    );
    row.appendChild(copy);
    notice.appendChild(row);
  }
}

function renderLibrary(data) {
  const grid = $("#lib-grid");
  if (!grid) return;
  grid.replaceChildren();

  const items = Array.isArray(data.items) ? data.items : [];
  if (!items.length) {
    grid.appendChild(
      createElement(
        "div",
        "grid-empty",
        t("views.gallery.library.empty", "没有找到图片。"),
      ),
    );
  }

  items.forEach((item) => {
    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = "tile";
    tile.title = item.title || item.id;

    const thumb = createElement("div", "tile-thumb");
    const img = document.createElement("img");
    img.loading = "lazy";
    img.alt = item.title || item.id;
    setImageSource(img, item.preview || item.webp || item.origin);
    img.addEventListener("error", () => {
      img.style.display = "none";
    });
    thumb.appendChild(img);

    const body = createElement("div", "tile-body");
    body.appendChild(createElement("div", "tile-title", item.title || item.id));
    const meta = [];
    if (item.width && item.height) meta.push(`${item.width}×${item.height}`);
    if (item.tags?.length) meta.push(`#${item.tags[0]}`);
    body.appendChild(createElement("div", "tile-meta", meta.join(" · ") || item.id));

    tile.append(thumb, body);
    tile.addEventListener("click", () => showInfo(item.id));
    grid.appendChild(tile);
  });

  const total = data.total || items.length;
  const page = data.page || 1;
  const totalPages = Math.max(1, Math.ceil(total / (data.page_size || 24)));

  setText("#lib-total", t("views.gallery.library.total", "共 {total} 张").replace("{total}", total));
  setText(
    "#lib-page-label",
    t("views.gallery.library.page", "第 {page} / {pages} 页")
      .replace("{page}", page)
      .replace("{pages}", totalPages),
  );

  const prev = $("#lib-prev");
  const next = $("#lib-next");
  if (prev) prev.disabled = page <= 1;
  if (next) next.disabled = Boolean(data.has_next) === false;
}

async function showInfo(id) {
  const holder = createElement("div", "preview-empty");
  holder.appendChild(createElement("div", "spinner"));
  holder.appendChild(
    createElement("p", "", t("views.gallery.preview.loading", "正在加载图片…")),
  );
  openModal(t("views.gallery.info.title", "图片详情"), holder);

  try {
    const data = await apiGet("info", { id });
    const info = data.info || {};
    const wrapper = document.createDocumentFragment();

    if (data.preview_url) {
      const img = createElement("img");
      img.alt = info.title || info.id || "image";
      if (setImageSource(img, data.preview_url)) {
        wrapper.appendChild(img);
      }
    }

    const list = createElement("dl", "detail-list");
    const add = (label, value, mono = false) => {
      if (value === undefined || value === null || value === "") return;
      list.appendChild(createElement("dt", "", label));
      list.appendChild(createElement("dd", mono ? "mono" : "", String(value)));
    };

    add("ID", info.id, true);
    add(t("views.gallery.info.titleLabel", "标题"), info.title);
    add(t("views.gallery.meta.dimensions", "尺寸"), info.width && info.height ? `${info.width}×${info.height}` : "");
    add(t("views.gallery.info.size", "大小"), info.size ? formatBytes(info.size) : "");
    add(t("views.gallery.info.mime", "格式"), info.mime);
    const infoTags = Array.isArray(info.tags)
      ? info.tags.join(", ")
      : typeof info.tags === "string"
        ? info.tags
        : "";
    add(t("views.gallery.info.tags", "标签"), infoTags);
    add(t("views.gallery.info.source", "来源"), info.source);
    add(t("views.gallery.info.status", "状态"), info.status);
    add(t("views.gallery.info.watermark", "接口水印"), info.watermarked ? t("views.gallery.meta.yes", "有") : t("views.gallery.meta.no", "无"));
    add(t("views.gallery.info.created", "上传时间"), formatDate(info.createdAt));
    add(t("views.gallery.info.updated", "更新时间"), formatDate(info.updatedAt));
    add(t("views.gallery.info.url", "直链"), info.url, true);
    add(t("views.gallery.info.webp", "WebP"), info.webpUrl, true);
    add(t("views.gallery.info.api", "API"), info.apiUrl, true);
    wrapper.appendChild(list);

    const actions = createElement("div", "actions");
    if (info.apiUrl) {
      const copy = createElement("button", "btn", t("views.gallery.info.copyApi", "复制 API 地址"));
      copy.type = "button";
      copy.addEventListener("click", () => copyText(info.apiUrl, "API URL"));
      actions.appendChild(copy);
    }
    if (info.url) {
      const copy = createElement("button", "btn", t("views.gallery.info.copyUrl", "复制直链"));
      copy.type = "button";
      copy.addEventListener("click", () => copyText(info.url, "URL"));
      actions.appendChild(copy);
    }
    wrapper.appendChild(actions);

    $("#modal-body").replaceChildren(wrapper);
  } catch (error) {
    $("#modal-body").replaceChildren(
      createElement("p", "notice error", error.message || t("views.gallery.error.generic", "加载失败")),
    );
  }
}

/* ------------------------------------------------------------------ */
/* Settings                                                            */
/* ------------------------------------------------------------------ */
function fillSettings(config) {
  if (!config) return;

  $("#set-base").value = config.base_url || "";
  $("#set-token").value = "";
  $("#set-clear").checked = false;
  $("#set-token").placeholder = config.token_configured
    ? t(
        "views.gallery.settings.tokenPlaceholderSaved",
        "已保存 {token}，留空保持不变",
      ).replace("{token}", config.token_masked || "******")
    : t(
        "views.gallery.settings.tokenPlaceholder",
        "粘贴 img_ 开头的 Token（可留空）",
      );

  $("#set-gallery").value = config.default_gallery || "v1";
  $("#set-type").value = config.default_type || "webp";
  $("#set-tag-mode").value = config.tag_mode || "any";
  $("#set-send-mode").value = config.send_mode || "bytes";
  $("#set-token-mode").value = config.token_mode || "query";
  $("#set-exclude-tags").value = excludedTagsOf(config).join(",");
  $("#set-exclude-retries").value = config.exclude_retries ?? 5;
  $("#set-show-meta").checked = Boolean(config.show_meta);
  $("#set-allow-origin").checked = Boolean(config.allow_origin);
  $("#set-enable-ai-pick").checked = config.enable_ai_pick !== false;
  $("#set-timeout").value = config.request_timeout ?? 20;
  $("#set-retry").value = config.retry_times ?? 1;
  $("#set-max-image").value = config.max_image_mb ?? 12;
  $("#set-cooldown").value = config.cooldown_seconds ?? 3;
  $("#set-cache").value = config.cache_ttl ?? 60;
  $("#set-max-tags").value = config.max_tags ?? 5;
  $("#set-webui-page").value = config.webui_page_size ?? 24;
  $("#set-command-page").value = config.command_page_size ?? 8;
  updateSecurityHint(config);

  const pageSize = $("#lib-page-size");
  if (pageSize) {
    const value = String(config.webui_page_size || 24);
    if (!Array.from(pageSize.options).some((option) => option.value === value)) {
      pageSize.add(
        new Option(
          t("views.gallery.library.perPage", "{n} / 页").replace("{n}", value),
          value,
        ),
      );
    }
    pageSize.value = value;
  }

  setText("#settings-token-state", tokenStateText(config));
  setFormatAvailability();
  updateSetupNotice();
  updateDocsLink();
  refreshCustomSelects();
  renderExcludeHint();
  state.settingsDirty = false;
}

function collectSettings() {
  return {
    base_url: $("#set-base").value.trim(),
    token: $("#set-token").value.trim(),
    clear_token: $("#set-clear").checked,
    default_gallery: $("#set-gallery").value,
    default_type: $("#set-type").value,
    tag_mode: $("#set-tag-mode").value,
    send_mode: $("#set-send-mode").value,
    token_mode: $("#set-token-mode").value,
    exclude_tags: $("#set-exclude-tags").value.trim(),
    exclude_retries: Number($("#set-exclude-retries").value),
    show_meta: $("#set-show-meta").checked,
    allow_origin: $("#set-allow-origin").checked,
    enable_ai_pick: $("#set-enable-ai-pick").checked,
    request_timeout: Number($("#set-timeout").value),
    retry_times: Number($("#set-retry").value),
    max_image_mb: Number($("#set-max-image").value),
    cooldown_seconds: Number($("#set-cooldown").value),
    cache_ttl: Number($("#set-cache").value),
    max_tags: Number($("#set-max-tags").value),
    webui_page_size: Number($("#set-webui-page").value),
    command_page_size: Number($("#set-command-page").value),
  };
}

async function saveSettings() {
  const button = $("#save-settings");
  const status = $("#settings-status");
  const payload = collectSettings();

  if (button) button.disabled = true;
  if (status) status.textContent = t("views.gallery.settings.saving", "保存中…");

  try {
    const result = await apiPost("settings", payload);
    state.config = result.config || state.config;
    fillSettings(result.config);
    setText("#settings-token-state", tokenStateText(result.config));
    setText(
      "#about-base",
      result.config?.base_url || t("views.gallery.setup.notConfigured", "未配置"),
    );
    if (status) status.textContent = t("views.gallery.settings.saved", "已保存");
    showToast(t("views.gallery.settings.saved", "已保存"));
    await loadOverview(true);
  } catch (error) {
    if (status) status.textContent = t("views.gallery.settings.saveFailed", "保存失败");
    showToast(error.message || t("views.gallery.settings.saveFailed", "保存失败"), true);
  } finally {
    if (button) button.disabled = false;
  }
}

async function testConnection() {
  const button = $("#test-connection");
  const status = $("#settings-status");
  if (button) button.disabled = true;
  if (status) status.textContent = t("views.gallery.settings.testing", "测试中…");

  try {
    const data = await apiGet("health");
    const latency = data.latency_ms !== undefined ? ` · ${data.latency_ms} ms` : "";
    const count = data.health?.count !== undefined ? ` · count ${data.health.count}` : "";
    if (status) {
      status.textContent = t("views.gallery.settings.testOk", "连接正常{latency}{count}")
        .replace("{latency}", latency)
        .replace("{count}", count);
    }
    updateHealth(data.health, null);
    showToast(t("views.gallery.settings.testOkShort", "连接正常"));
  } catch (error) {
    if (status) status.textContent = t("views.gallery.settings.testFail", "连接失败");
    updateHealth(null, error.message);
    showToast(error.message || t("views.gallery.settings.testFail", "连接失败"), true);
  } finally {
    if (button) button.disabled = false;
  }
}

/* ------------------------------------------------------------------ */
/* Tabs & events                                                       */
/* ------------------------------------------------------------------ */
function activateTab(name) {
  state.activeTab = name;
  updatePageHeading(name);
  $$(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.tab === name);
  });
  $$(".panel").forEach((panel) => {
    panel.hidden = panel.id !== `tab-${name}`;
  });

  if (name === "library" && !state.library.loaded) {
    loadLibrary(1);
  }
  if (name === "about") {
    setText("#about-version", state.overview?.version || "-");
  }
}

function bindEvents() {
  $$(".tab").forEach((tab) => {
    tab.addEventListener("click", () => activateTab(tab.dataset.tab));
  });

  $("#theme-toggle")?.addEventListener("click", () => {
    const current =
      document.documentElement.dataset.theme === "light" ? "light" : "dark";
    localTheme = current === "light" ? "dark" : "light";
    applyTheme(localTheme);
  });

  $("#goto-settings")?.addEventListener("click", () => {
    activateTab("settings");
    $("#set-base")?.focus();
  });

  $$("#gallery-switch button").forEach((button) => {
    button.addEventListener("click", () => {
      $$("#gallery-switch button").forEach((item) =>
        item.classList.toggle("active", item === button),
      );
    });
  });

  $("#random-btn")?.addEventListener("click", loadRandom);
  $("#tag-input")?.addEventListener("input", renderTags);
  $("#tag-input")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadRandom();
  });
  $("#id-input")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadRandom();
  });
  $("#type-select")?.addEventListener("change", updateOriginHint);

  $$("[data-action]").forEach((button) => {
    button.addEventListener("click", () => {
      const action = button.dataset.action;
      const image = state.current?.image;
      if (!image) {
        showToast(t("views.gallery.preview.empty", "请先加载一张图片。"), true);
        return;
      }
      if (action === "link") {
        copyText(image.url || image.webp || image.origin, t("views.gallery.action.link", "复制链接"));
      } else if (action === "markdown") {
        const url = image.url || image.webp || image.origin;
        const alt = image.title || image.id || "image";
        copyText(`![${alt}](${url})`, "Markdown");
      } else if (action === "id") {
        copyText(image.id, "ID");
      } else if (action === "origin") {
        openOriginal();
      } else if (action === "download") {
        downloadCurrent();
      }
    });
  });

  $("#lib-search")?.addEventListener("click", () => loadLibrary(1));
  $("#lib-q")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadLibrary(1);
  });
  $("#lib-tag")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadLibrary(1);
  });
  $("#lib-page-size")?.addEventListener("change", () => loadLibrary(1));
  $("#lib-prev")?.addEventListener("click", () =>
    loadLibrary(Math.max(1, state.library.page - 1)),
  );
  $("#lib-next")?.addEventListener("click", () =>
    loadLibrary(state.library.page + 1),
  );

  $("#save-settings")?.addEventListener("click", saveSettings);
  $("#test-connection")?.addEventListener("click", testConnection);

  $$(
    "#set-base, #set-token, #set-gallery, #set-type, #set-tag-mode, #set-send-mode, #set-token-mode, #set-exclude-tags, #set-exclude-retries, #set-show-meta, #set-allow-origin, #set-enable-ai-pick, #set-timeout, #set-retry, #set-max-image, #set-cooldown, #set-cache, #set-max-tags, #set-webui-page, #set-command-page, #set-clear",
  ).forEach((element) => {
    element.addEventListener("input", () => {
      state.settingsDirty = true;
    });
    element.addEventListener("change", () => {
      state.settingsDirty = true;
    });
  });

  $("#refresh-all")?.addEventListener("click", async () => {
    await loadOverview(true);
    if (state.activeTab === "library") {
      await loadLibrary(state.library.page);
    } else if (state.activeTab === "browse") {
      await loadRandom();
    }
  });

  $("#copy-docs")?.addEventListener("click", () => {
    const docs =
      state.config?.docs_url || "https://imgapi.bixygames.com/docs";
    copyText(docs, t("views.gallery.about.copyDocs", "复制 API 文档地址"));
  });
  $("#copy-profile")?.addEventListener("click", () => {
    const base = state.config?.base_url;
    if (!base) {
      showToast(
        t("views.gallery.setup.required", "请先在设置中填写图床 API 地址。"),
        true,
      );
      return;
    }
    copyText(
      `${base}/admin/profile`,
      t("views.gallery.about.copyProfile", "复制 Token 管理地址"),
    );
  });

  $("#docs-link")?.addEventListener("click", (event) => {
    event.preventDefault();
    openDocs();
  });
  $("#copy-docs-inline")?.addEventListener("click", () => {
    copyText(
      docsUrl(),
      t("views.gallery.settings.docsLabel", "接口文档"),
    );
  });

  document.addEventListener("click", (event) => {
    if (!event.target.closest(".select-wrap")) closeAllSelects();
  });

  $("#modal-close")?.addEventListener("click", closeModal);
  $("#modal")?.addEventListener("click", (event) => {
    if (event.target === $("#modal")) closeModal();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeAllSelects();
      if (!$("#modal").hidden) closeModal();
    }
  });
}

/* ------------------------------------------------------------------ */
/* Boot                                                                */
/* ------------------------------------------------------------------ */
async function init() {
  if (!bridge || typeof bridge.ready !== "function") {
    const app = $(".app");
    if (app) {
      app.replaceChildren(
        createElement(
          "div",
          "fatal",
          t("views.gallery.error.bridge", "请从 AstrBot 插件页打开此面板（Bridge 不可用）。"),
        ),
      );
    }
    return;
  }

  let context = null;
  try {
    context = await bridge.ready();
  } catch {
    /* The bridge may still work even if the initial context failed. */
  }

  const initialTheme =
    document.documentElement.dataset.theme ||
    (context && context.isDark === false ? "light" : "dark");
  applyTheme(initialTheme);

  applyI18n();
  bindEvents();
  enhanceSelects();
  updatePageHeading(state.activeTab);

  // Follow WebUI language/theme changes.
  if (typeof bridge.onContext === "function") {
    bridge.onContext((next) => {
      if (localTheme) {
        applyTheme(localTheme);
      } else if (next && typeof next.isDark === "boolean") {
        applyTheme(next.isDark ? "dark" : "light");
      }
      applyI18n();
      updatePageHeading(state.activeTab);
      refreshCustomSelects();
      renderTags();
      if (state.current) renderMeta(state.current);
      if (state.config) setText("#settings-token-state", tokenStateText(state.config));
    });
  }

  state.activeTab = "browse";
  await loadOverview();
  if (isConfigured()) {
    await loadRandom();
  } else {
    updateSetupNotice();
  }

  // Optional deep link: #library / #settings / #about
  const tab = (location.hash || "").replace("#", "");
  if (["browse", "library", "settings", "about"].includes(tab)) {
    activateTab(tab);
  }
}

init();
