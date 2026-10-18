"""Small explicit RU/EN catalogs shared by both frontends."""
import json
import locale
from functools import lru_cache
from importlib.resources import files

from .models import Issue


def default_language() -> str:
    language = locale.getlocale()[0] or "en"
    return "ru" if language.lower().startswith("ru") else "en"


@lru_cache(maxsize=2)
def messages(language: str) -> dict[str, str]:
    if language not in ("ru", "en"):
        raise ValueError("Unsupported language")
    return json.loads(files("wasdify").joinpath("locales", language + ".json").read_text("utf-8"))


class Translator:
    def __init__(self, language: str | None = None):
        self.language = language or default_language()
        self.messages = messages(self.language)

    def __call__(self, key: str, **params: object) -> str:
        return self.messages[key].format(**params)

    def issue(self, issue: Issue) -> str:
        return self(issue.code, **issue.params)

