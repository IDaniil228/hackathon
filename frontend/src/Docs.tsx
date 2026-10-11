import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { FileDown } from "lucide-react";
import { Brand, Theme, Federation } from "./App";
import { Button, ErrorBox, Go, Loading } from "./ui";
import Markdown from "./Markdown";

const sources = import.meta.glob<string>(
  [
    "../../docs/{api,matcher,taskgen,frontend}/**/*.md",
    "../../docs/Структура БД.md",
    "../../docs/database.md",
    "../../{api,matcher,taskgen,brand,db,frontend}/README.md",
    "../../README.md",
  ],
  { query: "?raw", import: "default" },
);
const assets = import.meta.glob<string>(
  [
    "../../docs/frontend/screenshots/*.png",
    "../../brand/assets/logo/*.svg",
    "../../brand/dist/fonts/*OFL.txt",
    "../../docs/{api,matcher,taskgen}/**/openapi.json",
  ],
  { query: "?url", import: "default", eager: true },
);
const names: Record<string, string> = {
  architecture: "Архитектура",
  deployment: "Запуск",
  frontend: "Контракт интерфейса",
  validation: "Проверки и результаты",
  security: "Безопасность",
  "matching-mechanics": "Механика подбора",
  "testing-mechanics": "Механика тестирования",
  models: "Модели",
  README: "Обзор",
  development: "Разработка",
  libraries: "Зависимости",
  api: "API",
  settings: "Настройки",
};
export default function Docs({ standalone = false }: { standalone?: boolean }) {
  const [params, setParams] = useSearchParams();
  const current = params.get("file") || "../../docs/api/architecture.md";
  const all = params.get("print") === "all";
  const bundle = params.get("bundle") === "frontend";
  const [documents, setDocuments] = useState<
    { path: string; source: string }[]
  >([]);
  const [error, setError] = useState<unknown>();
  useEffect(() => {
    let active = true;
    setDocuments([]);
    setError(undefined);
    const files = bundle
      ? Object.keys(sources).filter((path) =>
          path.startsWith("../../docs/frontend/"),
        )
      : all
        ? Object.keys(sources).filter((p) => !p.includes("/generated/"))
        : [current];
    Promise.all(
      files.map(async (path) => {
        if (!sources[path]) throw new Error("Документ не найден");
        return { path, source: await sources[path]() };
      }),
    )
      .then((values) => {
        if (active) setDocuments(values);
      })
      .catch((e) => {
        if (active) setError(e);
      });
    return () => {
      active = false;
    };
  }, [current, all, bundle]);
  function link(path: string, href: string) {
    if (/^https?:|^mailto:|^#/.test(href)) return href;
    const url = new URL(
      href,
      `https://scepka.invalid/${path.replace("../../", "")}`,
    );
    const key = "../../" + decodeURIComponent(url.pathname.slice(1));
    if (sources[key])
      return `${import.meta.env.BASE_URL}${standalone ? "" : "docs"}?file=${encodeURIComponent(key)}${url.hash}`;
    if (!standalone && key === "../../docs/api/openapi.json")
      return "/api/openapi.json";
    if (assets[key]) return assets[key];
    return `https://github.com/IDaniil228/hackathon/blob/master/${url.pathname.slice(1)}${url.hash}`;
  }
  return (
    <div className="docs-page">
      <nav className="public-nav">
        <Brand />
        <div className="nav-links">
          <Theme />
          {standalone ? (
            <a
              href={
                import.meta.env.VITE_PROTOTYPE_URL ||
                "https://scepka.rokokol.art"
              }
            >
              Прототип
            </a>
          ) : (
            <Go to="/">На главную</Go>
          )}
        </div>
      </nav>
      <main className="docs-layout">
        <aside>
          <p className="eyebrow">Документация</p>
          {Object.keys(sources)
            .sort()
            .map((path) => {
              const base = path.split("/").at(-1)!.replace(".md", "");
              const section =
                path.split("/")[path.startsWith("../../docs/") ? 3 : 2];
              return (
                <button
                  key={path}
                  className={current === path ? "active" : ""}
                  onClick={() => setParams({ file: path })}
                >
                  {section} / {names[base] || base}
                </button>
              );
            })}
          <Federation />
        </aside>
        <article className="panel docs-article">
          {bundle && (
            <header className="print-cover">
              <div className="split">
                <Brand />
                <Federation />
              </div>
              <h1>Сцепка</h1>
              <p>Интерфейс, локальный запуск и проверка платформы ФСП</p>
            </header>
          )}
          <div className="split">
            <span className="eyebrow">Сцепка / документация</span>
            <Button secondary onClick={() => window.print()}>
              <FileDown size={16} />
              Печать / PDF
            </Button>
          </div>
          <ErrorBox error={error} />
          {!documents.length && !error && <Loading />}
          {documents.map((d) => (
            <section key={d.path} className="document-section">
              <Markdown
                source={d.source}
                diagrams
                resolveLink={(href) => link(d.path, href)}
              />
            </section>
          ))}
        </article>
      </main>
    </div>
  );
}
