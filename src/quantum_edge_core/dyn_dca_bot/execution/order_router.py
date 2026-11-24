import ccxt
import structlog
import uuid
from typing import Dict, Any
import os

logger = structlog.get_logger(__name__)

class OrderRouter:
    def __init__(self, config: Any = None):
        self.config = config
        use_testnet = os.getenv("USE_TESTNET", "true").lower() == "true"
        if use_testnet:
            self.api_key = os.getenv("BINGX_TESTNET_API_KEY", "")
            self.api_secret = os.getenv("BINGX_TESTNET_SECRET", "")
        else:
            self.api_key = os.getenv("BINGX_API_KEY", "")
            self.api_secret = os.getenv("BINGX_SECRET", "")
        
        self.exchange = ccxt.bingx({
            'apiKey': self.api_key,
            'secret': self.api_secret,
            'options': {'defaultType': 'swap'},
            'enableRateLimit': True,
        })
        
        if use_testnet:
            self.exchange.set_sandbox_mode(True)

    def _get_ccxt_symbol(self) -> str:
        symbol = "BTCUSDT"
        if self.config and hasattr(self.config, "symbol"):
            symbol = self.config.symbol
        base = symbol.replace("USDT", "")
        return f"{base}/USDT:USDT"

    def place_limit_order(self, side: str, price: float, qty: float, reduce_only: bool = False, position_side: str = "") -> Dict[str, Any]:
        has_key = bool(self.api_key)
        has_secret = bool(self.api_secret)
        logger.debug("place_limit_order called", side=side, price=price, qty=qty, reduce_only=reduce_only, has_key=has_key)
        
        if not has_key or not has_secret:
            order_id = f"ord_{uuid.uuid4().hex[:8]}"
            logger.debug("Routing order to exchange (stub)", side=side, price=price, qty=qty, reduce_only=reduce_only)
            return {"order_id": order_id, "status": "NEW"}
        
        if not position_side:
            position_side = "LONG" if side.lower() == "buy" else "SHORT"
            
        params = {"positionSide": position_side}
        if reduce_only:
            params["reduceOnly"] = True

        ccxt_symbol = self._get_ccxt_symbol()
        try:
            order = self.exchange.create_order(
                symbol=ccxt_symbol,
                type='limit',
                side=side.lower(),
                amount=qty,
                price=price,
                params=params
            )
            order_id = order['id']
            logger.info("Real order placed", side=side, price=price, qty=qty, order_id=order_id, symbol=ccxt_symbol)
            return {"order_id": str(order_id), "status": "NEW", "reduce_only": reduce_only}
        except Exception as e:
            err_msg = str(e)
            if reduce_only and "ReduceOnly" in err_msg:
                logger.warning("reduceOnly rejected, retrying without it", error=err_msg)
                params.pop("reduceOnly", None)
                try:
                    order = self.exchange.create_order(
                        symbol=ccxt_symbol,
                        type='limit',
                        side=side.lower(),
                        amount=qty,
                        price=price,
                        params=params
                    )
                    order_id = order['id']
                    logger.info("Real order placed (no reduceOnly)", side=side, price=price, qty=qty, order_id=order_id, symbol=ccxt_symbol)
                    return {"order_id": str(order_id), "status": "NEW", "reduce_only": False}
                except Exception as e2:
                    logger.error("Order placement exception (retry failed)", error=str(e2))
                    return {}
            else:
                logger.error("Order placement exception", error=err_msg)
                return {}

    def cancel_order(self, order_id: str):
        if not self.api_key or not self.api_secret:
            logger.debug("Routing cancel request (stub)", order_id=order_id)
            return
            
        ccxt_symbol = self._get_ccxt_symbol()
        try:
            self.exchange.cancel_order(
                id=order_id,
                symbol=ccxt_symbol
            )
            logger.info("Real order cancelled", order_id=order_id, symbol=ccxt_symbol)
        except Exception as e:
            logger.error("Cancel exception", error=str(e))
