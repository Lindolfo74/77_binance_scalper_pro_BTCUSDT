"""
Estrategia: Alligator + Estocástico + Bandas de Bollinger.

Orden de confirmación (importante, no cambiar sin razón):
1. Alligator (idealmente en timeframe SUPERIOR, ej. 15m/1h): define la
   TENDENCIA. No se opera nunca en contra de ella.
2. Bandas de Bollinger (timeframe de entrada): el precio debe estar en la
   zona extrema opuesta al lado en el que se quiere entrar (cerca/tocando
   la banda inferior para LONG, cerca/tocando la banda superior para SHORT).
   Esto evita entrar en medio de la banda, donde la señal del Estocástico
   es más ruidosa.
3. Estocástico (timeframe de entrada): confirma el MOMENTO exacto de
   entrada con el cruce %K/%D, a favor de la tendencia, saliendo de
   sobreventa/sobrecompra.

Filtros opcionales (para subir la calidad de la señal a costa de menos
operaciones):
- Fuga y reentrada de Bollinger (fakeout): el precio cerró fuera de la
  banda y ya volvió a cerrar dentro -> señal de reversión más fuerte.
- Divergencia del Estocástico: el precio hace un extremo más marcado pero
  el Estocástico no lo acompaña -> debilidad del movimiento, reversión
  probable antes de que el precio la confirme.

Filosofía de salida anticipada:
Si la posición está en ganancia y el Estocástico (o el Alligator) muestran
que la tendencia se está revirtiendo, el bot cierra manualmente a mercado
para asegurar la ganancia neta, en vez de esperar a que el precio retroceda
hasta el TP original — o peor, hasta el SL.
"""
import pandas as pd
import numpy as np


# ---------------------------------------------------------------------------
# Alligator (Bill Williams) — define la tendencia
# ---------------------------------------------------------------------------

def _smma(series: pd.Series, period: int) -> pd.Series:
    """
    Smoothed Moving Average: la media móvil que usa el Alligator
    (distinta de una EMA o SMA normal).
    """
    smma = series.copy().astype(float)
    if len(series) < period:
        return smma * np.nan
    smma.iloc[:period - 1] = np.nan
    smma.iloc[period - 1] = series.iloc[:period].mean()
    for i in range(period, len(series)):
        smma.iloc[i] = (smma.iloc[i - 1] * (period - 1) + series.iloc[i]) / period
    return smma


def compute_alligator(df: pd.DataFrame, jaw_period=13, jaw_shift=8,
                       teeth_period=8, teeth_shift=5,
                       lips_period=5, lips_shift=3) -> pd.DataFrame:
    df = df.copy()
    median_price = (df["high"] + df["low"]) / 2
    df["jaw"] = _smma(median_price, jaw_period).shift(jaw_shift)
    df["teeth"] = _smma(median_price, teeth_period).shift(teeth_shift)
    df["lips"] = _smma(median_price, lips_period).shift(lips_shift)
    return df


def get_trend(df: pd.DataFrame, min_separation_pct: float = 0.05) -> str | None:
    """
    Devuelve 'UP', 'DOWN' o None (sin tendencia clara / "alligator dormido").

    UP:   lips > teeth > jaw  y el precio cierra por encima de las 3 líneas.
    DOWN: lips < teeth < jaw  y el precio cierra por debajo de las 3 líneas.

    min_separation_pct evita operar cuando las líneas están entrelazadas
    (mercado lateral / "boca cerrada" del Alligator). Si usas timeframe
    superior para la tendencia (recomendado), puedes subir este valor
    porque hay menos ruido.
    """
    last = df.iloc[-1]
    jaw, teeth, lips = last["jaw"], last["teeth"], last["lips"]
    if pd.isna(jaw) or pd.isna(teeth) or pd.isna(lips):
        return None

    price = last["close"]
    separation = abs(lips - jaw) / jaw * 100 if jaw else 0.0
    if separation < min_separation_pct:
        return None

    if lips > teeth > jaw and price > lips:
        return "UP"
    if lips < teeth < jaw and price < lips:
        return "DOWN"
    return None


# ---------------------------------------------------------------------------
# Estocástico — confirma el momento de entrada/salida
# ---------------------------------------------------------------------------

def compute_stochastic(df: pd.DataFrame, k_period=14, d_period=3, smooth_k=3) -> pd.DataFrame:
    df = df.copy()
    low_min = df["low"].rolling(window=k_period).min()
    high_max = df["high"].rolling(window=k_period).max()
    raw_k = 100 * (df["close"] - low_min) / (high_max - low_min).replace(0, np.nan)
    df["stoch_k"] = raw_k.rolling(window=smooth_k).mean()
    df["stoch_d"] = df["stoch_k"].rolling(window=d_period).mean()
    return df


def _stoch_cross_up(prev, last) -> bool:
    return prev["stoch_k"] <= prev["stoch_d"] and last["stoch_k"] > last["stoch_d"]


def _stoch_cross_down(prev, last) -> bool:
    return prev["stoch_k"] >= prev["stoch_d"] and last["stoch_k"] < last["stoch_d"]


def detect_stoch_divergence(df: pd.DataFrame, side: str, lookback: int = 10) -> bool:
    """
    Divergencia alcista (para LONG): el precio hace un mínimo más BAJO en la
    segunda mitad de la ventana, pero el Estocástico hace un mínimo más ALTO
    -> el impulso bajista se está agotando.

    Divergencia bajista (para SHORT): el precio hace un máximo más ALTO en
    la segunda mitad, pero el Estocástico hace un máximo más BAJO -> el
    impulso alcista se está agotando.

    Comparación simplificada: primera mitad de la ventana vs segunda mitad.
    Es un filtro estricto (baja la frecuencia de señales); actívalo solo si
    quieres priorizar calidad sobre cantidad (config.USE_DIVERGENCE_FILTER).
    """
    if len(df) < lookback or lookback < 4:
        return False

    window = df.iloc[-lookback:]
    mid = lookback // 2
    first_half, second_half = window.iloc[:mid], window.iloc[mid:]

    if first_half["stoch_k"].isna().all() or second_half["stoch_k"].isna().all():
        return False

    if side == "LONG":
        price_lower_low = second_half["low"].min() < first_half["low"].min()
        stoch_higher_low = second_half["stoch_k"].min() > first_half["stoch_k"].min()
        return bool(price_lower_low and stoch_higher_low)
    else:
        price_higher_high = second_half["high"].max() > first_half["high"].max()
        stoch_lower_high = second_half["stoch_k"].max() < first_half["stoch_k"].max()
        return bool(price_higher_high and stoch_lower_high)


# ---------------------------------------------------------------------------
# Bandas de Bollinger — zona extrema para confirmar entrada + base del SL/TP
# ---------------------------------------------------------------------------

def compute_bollinger(df: pd.DataFrame, period=20, num_std=2.0) -> pd.DataFrame:
    df = df.copy()
    mid = df["close"].rolling(window=period).mean()
    std = df["close"].rolling(window=period).std()
    df["bb_mid"] = mid
    df["bb_upper"] = mid + num_std * std
    df["bb_lower"] = mid - num_std * std
    return df


def get_bb_zone(df: pd.DataFrame, proximity_pct: float = 0.15) -> str | None:
    """
    Devuelve 'upper' si el precio está tocando/cerca de la banda superior
    (zona de sobrecompra), 'lower' si está tocando/cerca de la banda
    inferior (zona de sobreventa), o None si está en la zona media (sin
    señal de extremo -> no se debería entrar aquí).

    proximity_pct: qué tan cerca de la banda debe estar el precio, como %
    del precio actual. Ej: 0.15 significa que el high/low de la vela debe
    estar a 0.15% o menos de la banda (o haberla cruzado) para contar.
    """
    last = df.iloc[-1]
    close, high, low = last["close"], last["high"], last["low"]
    upper, lower = last["bb_upper"], last["bb_lower"]

    if pd.isna(upper) or pd.isna(lower):
        return None

    tolerance = close * (proximity_pct / 100.0)

    if high >= upper - tolerance:
        return "upper"
    if low <= lower + tolerance:
        return "lower"
    return None


def detect_bb_fakeout_reentry(df: pd.DataFrame, side: str, lookback: int = 3) -> bool:
    """
    True si en las últimas `lookback` velas (sin contar la actual) el precio
    cerró FUERA de la banda correspondiente, y la vela actual ya cerró de
    vuelta DENTRO. Esta "fuga y reentrada" suele anticipar una reversión más
    confiable que un simple toque de banda.

    LONG:  cerró bajo bb_lower en alguna vela reciente, y ahora cierra por
           encima de bb_lower otra vez.
    SHORT: cerró sobre bb_upper en alguna vela reciente, y ahora cierra por
           debajo de bb_upper otra vez.
    """
    if len(df) < lookback + 1:
        return False

    window = df.iloc[-(lookback + 1):-1]
    last = df.iloc[-1]

    if pd.isna(last["bb_lower"]) or pd.isna(last["bb_upper"]):
        return False

    if side == "LONG":
        was_outside = (window["close"] < window["bb_lower"]).any()
        back_inside = last["close"] > last["bb_lower"]
        return bool(was_outside and back_inside)
    else:
        was_outside = (window["close"] > window["bb_upper"]).any()
        back_inside = last["close"] < last["bb_upper"]
        return bool(was_outside and back_inside)


# ---------------------------------------------------------------------------
# Construcción de indicadores
# ---------------------------------------------------------------------------

def build_trend_indicators(df: pd.DataFrame, config) -> pd.DataFrame:
    """
    Solo Alligator. Se usa sobre el DataFrame del timeframe SUPERIOR
    (config.TREND_INTERVAL) cuando se quiere confirmación multi-timeframe.
    """
    return compute_alligator(
        df,
        jaw_period=config.ALLIGATOR_JAW_PERIOD, jaw_shift=config.ALLIGATOR_JAW_SHIFT,
        teeth_period=config.ALLIGATOR_TEETH_PERIOD, teeth_shift=config.ALLIGATOR_TEETH_SHIFT,
        lips_period=config.ALLIGATOR_LIPS_PERIOD, lips_shift=config.ALLIGATOR_LIPS_SHIFT,
    )


def build_indicators(df: pd.DataFrame, config) -> pd.DataFrame:
    """
    Alligator + Estocástico + Bollinger sobre el mismo DataFrame (timeframe
    de entrada, config.INTERVAL). Se mantiene por compatibilidad y para
    cuando NO se usa confirmación multi-timeframe (df_trend=None en
    get_entry_signal, en cuyo caso el Alligator de aquí mismo se usa como
    tendencia).
    """
    df = compute_alligator(
        df,
        jaw_period=config.ALLIGATOR_JAW_PERIOD, jaw_shift=config.ALLIGATOR_JAW_SHIFT,
        teeth_period=config.ALLIGATOR_TEETH_PERIOD, teeth_shift=config.ALLIGATOR_TEETH_SHIFT,
        lips_period=config.ALLIGATOR_LIPS_PERIOD, lips_shift=config.ALLIGATOR_LIPS_SHIFT,
    )
    df = compute_stochastic(
        df,
        k_period=config.STOCH_K_PERIOD, d_period=config.STOCH_D_PERIOD,
        smooth_k=config.STOCH_SMOOTH_K,
    )
    df = compute_bollinger(df, period=config.BB_PERIOD, num_std=config.BB_STD)
    return df


# ---------------------------------------------------------------------------
# Lectura de señales
# ---------------------------------------------------------------------------

def get_entry_signal(df: pd.DataFrame, config, df_trend: pd.DataFrame = None) -> str | None:
    """
    df: DataFrame del timeframe de ENTRADA (config.INTERVAL) con Estocástico
        y Bollinger ya calculados (y Alligator también, si no se pasa df_trend).
    df_trend: opcional, DataFrame del timeframe SUPERIOR (config.TREND_INTERVAL)
        con Alligator calculado (build_trend_indicators). Si no se pasa, la
        tendencia se toma del propio `df`.

    Orden de confirmación:
    1. Alligator (df_trend si se provee) -> tendencia UP/DOWN/None.
    2. Estocástico (df) -> cruce a favor de la tendencia, saliendo de
       sobreventa/sobrecompra -> candidato LONG/SHORT.
    3. Filtros de calidad sobre el candidato (todos activables por config):
       - Zona extrema de Bollinger (REQUIRE_BB_ZONE)
       - Fuga y reentrada de Bollinger (REQUIRE_BB_FAKEOUT)
       - Divergencia del Estocástico (USE_DIVERGENCE_FILTER)
    """
    if len(df) < 2:
        return None

    trend_df = df_trend if df_trend is not None else df
    trend = get_trend(trend_df, config.ALLIGATOR_MIN_SEPARATION_PCT)
    if trend is None:
        return None

    prev, last = df.iloc[-2], df.iloc[-1]
    if pd.isna(last["stoch_k"]) or pd.isna(last["stoch_d"]) or pd.isna(prev["stoch_k"]):
        return None

    candidate = None
    if trend == "UP" and _stoch_cross_up(prev, last) and prev["stoch_k"] < config.STOCH_OVERSOLD:
        candidate = "LONG"
    elif trend == "DOWN" and _stoch_cross_down(prev, last) and prev["stoch_k"] > config.STOCH_OVERBOUGHT:
        candidate = "SHORT"

    if candidate is None:
        return None

    if config.REQUIRE_BB_ZONE:
        zone = get_bb_zone(df, config.BB_ZONE_PROXIMITY_PCT)
        required_zone = "lower" if candidate == "LONG" else "upper"
        if zone != required_zone:
            return None

    if config.REQUIRE_BB_FAKEOUT:
        if not detect_bb_fakeout_reentry(df, candidate, config.BB_FAKEOUT_LOOKBACK):
            return None

    if config.USE_DIVERGENCE_FILTER:
        if not detect_stoch_divergence(df, candidate, config.DIVERGENCE_LOOKBACK):
            return None

    return candidate


def get_exit_signal(df: pd.DataFrame, side: str, config, df_trend: pd.DataFrame = None) -> bool:
    """
    True si conviene cerrar anticipadamente una posición abierta porque hay
    señal de que la tendencia se está revirtiendo:
    - El Estocástico cruza en contra viniendo de zona de sobrecompra/sobreventa, o
    - El Alligator ya deja de confirmar la tendencia de la posición.

    Esta función NO decide si hay ganancia o pérdida — eso lo evalúa bot.py
    antes de actuar sobre esta señal (solo se usa para "salir con ganancia").
    """
    if len(df) < 2:
        return False

    prev, last = df.iloc[-2], df.iloc[-1]
    if pd.isna(last["stoch_k"]) or pd.isna(last["stoch_d"]) or pd.isna(prev["stoch_k"]):
        return False

    trend_df = df_trend if df_trend is not None else df
    trend = get_trend(trend_df, config.ALLIGATOR_MIN_SEPARATION_PCT)

    if side == "LONG":
        stoch_reversal = _stoch_cross_down(prev, last) and prev["stoch_k"] > config.STOCH_OVERBOUGHT
        trend_broken = trend == "DOWN"
        return bool(stoch_reversal or trend_broken)
    else:
        stoch_reversal = _stoch_cross_up(prev, last) and prev["stoch_k"] < config.STOCH_OVERSOLD
        trend_broken = trend == "UP"
        return bool(stoch_reversal or trend_broken)
