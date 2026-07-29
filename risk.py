"""
Cálculo de tamaño de posición, SL/TP y control de riesgo.

Enfoque de riesgo fijo en dólares:
- Se arriesga siempre RISK_USDT_PER_TRADE (ej. 3-5 USDT) por operación,
  sin importar qué tan lejos quede el SL. El tamaño de la posición se ajusta
  para que, si se toca el SL, la pérdida sea exactamente ese monto (no más).

SL (config.SL_MODE):
- "bollinger" (default): distancia = banda opuesta al precio de entrada,
  acotada entre MIN_SL_PCT y MAX_SL_PCT. Si el volumen de la vela de entrada
  es muy bajo respecto al promedio reciente (mercado momentáneamente
  ilíquido), NO se confía en la banda -> se usa MIN_SL_PCT directamente,
  porque una banda calculada sobre poco volumen puede no reflejar
  volatilidad real y dejar el SL mal ubicado.
- "fixed": % fijo (config.STOP_LOSS_PCT), ignorando Bollinger. Más simple y
  predecible; útil si notas que las bandas te dan SL erráticos en momentos
  de alta volatilidad repentina.

TP (config.TP_MODE):
- "ratio" (default): distancia_SL * REWARD_RISK_RATIO.
- "band": usa la banda del LADO de la operación (bb_upper para LONG,
  bb_lower para SHORT) como referencia de salida, en vez de un múltiplo fijo.
  Si la banda queda del lado equivocado (ej. por debajo del precio de
  entrada en un LONG), se cae de vuelta a "ratio" para esa operación.
"""
import math
import pandas as pd
from config import config


def round_quantity(quantity: float, step_size: float) -> float:
    """Redondea la cantidad al step_size permitido por el símbolo en Binance."""
    precision = int(round(-math.log10(step_size))) if step_size < 1 else 0
    factor = 10 ** precision
    return math.floor(quantity * factor) / factor


def round_price(price: float, tick_size: float) -> float:
    """
    Redondea un precio al tick_size permitido por el símbolo en Binance.

    IMPORTANTE: usar SIEMPRE esta función para stopPrice/price en órdenes,
    en vez de round(x, 2) fijo. Binance exige que el precio sea múltiplo
    exacto del tick_size del símbolo (que puede tener 1, 2, 4+ decimales
    según el par); si no coincide, la orden se rechaza con error -1111.
    """
    precision = int(round(-math.log10(tick_size))) if tick_size < 1 else 0
    return round(round(price / tick_size) * tick_size, precision)


def calc_position_size(entry_price: float) -> dict:
    """
    (Se mantiene por compatibilidad; ya no la usa bot.py para abrir posiciones
    -- ver calc_bollinger_sl_tp más abajo -- pero queda disponible si quieres
    volver al modo % de capital.)
    """
    risk_usdt = config.CAPITAL_USDT * (config.RISK_PER_TRADE_PCT / 100.0)
    sl_fraction = config.STOP_LOSS_PCT / 100.0
    if sl_fraction <= 0:
        raise ValueError("STOP_LOSS_PCT debe ser mayor a 0")

    notional = risk_usdt / sl_fraction
    max_margin = config.CAPITAL_USDT * (config.MAX_MARGIN_PCT_PER_TRADE / 100.0)
    max_notional_by_margin = max_margin * config.LEVERAGE
    notional = min(notional, max_notional_by_margin)

    quantity = notional / entry_price
    margin_used = notional / config.LEVERAGE

    return {
        "quantity": quantity,
        "notional": notional,
        "margin_used": margin_used,
        "risk_usdt": risk_usdt,
    }


def _resolve_sl_fraction(df, entry_price: float, side: str) -> float:
    if config.SL_MODE == "fixed":
        return config.STOP_LOSS_PCT / 100.0

    last = df.iloc[-1]

    if side == "LONG":
        band_distance = entry_price - last["bb_lower"]
    else:
        band_distance = last["bb_upper"] - entry_price

    # Validación de volumen: si la vela actual tiene mucho menos volumen que
    # el promedio reciente, la distancia de la banda no es confiable (puede
    # estar calculada sobre un tramo de mercado ilíquido/errático).
    avg_volume = df["volume"].tail(20).mean()
    volume_ok = (
        avg_volume is not None and avg_volume > 0 and
        (last["volume"] / avg_volume) >= config.MIN_VOLUME_RATIO_FOR_BAND_SL
    )

    invalid_band = (
        band_distance is None or band_distance != band_distance or
        band_distance <= 0 or not volume_ok
    )

    if invalid_band:
        return config.MIN_SL_PCT / 100.0

    sl_fraction = band_distance / entry_price
    # Acotar entre MIN_SL_PCT y MAX_SL_PCT para no arriesgar posiciones
    # gigantes cuando la banda está casi pegada al precio, ni SL
    # demasiado lejanos cuando la banda está muy ensanchada.
    return max(config.MIN_SL_PCT / 100.0, min(sl_fraction, config.MAX_SL_PCT / 100.0))


def _resolve_tp_price(df, entry_price: float, side: str, sl_fraction: float) -> float:
    ratio_tp_fraction = sl_fraction * config.REWARD_RISK_RATIO
    ratio_tp_price = (
        entry_price * (1 + ratio_tp_fraction) if side == "LONG"
        else entry_price * (1 - ratio_tp_fraction)
    )

    if config.TP_MODE != "band":
        return ratio_tp_price

    last = df.iloc[-1]
    if side == "LONG":
        band_tp = last["bb_upper"]
        if pd.isna(band_tp) or band_tp <= entry_price:
            return ratio_tp_price
        return band_tp
    else:
        band_tp = last["bb_lower"]
        if pd.isna(band_tp) or band_tp >= entry_price:
            return ratio_tp_price
        return band_tp


def calc_bollinger_sl_tp(df, entry_price: float, side: str, filters: dict) -> dict:
    """
    Calcula SL y TP según config.SL_MODE / config.TP_MODE, y dimensiona la
    posición para arriesgar siempre config.RISK_USDT_PER_TRADE.

    df: DataFrame con indicadores ya calculados (debe incluir bb_upper/bb_lower
        y volume; columnas producidas por strategy.build_indicators).
    Devuelve: dict con sl_price, tp_price, quantity, notional, margin_used,
        risk_usdt, sl_pct, tp_pct.
    """
    sl_fraction = _resolve_sl_fraction(df, entry_price, side)

    risk_usdt = config.RISK_USDT_PER_TRADE
    notional = risk_usdt / sl_fraction

    # Tope duro: no usar más margen que MAX_MARGIN_PCT_PER_TRADE del capital.
    max_margin = config.CAPITAL_USDT * (config.MAX_MARGIN_PCT_PER_TRADE / 100.0)
    max_notional_by_margin = max_margin * config.LEVERAGE
    notional = min(notional, max_notional_by_margin)

    quantity = notional / entry_price
    margin_used = notional / config.LEVERAGE

    if side == "LONG":
        sl_price = entry_price * (1 - sl_fraction)
    else:
        sl_price = entry_price * (1 + sl_fraction)

    tp_price = _resolve_tp_price(df, entry_price, side, sl_fraction)

    tick = filters["tick_size"]
    sl_price = round_price(sl_price, tick)
    tp_price = round_price(tp_price, tick)
    quantity = round_quantity(quantity, filters["step_size"])

    tp_pct = abs(tp_price - entry_price) / entry_price * 100.0

    return {
        "quantity": quantity,
        "notional": notional,
        "margin_used": margin_used,
        "risk_usdt": risk_usdt,
        "sl_price": sl_price,
        "tp_price": tp_price,
        "sl_pct": sl_fraction * 100.0,
        "tp_pct": tp_pct,
    }


class DailyGuard:
    """Kill-switch diario: detiene el bot si se superan los límites configurados."""

    def __init__(self):
        self.trades_today = 0
        self.consecutive_losses = 0
        self.pnl_today = 0.0
        self.day = None

    def _reset_if_new_day(self, today):
        if self.day != today:
            self.day = today
            self.trades_today = 0
            self.consecutive_losses = 0
            self.pnl_today = 0.0

    def register_trade(self, pnl: float, today):
        self._reset_if_new_day(today)
        self.trades_today += 1
        self.pnl_today += pnl
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

    def can_trade(self, today) -> tuple[bool, str]:
        self._reset_if_new_day(today)
        if self.trades_today >= config.MAX_TRADES_PER_DAY:
            return False, f"Límite diario de operaciones alcanzado ({config.MAX_TRADES_PER_DAY})"
        if self.consecutive_losses >= config.MAX_CONSECUTIVE_LOSSES:
            return False, f"Límite de pérdidas consecutivas alcanzado ({config.MAX_CONSECUTIVE_LOSSES})"
        max_loss = config.CAPITAL_USDT * (config.DAILY_MAX_LOSS_PCT / 100.0)
        if self.pnl_today <= -max_loss:
            return False, f"Kill-switch diario: pérdida máxima diaria alcanzada ({config.DAILY_MAX_LOSS_PCT}%)"
        return True, ""
