const { Plugin, TFile } = require("obsidian");

const DEFAULT_SETTINGS = {
  delayMs: 500,
  fallbackTags: ["kind/note"],
  pathDefaults: {},
};

function timestamp() {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
}

function isEmpty(value) {
  return value === undefined || value === null || value === "" || (Array.isArray(value) && value.length === 0);
}

function isSameOrChild(path, prefix) {
  return path === prefix || path.startsWith(`${prefix}/`);
}

function defaultsForPath(path, rules) {
  const matches = Object.keys(rules.pathDefaults || {}).filter((prefix) => isSameOrChild(path, prefix));
  if (!matches.length) return [...(rules.fallbackTags || ["kind/note"])];
  const mostSpecific = matches.sort((left, right) => right.length - left.length)[0];
  return [...rules.pathDefaults[mostSpecific]];
}

function shouldManagePath(path) {
  return path.endsWith(".md") && !path.split("/").some((segment) => segment.startsWith("."));
}

function firstTag(tags, prefix) {
  return tags.find((tag) => tag.startsWith(prefix));
}

function reconcileTags(existingTags, newPath, oldPath, rules) {
  const current = Array.isArray(existingTags) ? [...existingTags] : [];
  const desired = defaultsForPath(newPath, rules);
  const oldDefaults = oldPath ? defaultsForPath(oldPath, rules) : [];
  const desiredKind = firstTag(desired, "kind/");
  const oldKind = firstTag(oldDefaults, "kind/");
  const desiredWorkflow = firstTag(desired, "workflow/");
  const oldWorkflow = firstTag(oldDefaults, "workflow/");
  let result = [...current];

  const currentKind = firstTag(result, "kind/");
  if (!currentKind && desiredKind) result.push(desiredKind);
  else if (oldKind && desiredKind && currentKind === oldKind && desiredKind !== oldKind) {
    result = result.map((tag) => (tag === oldKind ? desiredKind : tag));
  }
  if (oldWorkflow && !desiredWorkflow) result = result.filter((tag) => tag !== oldWorkflow);
  else if (desiredWorkflow && !result.includes(desiredWorkflow)) {
    if (oldWorkflow) result = result.filter((tag) => tag !== oldWorkflow);
    result.push(desiredWorkflow);
  }
  for (const tag of desired) {
    if (!tag.startsWith("kind/") && !tag.startsWith("workflow/") && !result.includes(tag)) result.push(tag);
  }
  return [...new Set(result)];
}

module.exports = class AutoPropertiesPlugin extends Plugin {
  async onload() {
    this.settings = { ...DEFAULT_SETTINGS, ...(await this.loadData() || {}) };
    this.pending = new Map();
    this.registerEvent(this.app.vault.on("create", (file) => this.schedule(file, null)));
    this.registerEvent(this.app.vault.on("rename", (file, oldPath) => this.schedule(file, oldPath)));
  }

  onunload() {
    for (const timer of this.pending.values()) window.clearTimeout(timer);
    this.pending.clear();
  }

  schedule(file, oldPath) {
    if (!(file instanceof TFile) || !shouldManagePath(file.path)) return;
    const current = this.pending.get(file.path);
    if (current) window.clearTimeout(current);
    const timer = window.setTimeout(() => {
      this.pending.delete(file.path);
      void this.applyProperties(file, oldPath);
    }, this.settings.delayMs);
    this.pending.set(file.path, timer);
  }

  async applyProperties(file, oldPath) {
    const current = this.app.vault.getAbstractFileByPath(file.path);
    if (!(current instanceof TFile) || !shouldManagePath(current.path)) return;
    const defaults = defaultsForPath(current.path, this.settings);
    const createdAt = timestamp();
    await this.app.fileManager.processFrontMatter(current, (frontmatter) => {
      if (isEmpty(frontmatter.created)) frontmatter.created = createdAt;
      if (isEmpty(frontmatter.modified)) frontmatter.modified = createdAt;
      const tags = reconcileTags(frontmatter.tags, current.path, oldPath, this.settings);
      if (isEmpty(frontmatter.tags) || tags.join("\u0000") !== (Array.isArray(frontmatter.tags) ? frontmatter.tags : [frontmatter.tags]).join("\u0000")) {
        frontmatter.tags = tags.length ? tags : defaults;
      }
    });
  }
};
