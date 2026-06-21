import structlog
from typing import Optional, Dict, Any

logger = structlog.get_logger(__name__)

class GridManager:
    def __init__(self, config: Any, order_router: Any):
        """
        :param config: Об'єкт конфігурації (DynDCAConfig)
        :param order_router: Модуль для відправки ордерів на біржу/PaperTrader
        """
        self.config = config
        self.router = order_router
        
        # Внутрішній стан позиції
        self.current_position_size: float = 0.0
        self.average_entry_price: float = 0.0
        self.active_tp_order_id: Optional[str] = None

    def on_dca_order_filled(self, fill_price: float, fill_qty: float, side: str):
        """
        Обробник події виконання лімітного ордера сітки.
        """
        logger.info("DCA order filled", price=fill_price, qty=fill_qty, side=side)
        
        # 1. Перерахунок середньої ціни входу
        total_cost = (self.average_entry_price * self.current_position_size) + (fill_price * fill_qty)
        self.current_position_size += fill_qty
        self.average_entry_price = total_cost / self.current_position_size
        
        logger.info("Position updated", new_avg_entry=self.average_entry_price, total_size=self.current_position_size)

        # 2. Скасування старого Take Profit
        if self.active_tp_order_id:
            self.router.cancel_order(self.active_tp_order_id)
            self.active_tp_order_id = None
            logger.debug("Previous TP order cancelled")

        # 3. Встановлення нового Take Profit
        self._place_take_profit(side)

    def _place_take_profit(self, position_side: str):
        """
        Розраховує та виставляє лімітний ордер на закриття з урахуванням NO-LOSS правила.
        """
        if self.current_position_size <= 0:
            return

        tp_offset = self.config.total_tp_pct / 100.0

        if position_side == "buy":
            # Для лонга: ціна закриття (sell) вища за середню
            tp_price = self.average_entry_price * (1.0 + tp_offset)
            close_side = "sell"
        elif position_side == "sell":
            # Для шорта: ціна закриття (buy) нижча за середню
            tp_price = self.average_entry_price * (1.0 - tp_offset)
            close_side = "buy"
        else:
            logger.error("Unknown position side", side=position_side)
            return

        tp_price = round(tp_price, 2) # Округлення до тіка (в майбутньому брати з exchange rules)

        # Перевірка на NO-LOSS (Sanity Check)
        if position_side == "buy" and tp_price <= self.average_entry_price:
            logger.error("NO-LOSS VIOLATION DETECTED: TP price is below entry!", tp_price=tp_price, entry=self.average_entry_price)
            return
        if position_side == "sell" and tp_price >= self.average_entry_price:
            logger.error("NO-LOSS VIOLATION DETECTED: TP price is above entry!", tp_price=tp_price, entry=self.average_entry_price)
            return

        # Відправка ордера
        order = self.router.place_limit_order(
            side=close_side,
            price=tp_price,
            qty=self.current_position_size,
            reduce_only=True # Гарантуємо, що це тільки закриття позиції
        )
        
        if order and "order_id" in order:
            self.active_tp_order_id = order["order_id"]
            logger.info("New Take Profit placed", target_price=tp_price, qty=self.current_position_size, order_id=self.active_tp_order_id)

    def on_tp_order_filled(self):
        """
        Обробник події, коли Take Profit повністю спрацював.
        """
        logger.info("TAKE PROFIT HIT. Position closed successfully.", profit_pct=self.config.total_tp_pct)
        self.current_position_size = 0.0
        self.average_entry_price = 0.0
        self.active_tp_order_id = None
        # Тут також можна викликати логіку початку нової сітки
