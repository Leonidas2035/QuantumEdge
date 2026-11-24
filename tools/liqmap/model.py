"""
================================================================
QuantumEdge — LIQUIDATION HEATMAP (CoinGlass-style, self-hosted)
================================================================
Папка: /home/layman/QuantumEdge/tools/liqmap/model.py

ЩО ЦЕ:
  Евристична модель карти ліквідацій (як у CoinGlass/Gate). CoinGlass НЕ має
  «справжніх» ліквідацій — вони їх МОДЕЛЮЮТЬ з ціни+обсягу+плечей. Ми робимо
  те саме: з OHLCV+OI будуємо сітку (ціна × час) з інтенсивністю ліквідацій.

ЛОГІКА:
  1. На кожній свічці трейдери відкривали позиції з типовими плечима (10/25/50/100x).
     Обсяг свічки (volume) ~ розмір нових позицій.
  2. Лонги відкриті на свічці мають ліквідаційну ціну НИЖЧЕ входу (entry*(1-1/lev)).
     Шорти — ВИЩЕ входу (entry*(1+1/lev)).
  3. Кожну ліквідаційну ціну «розмазуємо» в цінову корзину, вагу = обсяг * розподіл_плеча.
  4. Не спрацьовані рівні (де ціна ще НЕ проходила після їх утворення) лишаються
     як «магніти» ліквідності. Спрацьовані (ціна вже торкнулась) — гасяться.
  5. Опційно масштабуємо ваги приростом OI (більше OI = більше живих позицій).

РЕЗУЛЬТАТ:
  - grid: 2D інтенсивність (price_bins × time) для теплокарти
  - zones: топ цінових скупчень (магніти) з боком (LONG_LIQ нижче / SHORT_LIQ вище)
  - Не дає інсайду біржі; це модель ліквідності, куди ціну «тягне».
================================================================
"""
from __future__ import annotations
import numpy as np

# Типові плечі роздрібу та їх приблизна вага (сума=1). Емпірика CoinGlass-стилю.
LEVERAGE_MIX = {10: 0.30, 25: 0.30, 50: 0.25, 100: 0.15}

MAINT_MARGIN = 0.005  # ~0.5% підтримуюча маржа → ліквідація трохи раніше за 1/lev


def liq_price(entry: float, lev: int, side: str) -> float:
    """Приблизна ліквідаційна ціна позиції."""
    dist = (1.0 / lev) - MAINT_MARGIN
    if side == "long":
        return entry * (1.0 - dist)
    return entry * (1.0 + dist)


def build_heatmap(ohlcv, price_bins=140, oi_series=None, lookback=None):
    """
    ohlcv: список [ts, o, h, l, c, v] (за зростанням часу).
    oi_series: опційно список (ts, oi_value) для масштабування ваг.
    Повертає dict з grid, price_levels, zones, meta.
    """
    if lookback:
        ohlcv = ohlcv[-lookback:]
    arr = np.array(ohlcv, dtype=float)
    ts, o, h, l, c, v = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4], arr[:, 5]
    n = len(arr)
    last_price = float(c[-1])

    # цінова шкала: діапазон свічок + запас 6% вгору/вниз (де живуть магніти)
    pmin = float(l.min()) * 0.94
    pmax = float(h.max()) * 1.06
    edges = np.linspace(pmin, pmax, price_bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    bin_w = edges[1] - edges[0]

    # OI-масштаб: нормований множник ваги за кожну свічку
    oi_mult = np.ones(n)
    if oi_series and len(oi_series) >= 2:
        oi_ts = np.array([x[0] for x in oi_series], dtype=float)
        oi_val = np.array([x[1] for x in oi_series], dtype=float)
        # для кожної свічки беремо найближчий OI, нормуємо на середнє
        base = np.nanmean(oi_val) or 1.0
        for i in range(n):
            j = int(np.argmin(np.abs(oi_ts - ts[i])))
            oi_mult[i] = (oi_val[j] / base) if base else 1.0

    grid = np.zeros((price_bins, n))  # [price_bin, time]

    for i in range(n):
        entry = (o[i] + c[i]) / 2.0  # середня ціна входу на свічці
        vol = v[i] * oi_mult[i]
        if vol <= 0:
            continue
        for side in ("long", "short"):
            for lev, w in LEVERAGE_MIX.items():
                lp = liq_price(entry, lev, side)
                if lp <= pmin or lp >= pmax:
                    continue
                bidx = int((lp - pmin) / bin_w)
                if bidx < 0 or bidx >= price_bins:
                    continue
                weight = vol * w * 0.5  # 0.5: half long / half short assumption
                touched_at = None
                sub_l = l[i+1:]
                sub_h = h[i+1:]
                if len(sub_l) > 0:
                    matches = (sub_l <= lp) & (lp <= sub_h)
                    idx_matches = np.where(matches)[0]
                    if len(idx_matches) > 0:
                        touched_at = int(idx_matches[0] + i + 1)
                end = touched_at if touched_at is not None else n
                # додаємо вагу в стовпці [i..end); після торкання — зона «вибита»
                grid[bidx, i:end] += weight

    # згладжування по ціновій осі (розмазати сусідні корзини)
    kernel = np.array([0.25, 0.5, 1.0, 0.5, 0.25])
    kernel = kernel / kernel.sum()
    for t in range(n):
        grid[:, t] = np.convolve(grid[:, t], kernel, mode="same")

    # ПОТОЧНИЙ профіль магнітів = останній стовпець (актуальні незняті рівні)
    current = grid[:, -1].copy()

    zones = _extract_zones(current, centers, last_price)

    return {
        "meta": {
            "n_candles": n,
            "last_price": last_price,
            "price_min": pmin,
            "price_max": pmax,
            "bin_width": float(bin_w),
            "ts_start": float(ts[0]),
            "ts_end": float(ts[-1]),
        },
        "price_levels": centers.tolist(),
        "time_axis": ts.tolist(),
        "grid": grid.tolist(),            # для теплокарти
        "candles": [[float(ts[i]), float(o[i]), float(h[i]), float(l[i]), float(c[i])] for i in range(n)],
        "current_profile": current.tolist(),
        "zones": zones,                    # для бота
    }


def _extract_zones(profile, centers, last_price, top=8):
    """Витягти найсильніші цінові скупчення (магніти) з поточного профілю."""
    if profile.max() <= 0:
        return []
    
    # Коефіцієнт згасання від відстані (експоненціальний спад, характерний масштаб 1.8%)
    # Це гарантує монотонність (вищий інтенсивність -> вища ймовірність досягнення)
    dists = np.abs(centers - last_price) / last_price * 100.0
    decay = np.exp(-dists / 1.8)
    weighted_profile = profile * decay
    
    if weighted_profile.max() <= 0:
        return []
        
    norm = weighted_profile / weighted_profile.max()
    idxs = np.argsort(weighted_profile)[::-1]
    zones = []
    used = []
    for idx in idxs:
        price = float(centers[idx])
        if norm[idx] < 0.15:  # відсікаємо шум
            break
        # злиття близьких зон (у межах 0.4%)
        if any(abs(price - u) / last_price < 0.004 for u in used):
            continue
        side = "LONG_LIQ" if price < last_price else "SHORT_LIQ"
        dist_pct = (price - last_price) / last_price * 100.0
        zones.append({
            "price": round(price, 2),
            "intensity": round(float(norm[idx]), 3),
            "side": side,             # LONG_LIQ нижче ціни / SHORT_LIQ вище
            "dist_pct": round(dist_pct, 2),
        })
        used.append(price)
        if len(zones) >= top:
            break
    return zones


def classify_regime(ohlcv, t, window=200):
    """
    Класифікує режим ринку в точці t: 'bull', 'bear', або 'flat'.
    ohlcv: список [ts, o, h, l, c, v]
    t: поточний індекс
    """
    if t < window + 24:
        return 'flat'
    
    closes = [ohlcv[i][4] for i in range(max(0, t - window - 24), t)]
    
    # Розраховуємо поточне ковзне середнє (MA)
    ma_current = sum(closes[-window:]) / window
    # Розраховуємо MA 24 години тому
    ma_prev = sum(closes[:-24]) / window
    
    # Зміна MA в % за 24 години
    ma_slope = (ma_current - ma_prev) / ma_prev * 100.0
    
    # Поріг тренду: 0.03% за 24 години
    if ma_slope > 0.03:
        return 'bull'
    elif ma_slope < -0.03:
        return 'bear'
    else:
        return 'flat'

