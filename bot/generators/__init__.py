from bot.generators.cv_engine import render_application, build_cv_data
from bot.generators.intake import intake, JobData

try:
    from bot.generators.tailor import tailor, TailoredData
except ImportError:
    tailor = None  # type: ignore
    TailoredData = None  # type: ignore

__all__ = [
    "render_application", "build_cv_data",
    "intake", "JobData",
    "tailor", "TailoredData",
]
