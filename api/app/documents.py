import html
import multiprocessing
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urlparse

from pypdf import PdfReader


def _extract(data, connection):
    try:
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
            resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
        except ImportError:
            pass
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise ValueError("Зашифрованный PDF не поддерживается")
        if len(reader.pages) > 100:
            raise ValueError("PDF содержит слишком много страниц")
        pieces = []
        length = 0
        for page in reader.pages:
            value = page.extract_text() or ""
            length += len(value)
            if length > 200000:
                raise ValueError("Извлечённый текст превышает лимит")
            pieces.append(value)
        text = "\n".join(pieces)
        if not text.strip():
            raise ValueError("PDF не содержит текста; для скана нужен OCR")
        connection.send((True, text))
    except Exception as error:
        connection.send(
            (
                False,
                str(error)
                if isinstance(error, ValueError)
                else "Повреждённый или неподдерживаемый PDF",
            )
        )
    finally:
        connection.close()


def extract_text(data):
    parent, child = multiprocessing.get_context("spawn").Pipe(duplex=False)
    process = multiprocessing.get_context("spawn").Process(target=_extract, args=(data, child))
    process.start()
    child.close()
    try:
        if not parent.poll(15):
            raise ValueError("Разбор PDF превысил ограничение времени")
        try:
            success, value = parent.recv()
        except EOFError as error:
            raise ValueError("Разбор PDF превысил ограничение ресурсов") from error
        if not success:
            raise ValueError(value)
        return value
    finally:
        if process.is_alive():
            process.terminate()
        process.join()
        parent.close()


def render_pdf(card, brand_dir):
    from weasyprint import HTML
    from weasyprint.urls import URLFetcher

    root = Path(brand_dir).resolve()

    class BrandFetcher(URLFetcher):
        def fetch(self, url, headers=None):
            parsed = urlparse(url)
            if parsed.scheme != "file":
                raise ValueError("Внешние ресурсы PDF запрещены")
            path = Path(unquote(parsed.path)).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError("Ресурс PDF вне брендовых материалов")
            return super().fetch(path.as_uri(), headers)

    def escape(value):
        return html.escape(str(value or ""))

    name = " ".join(
        str(card.get(field) or "") for field in ("last_name", "first_name", "middle_name")
    ).strip()
    title = name or "Профиль кандидата"
    logo = (root / "assets/logo/logo-full-on-light.svg").as_uri()
    tokens = (root / "dist/tokens.css").as_uri()
    regular = (root / "dist/fonts/JetBrainsMono-Regular.woff2").as_uri()
    bold = (root / "dist/fonts/JetBrainsMono-Bold.woff2").as_uri()
    accent = (root / "dist/fonts/TDAText-Regular.woff2").as_uri()
    pieces = [
        f'<img class="logo" src="{logo}"><h1>{escape(title)}</h1>',
        f'<p class="status">Грейд {"подтверждён тестом" if card.get("grade_confirmed") else "не подтверждён"}</p>',
    ]
    if card.get("category"):
        pieces.append(f"<h2>Категория</h2><p>{escape(card['category'].get('name'))}</p>")
    if "city" in card:
        formats = {"office": "Офис", "hybrid": "Гибридный формат", "remote": "Удалённая работа"}
        conditions = " · ".join(
            str(value)
            for value in (card.get("city"), formats.get(card.get("work_format")))
            if value
        )
        if conditions:
            pieces.append(f"<h2>Условия работы</h2><p>{escape(conditions)}</p>")
    if "experience_months" in card:
        pieces.append(f"<p>Стаж: {escape(card['experience_months'])} месяцев</p>")
    if card.get("about"):
        pieces.append(f'<h2>О себе</h2><p class="text">{escape(card["about"])}</p>')
    if card.get("skills"):
        pieces.append("<h2>Навыки</h2><ul>")
        for skill in card["skills"]:
            level = (
                "не проверен"
                if skill.get("confirmed_level") is None
                else f"подтверждённый уровень {skill['confirmed_level']}"
            )
            pieces.append(
                f"<li>{escape(skill['name'])}: заявленный уровень {escape(skill['self_level'])}; {escape(level)}</li>"
            )
        pieces.append("</ul>")
    preference = card.get("preferences") or {}
    if preference.get("salary_min") or preference.get("salary_max"):
        pieces.append(
            f"<h2>Зарплатные ожидания</h2><p>{escape(preference.get('salary_min'))}–{escape(preference.get('salary_max'))} ₽ в месяц до вычета НДФЛ</p>"
        )
    if card.get("contacts"):
        pieces.append("<h2>Контакты</h2>")
        pieces.extend(f"<p>{escape(value)}</p>" for value in card["contacts"].values() if value)
    if card.get("links"):
        pieces.append("<h2>Ссылки</h2>")
        pieces.extend(
            f'<p class="url">{escape(link.get("label") or link["kind"])}: {escape(link["url"])}</p>'
            for link in card["links"]
        )
    if card.get("fsp_achievements"):
        pieces.append("<h2>Достижения ФСП</h2>")
        pieces.extend(
            f"<p>{escape(item.get('event_title', 'Событие ФСП'))}: место {escape(item.get('place'))} из {escape(item.get('participants'))}{' (демонстрационные данные)' if item.get('demo') else ''}</p>"
            for item in card["fsp_achievements"]
        )
    css = f'''@font-face {{font-family: ProfileMono; src: url("{regular}");}}
    @font-face {{font-family: ProfileMono; src: url("{bold}"); font-weight: bold;}}
    @font-face {{font-family: ProfileAccent; src: url("{accent}");}}
    @page {{size: A4; margin: 18mm; @bottom-right {{content: counter(page); font-family: ProfileMono; font-size: 9pt;}}}}
    body {{font-family: ProfileMono; font-size: 10pt; color: var(--fsp-color-text); background: var(--fsp-color-surface);}}
    h1 {{font-size: 22pt; color: var(--fsp-color-primary);}} h2 {{font-size: 13pt; break-after: avoid; color: var(--fsp-color-primary);}}
    p, li {{line-height: 1.6; overflow-wrap: anywhere;}} .text {{white-space: pre-wrap;}} .logo {{width: 65mm;}}
    .status {{border-bottom: 2px solid var(--fsp-color-primary); padding-bottom: 4mm;}} .url {{word-break: break-all;}}
    '''
    document = f'<!doctype html><html lang="ru" data-theme="light"><head><meta charset="utf-8"><link rel="stylesheet" href="{tokens}"><style>{css}</style></head><body>{"".join(pieces)}</body></html>'
    return HTML(string=document, url_fetcher=BrandFetcher(allowed_protocols={"file"})).write_pdf()
