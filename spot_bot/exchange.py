from __future__ import annotations
from decimal import Decimal
import ccxt
from .config import get_settings
from .models import Environment, OrderRequest, OrderType
D=Decimal

class ExchangeSafetyError(RuntimeError): pass

class BinanceSpot:
    def __init__(self, environment: Environment):
        self.environment=environment
        settings=get_settings(); key,secret=settings.credentials(environment.value)
        if not key or not secret: raise ExchangeSafetyError(f"Binance {environment.value} credentials are missing")
        self.exchange=ccxt.binance({'apiKey':key,'secret':secret,'enableRateLimit':True,'options':{'defaultType':'spot'}})
        self.exchange.set_sandbox_mode(environment==Environment.TESTNET)
        self.exchange.load_markets()
    def market(self,symbol):
        m=self.exchange.market(symbol)
        if m.get('spot') is not True: raise ExchangeSafetyError(f"{symbol} is not Spot")
        return m
    def balance(self): return self.exchange.fetch_balance()
    def ticker(self,symbol): self.market(symbol); return self.exchange.fetch_ticker(symbol)
    def open_orders(self,symbol=None): return self.exchange.fetch_open_orders(symbol)
    def recent_trades(self,symbol=None,limit=50): return self.exchange.fetch_my_trades(symbol,limit=limit)
    def create_order(self, request: OrderRequest):
        settings=get_settings()
        if not settings.allow_order_submission: raise ExchangeSafetyError("Order submission is disabled")
        if request.environment!=self.environment: raise ExchangeSafetyError("Environment mismatch")
        self.market(request.symbol)
        if request.amount<=0: raise ExchangeSafetyError("Amount must be positive")
        if request.order_type!=OrderType.MARKET and (request.price is None or request.price<=0): raise ExchangeSafetyError("Positive limit price required")
        params={}
        if request.post_only or request.order_type==OrderType.LIMIT_MAKER: params['postOnly']=True
        order_type='limit' if request.order_type==OrderType.LIMIT_MAKER else request.order_type.value
        return self.exchange.create_order(request.symbol,order_type,request.side.value,float(request.amount),float(request.price) if request.price else None,params)
