"""
nanobot - A lightweight AI agent framework
"""

__version__ = "0.1.4.post6"
__logo__ = "🐈"
__all__ = ["Nanobot", "RunResult", "__version__", "__logo__"]


def __getattr__(name: str):
    if name in {"Nanobot", "RunResult"}:
        from nanobot.nanobot import Nanobot, RunResult
        return {"Nanobot": Nanobot, "RunResult": RunResult}[name]
    raise AttributeError(name)
