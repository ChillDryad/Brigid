import { state } from "./state.js";
import { DEFAULT_THEME, DEFAULT_APPS } from "./constants.js";

const STORAGE_KEY = "BRIGID_DASHBOARD_STATE";
const LEGACY_STORAGE_KEY = "HESTIA_DASHBOARD_STATE";
const LEGACY_THEME_KEY = "hestia_theme";
const LEGACY_APPS_KEY = "hestia_apps";
const SENSITIVE_KEYS = ["apiKey", "password", "token", "secret", "auth", "key", "userId", "url"];
let saveTimer;

function mergeState(parsed) {
  if (parsed?.apps && Array.isArray(parsed.apps)) state.apps = parsed.apps;
  if (parsed?.settings && typeof parsed.settings === "object") {
    state.settings = { ...state.settings, ...parsed.settings };
    state.settings.theme = { ...DEFAULT_THEME, ...(parsed.settings.theme || {}) };
  }
}

function defaults() {
  state.apps = structuredClone(DEFAULT_APPS);
  state.settings.theme = { ...DEFAULT_THEME };
}

function localPayload() {
  return { apps: state.apps, settings: state.settings };
}

function saveLocal() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(localPayload()));
}

async function saveRemote() {
  try {
    const response = await fetch("/api/profile", {
      method: "PUT",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(localPayload()),
    });
    if (!response.ok) throw new Error(`Profile save failed (${response.status})`);
  } catch (error) {
    console.warn("[storage] Server profile save deferred.", error);
  }
}

export async function loadState() {
  try {
    const local = localStorage.getItem(STORAGE_KEY) || localStorage.getItem(LEGACY_STORAGE_KEY);
    if (local) mergeState(JSON.parse(local));
    else {
      const legacyTheme = localStorage.getItem(LEGACY_THEME_KEY);
      const legacyApps = localStorage.getItem(LEGACY_APPS_KEY);
      if (legacyTheme || legacyApps) {
        if (legacyTheme) {
          const parsedTheme = JSON.parse(legacyTheme);
          state.settings.theme = { ...DEFAULT_THEME, ...(parsedTheme.theme || parsedTheme) };
          state.settings.custom_presets = parsedTheme.custom_presets || {};
        }
        if (legacyApps) state.apps = JSON.parse(legacyApps);
      } else {
        defaults();
      }
    }

    if (!state.apps?.length && !local) defaults();
    state.settings.theme = { ...DEFAULT_THEME, ...(state.settings.theme || {}) };
    saveLocal();

    const response = await fetch("/api/profile", { credentials: "same-origin" });
    if (response.status === 204) {
      await saveRemote();
    } else if (response.ok) {
      const remote = await response.json();
      if (remote.apps && remote.settings) {
        mergeState(remote);
        saveLocal();
      }
    } else {
      throw new Error(`Profile load failed (${response.status})`);
    }
  } catch (error) {
    console.warn("[storage] Using local dashboard state.", error);
    if (!state.apps?.length) defaults();
  }
  return state;
}

export function saveState() {
  try {
    saveLocal();
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveRemote, 350);
  } catch (error) {
    console.error("[storage] Failed to save local state.", error);
  }
}

export async function resetState() {
  localStorage.removeItem(STORAGE_KEY);
  localStorage.removeItem(LEGACY_STORAGE_KEY);
  localStorage.removeItem(LEGACY_THEME_KEY);
  localStorage.removeItem(LEGACY_APPS_KEY);
  try {
    await fetch("/api/profile", { method: "DELETE", credentials: "same-origin" });
  } finally {
    window.location.reload();
  }
}

export function exportStateToFile(sanitize = false) {
  const appsClone = JSON.parse(JSON.stringify(state.apps));
  if (sanitize) {
    appsClone.forEach((app) => {
      if (!app.data) return;
      SENSITIVE_KEYS.forEach((key) => {
        if (app.data[key]) app.data[key] = "";
      });
    });
  }

  const exportData = {
    apps: appsClone,
    settings: state.settings,
    timestamp: Date.now(),
    version: "brigid-1",
    mode: sanitize ? "clean" : "full",
  };
  const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `brigid_config_${sanitize ? "CLEAN" : "FULL"}_${new Date().toISOString().slice(0, 10)}.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function importStateFromFile(file) {
  return new Promise((resolve, reject) => {
    if (!file) return reject(new Error("No file"));
    const reader = new FileReader();
    reader.onload = (event) => {
      try {
        mergeState(JSON.parse(event.target.result));
        saveState();
        resolve(state);
      } catch (error) {
        reject(error);
      }
    };
    reader.onerror = reject;
    reader.readAsText(file);
  });
}
