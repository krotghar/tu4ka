"""Публичные страницы: правовой пакет (S8 добавит сюда /d/<slug>)."""

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()

_PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEGAL_DIR = os.path.join(_PACKAGE_DIR, "static", "legal")

# Whitelist (lang, slug) -> имя файла — а не склейка пути, чтобы запрос вида
# /legal/../../etc/passwd не превращался в вопрос везения.
_LEGAL_FILES = {
    ("ru", "terms"): "terms.ru.html",
    ("ru", "privacy"): "privacy.ru.html",
    ("ru", "license"): "license.ru.html",
    ("en", "terms"): "terms.en.html",
    ("en", "privacy"): "privacy.en.html",
    ("en", "license"): "license.en.html",
}


def _legal_response(lang: str, slug: str) -> FileResponse:
    filename = _LEGAL_FILES.get((lang, slug))
    if filename is None:
        raise HTTPException(status_code=404)
    return FileResponse(os.path.join(LEGAL_DIR, filename), media_type="text/html")


@router.get("/legal/{slug}", include_in_schema=False)
def legal_ru(slug: str):
    return _legal_response("ru", slug)


@router.get("/en/legal/{slug}", include_in_schema=False)
def legal_en(slug: str):
    return _legal_response("en", slug)
