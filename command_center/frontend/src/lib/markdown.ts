import DOMPurify from "dompurify";
import { Marked } from "marked";
import hljs from "highlight.js/lib/core";
import javascript from "highlight.js/lib/languages/javascript";
import typescript from "highlight.js/lib/languages/typescript";
import python from "highlight.js/lib/languages/python";
import bash from "highlight.js/lib/languages/bash";
import json from "highlight.js/lib/languages/json";
import yaml from "highlight.js/lib/languages/yaml";
import xml from "highlight.js/lib/languages/xml";
import css from "highlight.js/lib/languages/css";
import sql from "highlight.js/lib/languages/sql";
import markdown from "highlight.js/lib/languages/markdown";
import dockerfile from "highlight.js/lib/languages/dockerfile";

hljs.registerLanguage("javascript", javascript);
hljs.registerLanguage("js", javascript);
hljs.registerLanguage("typescript", typescript);
hljs.registerLanguage("ts", typescript);
hljs.registerLanguage("python", python);
hljs.registerLanguage("py", python);
hljs.registerLanguage("bash", bash);
hljs.registerLanguage("sh", bash);
hljs.registerLanguage("shell", bash);
hljs.registerLanguage("json", json);
hljs.registerLanguage("yaml", yaml);
hljs.registerLanguage("yml", yaml);
hljs.registerLanguage("html", xml);
hljs.registerLanguage("xml", xml);
hljs.registerLanguage("css", css);
hljs.registerLanguage("sql", sql);
hljs.registerLanguage("markdown", markdown);
hljs.registerLanguage("md", markdown);
hljs.registerLanguage("dockerfile", dockerfile);

const marked = new Marked({
  gfm: true, breaks: true,
  renderer: {
    code({ text, lang }: { text: string; lang?: string }) {
      const language = lang && hljs.getLanguage(lang) ? lang : "";
      const highlighted = language ? hljs.highlight(text, { language }).value : escapeHtml(text);
      return `<pre data-lang="${language}"><button class="btn sm ghost copy-btn" data-copy type="button" aria-label="Copy code">Copy</button><code class="hljs language-${language}">${highlighted}</code></pre>`;
    },
  },
});

function escapeHtml(s: string) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

export function renderMarkdown(text: string): string {
  const html = marked.parse(text || "", { async: false }) as string;
  return DOMPurify.sanitize(html, { ADD_ATTR: ["target", "data-copy", "data-lang"] });
}

/** Delegated click handler for copy buttons inside rendered markdown. */
export function handleMarkdownClick(e: React.MouseEvent) {
  const btn = (e.target as HTMLElement).closest("[data-copy]") as HTMLButtonElement | null;
  if (!btn) return;
  const code = btn.parentElement?.querySelector("code")?.textContent || "";
  navigator.clipboard?.writeText(code).then(() => {
    btn.textContent = "Copied";
    setTimeout(() => { btn.textContent = "Copy"; }, 1200);
  });
}
