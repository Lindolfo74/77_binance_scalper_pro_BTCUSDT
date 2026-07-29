"""
Configuración centralizada del bot.
Todos los parámetros sensibles y ajustables viven en el archivo .env
(ver .env.example para la plantilla).
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "y")


def _get_float(name: str, default: float) -> float:
    val = os.getenv(name)
    return float(val) if val is not None else default


def _get_int(name: str, default: int) -> int:
    val = os.getenv(name)
    return int(val) if val is not None else default


class Config:
    # --- Credenciales (SIEMPRE desde variables de entorno, nunca hardcodeadas) ---
    API_KEY = os.getenv("BINANCE_API_KEY", "")
    API_SECRET = os.getenv("BINANCE_API_SECRET", "")

    # --- Entorno ---
    # TESTNET debe permanecer True mientras pruebas. Pasar a False = dinero REAL.
    TESTNET = _get_bool("TESTNET", True)

    # --- Mercado ---
    SYMBOL = os.getenv("SYMBOL", "BTCUSDT")
    INTERVAL = os.getenv("INTERVAL", "1m")  # velas de ENTRADA: 1m, 3m, 5m...
    LEVERAGE = _get_int("LEVERAGE", 7)
    MARGIN_TYPE = os.getenv("MARGIN_TYPE", "ISOLATED")  # ISOLATED o CROSSED

    # --- Capital ---
    CAPITAL_USDT = _get_float("CAPITAL_USDT", 200.0)
    # % de margen máximo que se usa por posición (sobre CAPITAL_USDT)
    MAX_MARGIN_PCT_PER_TRADE = _get_float("MAX_MARGIN_PCT_PER_TRADE", 25.0)

    # --- Riesgo por operación (monto fijo en USDT, no % del capital) ---
    # Cuánto se arriesga como máximo si se toca el SL. El tamaño de la
    # posición se calcula para que la pérdida en el SL sea exactamente esto.
    RISK_USDT_PER_TRADE = _get_float("RISK_USDT_PER_TRADE", 4.0)
    # Relación ganancia/riesgo objetivo (solo aplica si TP_MODE="ratio"): el
    # TP queda a (distancia del SL) * este número.
    REWARD_RISK_RATIO = _get_float("REWARD_RISK_RATIO", 4.0)

    # --- (Legado) Modo de riesgo por % de capital — ya no lo usa bot.py por
    # defecto (ahora usa RISK_USDT_PER_TRADE + Bollinger/fixed), pero se
    # conserva por si quieres volver a calc_position_size() en risk.py ---
    RISK_PER_TRADE_PCT = _get_float("RISK_PER_TRADE_PCT", 1.0)
    TAKE_PROFIT_PCT = _get_float("TAKE_PROFIT_PCT", 0.6)
    STOP_LOSS_PCT = _get_float("STOP_LOSS_PCT", 0.3)

    # --- Modo de cálculo del SL/TP ---
    # SL_MODE: "bollinger" (distancia = banda opuesta, validada por volumen)
    #          o "fixed" (% fijo = STOP_LOSS_PCT, ignora Bollinger).
    SL_MODE = os.getenv("SL_MODE", "bollinger")
    # TP_MODE: "ratio" (distancia_SL * REWARD_RISK_RATIO)
    #          o "band" (banda del lado de la operación: bb_upper en LONG,
    #          bb_lower en SHORT; si queda mal ubicada, cae a "ratio").
    TP_MODE = os.getenv("TP_MODE", "ratio")
    # Si el volumen de la vela de entrada es menor a este % del promedio de
    # las últimas 20 velas, no se confía en la distancia de la banda para el
    # SL (mercado momentáneamente ilíquido) y se usa MIN_SL_PCT directamente.
    MIN_VOLUME_RATIO_FOR_BAND_SL = _get_float("MIN_VOLUME_RATIO_FOR_BAND_SL", 0.5)

    # --- Bandas de Bollinger (timeframe de entrada) ---
    BB_PERIOD = _get_int("BB_PERIOD", 20)
    BB_STD = _get_float("BB_STD", 2.0)
    # Límites de seguridad para el % de SL calculado desde la banda, para que
    # nunca quede pegado al precio (posición gigante) ni demasiado lejos.
    MIN_SL_PCT = _get_float("MIN_SL_PCT", 0.10)
    MAX_SL_PCT = _get_float("MAX_SL_PCT", 0.60)

    # Zona extrema de Bollinger como confirmación de entrada (Alligator ->
    # Bollinger -> Estocástico). Si REQUIRE_BB_ZONE=True, solo se entra en
    # LONG si el precio tocó/está cerca de bb_lower, y en SHORT si tocó/está
    # cerca de bb_upper.
    REQUIRE_BB_ZONE = _get_bool("REQUIRE_BB_ZONE", True)
    # Qué tan cerca de la banda debe estar el precio, como % del precio.
    BB_ZONE_PROXIMITY_PCT = _get_float("BB_ZONE_PROXIMITY_PCT", 0.15)

    # Fuga y reentrada de banda (fakeout) como confirmación extra, opcional.
    REQUIRE_BB_FAKEOUT = _get_bool("REQUIRE_BB_FAKEOUT", False)
    BB_FAKEOUT_LOOKBACK = _get_int("BB_FAKEOUT_LOOKBACK", 3)

    # --- Estocástico (entradas y salidas, timeframe de entrada) ---
    STOCH_K_PERIOD = _get_int("STOCH_K_PERIOD", 14)
    STOCH_D_PERIOD = _get_int("STOCH_D_PERIOD", 3)
    STOCH_SMOOTH_K = _get_int("STOCH_SMOOTH_K", 3)
    STOCH_OVERSOLD = _get_float("STOCH_OVERSOLD", 20.0)
    STOCH_OVERBOUGHT = _get_float("STOCH_OVERBOUGHT", 80.0)

    # Filtro de divergencia del Estocástico (opcional, baja mucho la
    # frecuencia de señales; actívalo si prefieres calidad sobre cantidad).
    USE_DIVERGENCE_FILTER = _get_bool("USE_DIVERGENCE_FILTER", False)
    DIVERGENCE_LOOKBACK = _get_int("DIVERGENCE_LOOKBACK", 10)

    # --- Alligator (Bill Williams) — define la tendencia ---
    ALLIGATOR_JAW_PERIOD = _get_int("ALLIGATOR_JAW_PERIOD", 13)
    ALLIGATOR_JAW_SHIFT = _get_int("ALLIGATOR_JAW_SHIFT", 8)
    ALLIGATOR_TEETH_PERIOD = _get_int("ALLIGATOR_TEETH_PERIOD", 8)
    ALLIGATOR_TEETH_SHIFT = _get_int("ALLIGATOR_TEETH_SHIFT", 5)
    ALLIGATOR_LIPS_PERIOD = _get_int("ALLIGATOR_LIPS_PERIOD", 5)
    ALLIGATOR_LIPS_SHIFT = _get_int("ALLIGATOR_LIPS_SHIFT", 3)
    # % mínimo de separación entre lips y jaw para considerar que hay
    # tendencia (evita operar con el "alligator dormido" en mercado lateral).
    ALLIGATOR_MIN_SEPARATION_PCT = _get_float("ALLIGATOR_MIN_SEPARATION_PCT", 0.05)

    # --- Confirmación multi-timeframe ---
    # Timeframe SUPERIOR donde se calcula el Alligator (tendencia). El
    # Estocástico y Bollinger siguen usando INTERVAL. Déjalo igual a
    # INTERVAL si prefieres desactivar la confirmación multi-timeframe.
    TREND_INTERVAL = os.getenv("TREND_INTERVAL", "15m")

    # --- Filtro de libro de órdenes (paredes de liquidez) ---
    USE_ORDERBOOK_FILTER = _get_bool("USE_ORDERBOOK_FILTER", False)
    ORDERBOOK_DEPTH = _get_int("ORDERBOOK_DEPTH", 50)
    # Rango de precio (% alrededor del mark price) donde se suma liquidez.
    ORDERBOOK_RANGE_PCT = _get_float("ORDERBOOK_RANGE_PCT", 0.5)
    # Si la liquidez en contra supera este múltiplo de la liquidez a favor
    # dentro del rango, se descarta la señal.
    ORDERBOOK_WALL_RATIO = _get_float("ORDERBOOK_WALL_RATIO", 3.0)

    # --- Límites operativos de seguridad ---
    MAX_TRADES_PER_DAY = _get_int("MAX_TRADES_PER_DAY", 15)
    MAX_CONSECUTIVE_LOSSES = _get_int("MAX_CONSECUTIVE_LOSSES", 3)
    DAILY_MAX_LOSS_PCT = _get_float("DAILY_MAX_LOSS_PCT", 10.0)  # kill-switch diario
    LOOP_SLEEP_SECONDS = _get_int("LOOP_SLEEP_SECONDS", 15)

    # --- Logging ---
    LOG_FILE = os.getenv("LOG_FILE", "trades_log.csv")


config = Config()
