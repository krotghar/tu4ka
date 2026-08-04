"""Тесты правового пакета (L1): раздача /legal/* и /en/legal/*, содержимое."""

import re
from pathlib import Path

import pytest

LEGAL_DIR = Path(__file__).resolve().parent.parent / "server" / "static" / "legal"

RU_SLUGS = ["terms", "privacy", "license"]
EN_SLUGS = ["terms", "privacy", "license"]

ALL_LEGAL_FILES = [
    "terms.ru.html", "terms.en.html",
    "privacy.ru.html", "privacy.en.html",
    "license.ru.html", "license.en.html",
]

# Абсолютные URL, на которые страницам можно ссылаться, не будучи "внешним доменом".
# www.w3.org — не запрос, а namespace URI инлайновой SVG-фавиконки (как в index.html).
ALLOWED_EXTERNAL_HOSTS = ("creativecommons.org", "www.w3.org")


@pytest.mark.parametrize("slug", RU_SLUGS)
def test_legal_ru_pages_serve_200_html(client, slug):
    r = client.get(f"/legal/{slug}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize("slug", EN_SLUGS)
def test_legal_en_pages_serve_200_html(client, slug):
    r = client.get(f"/en/legal/{slug}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


def test_unknown_slug_is_404(client):
    assert client.get("/legal/nonexistent").status_code == 404
    assert client.get("/en/legal/nonexistent").status_code == 404


def test_unknown_lang_is_404(client):
    # только /legal/* (ru) и /en/legal/* (en) — /fr/legal/terms не существует
    assert client.get("/fr/legal/terms").status_code == 404


@pytest.mark.parametrize("attempt", [
    "..%2f..%2fmain.py",
    "..%2Fmain.py",
    "terms.ru.html",  # прямое имя файла в whitelist не значится как slug
])
def test_path_traversal_attempts_are_404(client, attempt):
    r = client.get(f"/legal/{attempt}")
    assert r.status_code == 404


@pytest.mark.parametrize("filename", ALL_LEGAL_FILES)
def test_pages_are_versioned(filename):
    text = (LEGAL_DIR / filename).read_text(encoding="utf-8")
    assert re.search(r"Version|Версия", text)


@pytest.mark.parametrize("filename", ["terms.ru.html", "terms.en.html",
                                       "privacy.ru.html", "privacy.en.html"])
def test_terms_and_privacy_name_the_operator_contact(filename):
    text = (LEGAL_DIR / filename).read_text(encoding="utf-8")
    assert "krotghar@gmail.com" in text


@pytest.mark.parametrize("filename", ["terms.ru.html", "terms.en.html",
                                       "license.ru.html", "license.en.html"])
def test_terms_and_license_state_indicative_measurements(filename):
    text = (LEGAL_DIR / filename).read_text(encoding="utf-8")
    assert re.search(r"[Ii]ndicative", text)


@pytest.mark.parametrize("filename", ["license.ru.html", "license.en.html"])
def test_license_pages_link_cc_by(filename):
    text = (LEGAL_DIR / filename).read_text(encoding="utf-8")
    assert "creativecommons.org/licenses/by/4.0/" in text


def test_terms_pages_contain_the_license_grant():
    for filename in ("terms.ru.html", "terms.en.html"):
        text = (LEGAL_DIR / filename).read_text(encoding="utf-8")
        assert 'id="grant"' in text


def test_privacy_pages_contain_the_deletion_contract():
    for filename in ("privacy.ru.html", "privacy.en.html"):
        text = (LEGAL_DIR / filename).read_text(encoding="utf-8")
        assert 'id="deletion"' in text


def test_dashboard_links_only_existing_legal_routes(client):
    text = client.get("/").text
    for match in re.findall(r'href="(/legal/[a-z-]+)"', text):
        slug = match.rsplit("/", 1)[-1]
        assert slug in RU_SLUGS, f"дашборд ссылается на несуществующий /legal/{slug}"


@pytest.mark.parametrize("filename", ALL_LEGAL_FILES)
def test_no_external_domains_besides_allowlist(filename):
    text = (LEGAL_DIR / filename).read_text(encoding="utf-8")
    for url in re.findall(r'https?://([a-zA-Z0-9.-]+)', text):
        assert any(url == host or url.endswith("." + host) for host in ALLOWED_EXTERNAL_HOSTS), (
            f"{filename} ссылается на внешний домен {url}, не входящий в allowlist"
        )
