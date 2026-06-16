#!/usr/bin/env python
from quantum_edge_core.bot.service import BotService as TradingBot
from quantum_edge_core.utils.async_runner import run_service
from quantum_edge_core.logging_setup import setup_logging


def main():
    setup_logging()
    bot = TradingBot()
    run_service(bot.run())


if __name__ == "__main__":
    main()
