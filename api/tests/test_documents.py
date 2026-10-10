from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from app.documents import render_pdf


def test_pdf_has_cyrillic_and_no_hidden_contacts():
    brand = Path(__file__).resolve().parents[2] / "brand"
    value = {
        "user_id": "test",
        "first_name": "Иван",
        "last_name": "Петров",
        "grade_confirmed": False,
        "about": "Разработка серверных приложений " * 400,
        "skills": [{"name": "Python", "self_level": 3, "confirmed_level": None}],
        "links": [],
        "category": None,
    }
    document = render_pdf(value, brand)
    reader = PdfReader(BytesIO(document))
    text = " ".join(page.extract_text() for page in reader.pages)
    assert len(reader.pages) > 1
    assert "Иван" in text and "Петров" in text and "Python" in text
    assert "example.org" not in text
    assert "не подтверждён" in text


def test_user_text_cannot_create_pdf_network_resource():
    brand = Path(__file__).resolve().parents[2] / "brand"
    document = render_pdf(
        {
            "user_id": "test",
            "grade_confirmed": False,
            "about": '<img src="http://127.0.0.1:9/private">',
            "skills": [],
            "links": [],
            "category": None,
        },
        brand,
    )
    assert document.startswith(b"%PDF")
