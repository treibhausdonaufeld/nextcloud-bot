"""Per-request language handling for the web UI.

The language is chosen from the `lang` cookie (set by the language switcher)
or the Accept-Language header. Handlers run synchronously in a threadpool,
so `activate` sets the gettext ContextVar for code called inside the handler,
and `template_context` additionally passes the translation callables into
the template explicitly (template rendering happens outside the handler's
context).
"""

import gettext as gettext_module

from ravyn import Request

from app.settings import LOCALES_DIR, available_languages, set_language, settings

_catalogs: dict[str, gettext_module.NullTranslations] = {}


def get_catalog(language: str) -> gettext_module.NullTranslations:
    if language not in _catalogs:
        if language == "en":
            _catalogs[language] = gettext_module.NullTranslations()
        else:
            _catalogs[language] = gettext_module.translation(
                "messages",
                localedir=str(LOCALES_DIR),
                languages=[language],
                fallback=True,
            )
    return _catalogs[language]


def get_language(request: Request) -> str:
    lang = request.cookies.get("lang", "")
    if lang in available_languages:
        return lang

    accept = request.headers.get("accept-language", "")
    for part in accept.split(","):
        code = part.split(";")[0].strip()[0:2].lower()
        if code in available_languages:
            return code

    return settings.default_language


def activate(request: Request) -> str:
    """Set the request language for `_()` calls inside the handler."""
    lang = get_language(request)
    set_language(lang)
    return lang


def template_context(request: Request, **extra) -> dict:
    """Base context for templates, including the translation callables."""
    lang = activate(request)
    catalog = get_catalog(lang)
    return {
        "_": catalog.gettext,
        "_n": catalog.ngettext,
        "lang": lang,
        "available_languages": available_languages,
        "settings": settings,
        "current_path": request.url.path,
        **extra,
    }
