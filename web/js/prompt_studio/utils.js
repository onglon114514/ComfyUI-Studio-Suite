import { $el } from "/scripts/ui.js";


export function addStylesheet(url) {
    let href = url;
    if (href.endsWith(".js")) {
        href = href.slice(0, -2) + "css";
    }
    $el("link", {
        parent: document.head,
        rel: "stylesheet",
        type: "text/css",
        href: href.startsWith("http") ? href : new URL(href, import.meta.url).toString(),
    });
}
