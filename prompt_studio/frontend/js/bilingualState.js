export const BILINGUAL_PROPERTY = "studio_suite_bilingual";
export const BILINGUAL_LIMIT = 512;

export function annotationKey(text) {
    return String(text || "").replace(/\\([()])/g, "$1")
        .replace(/\s+/g, " ").replace(/\s*,\s*/g, ", ").trim()
        .replace(/[.,，。!?！？;；]+$/u, "").trim().toLowerCase();
}

// Workflow metadata, not model input. Bound data arriving from an iframe/file.
export function sanitizeBilingualState(value) {
    const result = { version: 1, positive: [], negative: [] };
    let budget = 120000;
    for (const kind of ["positive", "negative"]) {
        const entries = Array.isArray(value?.[kind]) ? value[kind] : [];
        const seen = new Set();
        for (const entry of entries.slice(-BILINGUAL_LIMIT)) {
            if (typeof entry?.text !== "string" || typeof entry?.local !== "string") continue;
            const text = entry.text.trim();
            const local = entry.local.trim();
            const key = annotationKey(text);
            if (!key || !local || text.length > 8192 || local.length > 8192 || seen.has(key)) continue;
            if (text.length + local.length > budget) continue;
            budget -= text.length + local.length;
            seen.add(key);
            result[kind].push({ text, local, lang: entry.lang === "en" ? "en" : "zh" });
        }
    }
    return result;
}
