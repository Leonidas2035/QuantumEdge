#!/usr/bin/env python
from quantum_edge_core.bot.service import TradingBot
from quantum_edge_core.logging_setup import setup_logging
from quantum_edge_core.utils.async_runner import run_service


def main():
    setup_logging()
    bot = TradingBot()
    run_service(bot._runner_wrapper())


if __name__ == "__main__":
    main()
