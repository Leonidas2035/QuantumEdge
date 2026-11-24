"""
================================================================
QuantumEdge — COMPOSITE SIGNAL ENGINE (4 + 1 Сигнальні Осі)
================================================================
Розраховує інтегральний скор ринку (-100...+100) на основі
незалежних джерел сигналу:
  1. ТРЕНД: EMA Ribbon (9, 21, 50) + ADX (сила тренду)
  2. МОМЕНТУМ: MACD + RSI (прискорення, зони перекупленості)
  3. ВОЛАТИЛЬНІСТЬ: Bollinger Bands (свічка в діапазоні, squeeze) + ATR
  4. ОБ'ЄМ/ПОТІК: OBV + Зміна Open Interest + Funding Rate
  5. КАРТА ЛІКВІДАЦІЙ: Тяжіння найближчих великих зон (з liqmap_latest.json)
================================================================
"""
from __future__ import annotations
import json
import numpy as np
import pandas as pd
from pathlib import Path

from ta.trend import ADXIndicator, MACD
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands, AverageTrueRange
from ta.volume import OnBalanceVolumeIndicator


def calculate_composite_score(df: pd.DataFrame, funding_rate: float = 0.0,
                              oi_series: list | None = None, liq_zones: list | None = None) -> dict:
    """
    df: DataFrame з колонками ['open', 'high', 'low', 'close', 'volume']
    funding_rate: поточна ставка фінансування (напр. 0.0001)
    oi_series: список (timestamp, open_interest) для оцінки зміни OI
    liq_zones: список зон з liqmap_latest.json для розрахунку 5-ї осі
    
    Повертає dict з детальними очками по кожній осі та загальним score (-100...+100).
    """
    if len(df) < 55:
        return {"score": 0, "trend": 0, "momentum": 0, "volatility": 0, "volume_flow": 0, "liq_map": 0}

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # --------------------------------------------------------
    # 1. ТРЕНД (EMA-стрічка 9/21/50 + ADX, max +-25)
    # --------------------------------------------------------
    ema9 = close.ewm(span=9, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    
    adx_ind = ADXIndicator(high, low, close, window=14)
    adx = adx_ind.adx().iloc[-1]
    
    trend_score = 0.0
    # Перевіряємо порядок EMA стрічки
    if ema9.iloc[-1] > ema21.iloc[-1] > ema50.iloc[-1]:
        trend_score = 15.0  # бичачий тренд
    elif ema9.iloc[-1] < ema21.iloc[-1] < ema50.iloc[-1]:
        trend_score = -15.0  # ведмежий тренд

    # Коригування по ADX (сила тренду)
    if adx > 25:
        # Сильний тренд посилює скор
        trend_score *= 1.66
    elif adx < 20:
        # Слабкий тренд (флет) послаблює скор
        trend_score *= 0.5
        
    trend_score = np.clip(trend_score, -25.0, 25.0)

    # --------------------------------------------------------
    # 2. МОМЕНТУМ (MACD + RSI, max +-25)
    # --------------------------------------------------------
    # MACD
    macd_ind = MACD(close, window_fast=12, window_slow=26, window_sign=9)
    macd_diff = macd_ind.macd_diff().iloc[-1]
    macd_line = macd_ind.macd().iloc[-1]
    
    macd_contrib = 0.0
    if macd_diff > 0:
        macd_contrib = 12.5 if macd_line > 0 else 6.25
    else:
        macd_contrib = -12.5 if macd_line < 0 else -6.25

    # RSI
    rsi_ind = RSIIndicator(close, window=14)
    rsi_val = rsi_ind.rsi().iloc[-1]
    
    rsi_contrib = 0.0
    if rsi_val > 50:
        # Бичачий моментум, максимум при RSI=70
        rsi_contrib = 12.5 * (min(rsi_val, 70.0) - 50.0) / 20.0
    else:
        # Ведмежий моментум, максимум при RSI=30
        rsi_contrib = -12.5 * (50.0 - max(rsi_val, 30.0)) / 20.0
        
    momentum_score = np.clip(macd_contrib + rsi_contrib, -25.0, 25.0)

    # --------------------------------------------------------
    # 3. ВОЛАТИЛЬНІСТЬ (Bollinger Bands + ATR, max +-25)
    # --------------------------------------------------------
    bb = BollingerBands(close, window=20, window_dev=2)
    bb_high = bb.bollinger_hband()
    bb_low = bb.bollinger_lband()
    bb_mid = bb.bollinger_mavg()
    
    last_close = close.iloc[-1]
    last_bb_high = bb_high.iloc[-1]
    last_bb_low = bb_low.iloc[-1]
    last_bb_mid = bb_mid.iloc[-1]
    
    # Співвідношення %B
    bb_width = last_bb_high - last_bb_low
    pct_b = (last_close - last_bb_low) / bb_width if bb_width > 0 else 0.5
    
    vol_contrib = 0.0
    if pct_b > 0.8:
        vol_contrib = 12.5 * (pct_b - 0.8) / 0.2
    elif pct_b < 0.2:
        vol_contrib = -12.5 * (0.2 - pct_b) / 0.2

    # Виявлення стиснення (BB Squeeze)
    bandwidths = (bb_high - bb_low) / bb_mid
    current_bandwidth = bandwidths.iloc[-1]
    
    # Шукаємо 20% перцентиль за останні 100 свічок
    is_squeeze = False
    if len(bandwidths) >= 100:
        recent_bandwidths = bandwidths.iloc[-100:]
        percentile_20 = np.percentile(recent_bandwidths, 20)
        is_squeeze = current_bandwidth <= percentile_20
        
    squeeze_contrib = 0.0
    if is_squeeze:
        # При стисненні очікуємо пробій у бік тренду середніх
        if last_close > last_bb_mid:
            squeeze_contrib = 12.5
        else:
            squeeze_contrib = -12.5
            
    volatility_score = np.clip(vol_contrib + squeeze_contrib, -25.0, 25.0)

    # --------------------------------------------------------
    # 4. ОБ'ЄМ/ПОТІК (OBV + Open Interest + Funding Rate, max +-25)
    # --------------------------------------------------------
    # OBV
    obv_ind = OnBalanceVolumeIndicator(close, volume)
    obv = obv_ind.on_balance_volume()
    obv_ema = obv.ewm(span=5, adjust=False).mean()
    
    obv_contrib = 8.3 if obv.iloc[-1] > obv_ema.iloc[-1] else -8.3

    # Open Interest зміна за останні 4 години
    oi_contrib = 0.0
    if oi_series and len(oi_series) >= 5:
        # Отримуємо поточне OI та OI 4 години тому
        current_ts = df.index[-1] if isinstance(df.index[-1], (int, float)) else df['timestamp'].iloc[-1]
        
        # Наближено вибираємо значення OI
        matching_oi = [x[1] for x in oi_series if x[0] <= current_ts]
        if len(matching_oi) >= 5:
            current_oi = matching_oi[-1]
            prev_oi = matching_oi[-5]  # 4 години тому (крок 1 год)
            oi_change_pct = (current_oi - prev_oi) / prev_oi * 100.0 if prev_oi > 0 else 0.0
            
            # Якщо OI зростає на зростанні ціни -> сильні лонги
            price_change_4h = close.iloc[-1] - close.iloc[-5] if len(close) >= 5 else 0.0
            if oi_change_pct > 2.0:
                oi_contrib = 8.3 if price_change_4h > 0 else -8.3
            elif oi_change_pct < -2.0:
                oi_contrib = -4.1 if price_change_4h > 0 else 4.1

    # Funding rate перекіс
    funding_contrib = 0.0
    if funding_rate > 0.0003:  # перегрів лонгів -> ризик лонг-сквізу
        funding_contrib = -8.3
    elif funding_rate < -0.0003: # перегрів шортів -> ризик шорт-сквізу
        funding_contrib = 8.3

    volume_flow_score = np.clip(obv_contrib + oi_contrib + funding_contrib, -25.0, 25.0)

    # --------------------------------------------------------
    # 5. КАРТА ЛІКВІДАЦІЙ (Тяжіння найближчих зон, max +-25)
    # --------------------------------------------------------
    liq_score = 0.0
    if liq_zones:
        # Рахуємо нетто-силу тяжіння зон ліквідації
        # Сила = intensity * exp(-abs(dist_pct) / 2.0)
        net_force = 0.0
        for z in liq_zones:
            # Беремо тільки зони з впевненістю >= 0.75 або хоча б вагою
            intensity = z.get("intensity", 0.5)
            dist_pct = z.get("dist_pct", 0.0)
            if dist_pct == 0:
                continue
            
            # Тяжіння експоненціально згасає з відстанню
            force = intensity * np.exp(-abs(dist_pct) / 2.0)
            if z["side"] == "SHORT_LIQ":
                net_force += force  # тягне вгору
            else:
                net_force -= force  # тягне вниз
                
        # Нормуємо на шкалу +-25
        liq_score = np.clip(net_force * 15.0, -25.0, 25.0)

    # --------------------------------------------------------
    # ФІНАЛЬНИЙ СКОР (-100 ... +100)
    # --------------------------------------------------------
    total_score = trend_score + momentum_score + volatility_score + volume_flow_score + liq_score
    total_score = int(np.clip(round(total_score), -100, 100))

    # Додатково ATR для масштабування цілей
    atr_ind = AverageTrueRange(high, low, close, window=14)
    atr_val = float(atr_ind.average_true_range().iloc[-1])

    return {
        "score": total_score,
        "trend": round(trend_score, 1),
        "momentum": round(momentum_score, 1),
        "volatility": round(volatility_score, 1),
        "volume_flow": round(volume_flow_score, 1),
        "liq_map": round(liq_score, 1),
        "atr": atr_val
    }
