import MarkdownIt from "markdown-it";
import DOMPurify from "dompurify";
import { useEffect, useId, useRef, useState } from "react";

export default function Markdown({
  source,
  diagrams = false,
  resolveLink,
}: {
  source: string;
  diagrams?: boolean;
  resolveLink?: (href: string) => string;
}) {
  const container = useRef<HTMLDivElement>(null);
  const identity = useId().replace(/[^a-z0-9]/gi, "");
  const [error, setError] = useState("");
  const markdown = new MarkdownIt({
    html: diagrams,
    linkify: false,
    typographer: false,
  });
  const link =
    markdown.renderer.rules.link_open ||
    ((tokens, index, options, _env, renderer) =>
      renderer.renderToken(tokens, index, options));
  markdown.renderer.rules.link_open = (
    tokens,
    index,
    options,
    env,
    renderer,
  ) => {
    const href = tokens[index].attrGet("href") || "";
    if (resolveLink) tokens[index].attrSet("href", resolveLink(href));
    if (/^https?:/.test(href))
      tokens[index].attrSet("rel", "noopener noreferrer");
    return link(tokens, index, options, env, renderer);
  };
  const html = DOMPurify.sanitize(markdown.render(source), {
    FORBID_TAGS: ["style"],
    FORBID_ATTR: ["style"],
    ADD_TAGS: ["picture", "source"],
    ADD_ATTR: ["srcset", "media"],
  });
  useEffect(() => {
    let cancelled = false;
    let generation = 0;
    const root = container.current;
    const render = async () => {
      const ticket = ++generation;
      if (!root) return;
      root.innerHTML = html;
      root.dataset.ready = "false";
      if (resolveLink)
        for (const image of root.querySelectorAll("img,source")) {
          const attr = image.tagName === "SOURCE" ? "srcset" : "src";
          const original = image.getAttribute(attr);
          if (original && !/^https?:|^data:|^\//.test(original))
            image.setAttribute(attr, resolveLink(original));
        }
      for (const picture of root.querySelectorAll("picture source[media]"))
        if (picture.getAttribute("media") === "(prefers-color-scheme: dark)")
          picture.setAttribute(
            "media",
            getComputedStyle(document.documentElement).colorScheme === "dark"
              ? "(min-width: 0px)"
              : "(max-width: 0px)",
          );
      setError("");
      const blocks = [
        ...root.querySelectorAll<HTMLElement>("pre code.language-mermaid"),
      ];
      if (!diagrams || !blocks.length) {
        root.dataset.ready = "true";
        return;
      }
      const mermaid = (await import("mermaid")).default;
      const style = getComputedStyle(document.documentElement);
      const color = (name: string) =>
        style.getPropertyValue(`--fsp-color-${name}`).trim();
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: "base",
        themeVariables: {
          primaryColor: color("primary-soft"),
          primaryTextColor: color("text"),
          primaryBorderColor: color("primary"),
          secondaryColor: color("surface-muted"),
          tertiaryColor: color("surface"),
          lineColor: color("text-muted"),
          fontFamily: style.getPropertyValue("--fsp-font-family-text").trim(),
        },
      });
      for (let i = 0; i < blocks.length; i++) {
        try {
          const { svg } = await mermaid.render(
            `scepka-${identity}-${ticket}-${i}`,
            blocks[i].textContent || "",
          );
          if (cancelled || ticket !== generation) return;
          const wrapper = document.createElement("div");
          wrapper.className = "diagram";
          wrapper.innerHTML = svg;
          blocks[i].parentElement?.replaceWith(wrapper);
        } catch (e) {
          if (!cancelled)
            setError(
              e instanceof Error ? e.message : "Не удалось отобразить схему",
            );
        }
      }
      if (!cancelled) root.dataset.ready = "true";
    };
    render();
    const observer = new MutationObserver(() => {
      render();
    });
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    const media = matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", render);
    return () => {
      cancelled = true;
      observer.disconnect();
      media.removeEventListener("change", render);
    };
  }, [html, diagrams, identity]);
  return (
    <>
      <div
        ref={container}
        className="markdown"
        dangerouslySetInnerHTML={{ __html: html }}
      />
      {error && <p role="alert">{error}</p>}
    </>
  );
}
