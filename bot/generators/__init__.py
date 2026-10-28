"""Генератори заявки. Реекспорт ліниво (PEP 562): `bot.generators.cv_engine`
тягне `bot.apply.candidate`, а той під час імпорту вимагає street/zip/birth_date
з profile.yaml. Контрактний клієнт цих полів не має, і без лінивості навіть
`import bot.generators.engagement` падав би з KeyError."""
from importlib import import_module

_EXPORTS = {
    "render_application": "bot.generators.cv_engine",
    "build_cv_data": "bot.generators.cv_engine",
    "intake": "bot.generators.intake",
    "JobData": "bot.generators.intake",
    "tailor": "bot.generators.tailor",
    "TailoredData": "bot.generators.tailor",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    try:
        value = getattr(import_module(module), name)
    except ImportError:
        # tailor опційний: без його залежностей реекспорт дає None, як і раніше
        if module != "bot.generators.tailor":
            raise
        value = None
    globals()[name] = value
    return value
