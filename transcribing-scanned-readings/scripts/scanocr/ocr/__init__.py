"""OCR engines. Unlimited-OCR is the default; add new ones to ENGINES."""
from .base import Block, OCREngine, PageOutput
from .unlimited import UnlimitedOCR

ENGINES = {UnlimitedOCR.name: UnlimitedOCR}
DEFAULT_ENGINE = UnlimitedOCR.name


def get_engine(name=DEFAULT_ENGINE, **kwargs) -> OCREngine:
    try:
        return ENGINES[name](**kwargs)
    except KeyError:
        raise ValueError(f'Unknown OCR engine {name!r}. Available: {", ".join(ENGINES)}') from None


__all__ = ['Block', 'OCREngine', 'PageOutput', 'UnlimitedOCR', 'ENGINES', 'DEFAULT_ENGINE', 'get_engine']
