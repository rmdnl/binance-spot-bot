from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN


@dataclass(frozen=True)
class GridLevel:
    index: int
    price: Decimal


@dataclass(frozen=True)
class GridPreview:
    levels: tuple[GridLevel, ...]
    step_pct_min: Decimal
    step_pct_max: Decimal


def _floor(value: Decimal, places: int = 12) -> Decimal:
    quantum = Decimal(1).scaleb(-places)
    return value.quantize(quantum, rounding=ROUND_DOWN)


def build_grid(lower: Decimal, upper: Decimal, grids: int, grid_type: str) -> GridPreview:
    if lower <= 0 or upper <= lower:
        raise ValueError("lower must be positive and below upper")
    if grids < 2:
        raise ValueError("grids must be at least 2")
    kind = grid_type.strip().lower()
    levels: list[GridLevel] = []
    if kind == "arithmetic":
        step = (upper - lower) / Decimal(grids - 1)
        levels = [GridLevel(i, _floor(lower + step * i)) for i in range(grids)]
    elif kind == "geometric":
        ratio = (upper / lower) ** (Decimal(1) / Decimal(grids - 1))
        levels = [GridLevel(i, _floor(lower * (ratio ** i))) for i in range(grids)]
    else:
        raise ValueError("grid_type must be arithmetic or geometric")
    pcts = [((levels[i + 1].price / levels[i].price) - 1) * 100 for i in range(len(levels) - 1)]
    return GridPreview(tuple(levels), min(pcts), max(pcts))
