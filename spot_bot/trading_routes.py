from fastapi import FastAPI

from .spot_api import router as spot_router
from .grid_api import router as grid_router


def register_trading_routes(app: FastAPI) -> None:
    app.include_router(spot_router)
    app.include_router(grid_router)
