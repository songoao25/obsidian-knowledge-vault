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
  if (!currentKind && desiredKind) {
    result.push(desiredKind);
  } else if (oldKind && desiredKind && currentKind === oldKind && desiredKind !== oldKind) {
    result = result.map((tag) => (tag === oldKind ? desiredKind : tag));
  }

  if (oldWorkflow && !desiredWorkflow) {
    result = result.filter((tag) => tag !== oldWorkflow);
  } else if (desiredWorkflow && !result.includes(desiredWorkflow)) {
    if (oldWorkflow) result = result.filter((tag) => tag !== oldWorkflow);
    result.push(desiredWorkflow);
  }

  for (const tag of desired) {
    if (!tag.startsWith("kind/") && !tag.startsWith("workflow/") && !result.includes(tag)) result.push(tag);
  }
  return [...new Set(result)];
}

module.exports = { defaultsForPath, reconcileTags, shouldManagePath };
