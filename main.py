"""Совместимая корневая точка входа для Bothost и локального запуска."""
from __future__ import annotations

import asyncio

from app.runtime import main as runtime_main


if __name__ == "__main__":
    asyncio.run(runtime_main())
