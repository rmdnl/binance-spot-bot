from decimal import Decimal
from dataclasses import dataclass

D = Decimal

@dataclass(frozen=True)
class GridLevel:
    index: int
    price: D

@dataclass(frozen=True)
class GridPreview:
    lower_price: D
    upper_price: D
    grids: int
    grid_type: str
    levels: tuple[GridLevel, ...]
    step_pct: D

def build_grid(lower_price: D, upper_price: D, grids: int, grid_type: str = "arithmetic") -> GridPreview:
    lo, hi = D(str(lower_price)), D(str(upper_price))
    if lo <= 0 or hi <= lo: raise ValueError("invalid grid range")
    if grids < 2: raise ValueError("grids must be at least 2")
    kind = grid_type.lower().strip()
    if kind == "arithmetic":
        step = (hi-lo)/D(grids-1); prices=[lo+step*i for i in range(grids)]
    elif kind == "geometric":
        ratio=(hi/lo)**(D(1)/D(grids-1)); prices=[lo*(ratio**i) for i in range(grids)]
    else: raise ValueError("grid_type must be arithmetic or geometric")
    prices[-1]=hi
    levels=tuple(GridLevel(i,p) for i,p in enumerate(prices))
    return GridPreview(lo,hi,grids,kind,levels,(levels[1].price/levels[0].price-D(1))*D(100))

def grid_profit_pct(buy_price: D, sell_price: D) -> D:
    buy,sell=D(str(buy_price)),D(str(sell_price))
    if buy <= 0 or sell <= buy: raise ValueError("sell price must be greater than buy price")
    return (sell/buy-D(1))*D(100)
