"""
Bot de scalping para Binance FUTURES TESTNET (dinero ficticio).

Estrategia: Alligator (tendencia, timeframe superior configurable) +
Estocástico (entradas/salidas) + Bandas de Bollinger (zona extrema +
cálculo de SL/TP con riesgo fijo en dólares). Filtros opcionales de
fakeout, divergencia y libro de órdenes -- ver config.py.

⚠️ IMPORTANTE:
- Este bot está pensado para correr contra la TESTNET de Binance Futures
  (https://testnet.binancefuture.com), con fondos ficticios.
- config.TESTNET debe permanecer en True. Pasarlo a False conecta a
  producción con dinero REAL — no lo hagas sin entender completamente
  el código, haber hecho tus propias pruebas, y asumir el riesgo.
- Nada de esto es asesoría financiera ni garantía de resultados.

Uso:
    python bot.py
"""
import csv
import os
import time
import logging
from datetime import datetime, timezone

import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException

from config import config
from strategy import (
    build_indicators, build_trend_indicators,
    get_entry_signal, get_exit_signal,
)
from risk import calc_bollinger_sl_tp, round_quantity, DailyGuard

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("scalper")

# Con el shift del Alligator (hasta 8 velas hacia adelante) y el periodo del
# Jaw (13), conviene traer bastante más historial que el mínimo, para que los
# indicadores no arranquen en NaN por falta de datos.
KLINES_LIMIT = 150


def make_client() -> Client:
    if not config.API_KEY or not config.API_SECRET:
        raise SystemExit(
            "Faltan BINANCE_API_KEY / BINANCE_API_SECRET. "
            "Copia .env.example a .env y complétalo con tus claves de TESTNET."
        )

    client = Client(config.API_KEY, config.API_SECRET, testnet=config.TESTNET)

    if config.TESTNET:
        # Binance migró la Testnet web de futuros a "Demo Trading" (demo.binance.com).
        # La URL base de la API cambió de testnet.binancefuture.com a demo-fapi.binance.com.
        client.FUTURES_URL = "https://demo-fapi.binance.com/fapi"
    return client


def get_symbol_filters(client: Client, symbol: str) -> dict:
    info = client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            filters = {f["filterType"]: f for f in s["filters"]}
            return {
                "step_size": float(filters["LOT_SIZE"]["stepSize"]),
                "tick_size": float(filters["PRICE_FILTER"]["tickSize"]),
                "min_qty": float(filters["LOT_SIZE"]["minQty"]),
            }
    raise ValueError(f"Símbolo {symbol} no encontrado en exchange info")


def setup_account(client: Client):
    try:
        client.futures_change_leverage(symbol=config.SYMBOL, leverage=config.LEVERAGE)
        log.info(f"Apalancamiento configurado: {config.LEVERAGE}x")
    except BinanceAPIException as e:
        log.warning(f"No se pudo fijar el apalancamiento (puede que ya esté igual): {e}")

    try:
        client.futures_change_margin_type(symbol=config.SYMBOL, marginType=config.MARGIN_TYPE)
        log.info(f"Tipo de margen configurado: {config.MARGIN_TYPE}")
    except BinanceAPIException as e:
        # Suele fallar con "No need to change margin type" si ya estaba puesto
        log.info(f"Margen ya estaba en {config.MARGIN_TYPE} o no se pudo cambiar: {e}")


def get_klines_df(client: Client, symbol: str, interval: str, limit: int = KLINES_LIMIT) -> pd.DataFrame:
    raw = client.futures_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "num_trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df


def get_market_data(client: Client):
    """
    Trae el DataFrame de ENTRADA (config.INTERVAL, con Alligator+Estocástico
    +Bollinger) y, si TREND_INTERVAL es distinto, el DataFrame de TENDENCIA
    (config.TREND_INTERVAL, solo con Alligator) para confirmación
    multi-timeframe. Si son iguales, se reutiliza el mismo DataFrame y la
    tendencia sale de ahí (comportamiento equivalente al bot original).
    """
    df_entry = get_klines_df(client, config.SYMBOL, config.INTERVAL)
    df_entry = build_indicators(df_entry, config)

    if config.TREND_INTERVAL == config.INTERVAL:
        return df_entry, None

    df_trend = get_klines_df(client, config.SYMBOL, config.TREND_INTERVAL)
    df_trend = build_trend_indicators(df_trend, config)
    return df_entry, df_trend


def orderbook_filter_ok(client: Client, side: str, mark_price: float) -> bool:
    """
    Filtro de libro de órdenes (punto 4): bloquea la entrada si hay una
    pared de liquidez significativa en el camino de la operación dentro de
    config.ORDERBOOK_RANGE_PCT alrededor del precio.
    - LONG:  importa la pared de VENTAS (asks) por encima del precio.
    - SHORT: importa la pared de COMPRAS (bids) por debajo del precio.
    Se compara contra la liquidez del lado opuesto en el mismo rango; si el
    lado que bloquea supera ORDERBOOK_WALL_RATIO veces al lado que confirma,
    se descarta la señal.
    """
    try:
        depth = client.futures_order_book(symbol=config.SYMBOL, limit=config.ORDERBOOK_DEPTH)
    except BinanceAPIException as e:
        log.warning(f"No se pudo obtener el libro de órdenes, se omite el filtro: {e}")
        return True

    range_frac = config.ORDERBOOK_RANGE_PCT / 100.0
    blocking_levels = depth["asks"] if side == "LONG" else depth["bids"]
    confirming_levels = depth["bids"] if side == "LONG" else depth["asks"]

    def sum_qty_within_range(levels):
        total = 0.0
        for price_str, qty_str in levels:
            price = float(price_str)
            if abs(price - mark_price) / mark_price <= range_frac:
                total += float(qty_str)
        return total

    wall_qty = sum_qty_within_range(blocking_levels)
    confirming_qty = sum_qty_within_range(confirming_levels)

    if confirming_qty <= 0:
        return True

    ratio = wall_qty / confirming_qty
    if ratio >= config.ORDERBOOK_WALL_RATIO:
        log.info(
            f"Señal descartada por pared de liquidez en contra de {side}: "
            f"{wall_qty:.3f} vs {confirming_qty:.3f} dentro de ±{config.ORDERBOOK_RANGE_PCT}% "
            f"(ratio {ratio:.1f}x >= {config.ORDERBOOK_WALL_RATIO}x)."
        )
        return False
    return True


def get_available_balance(client: Client) -> float:
    balances = client.futures_account_balance()
    for b in balances:
        if b["asset"] == "USDT":
            return float(b["balance"])
    return 0.0


def ensure_log_file():
    if not os.path.exists(config.LOG_FILE):
        with open(config.LOG_FILE, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp", "side", "entry_price", "exit_price", "quantity",
                "notional", "margin_used", "pnl_usdt", "reason",
            ])


def log_trade(row: dict):
    with open(config.LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            row["timestamp"], row["side"], row["entry_price"], row["exit_price"],
            row["quantity"], row["notional"], row["margin_used"], row["pnl_usdt"],
            row["reason"],
        ])


def get_open_position_amt(client: Client) -> float:
    positions = client.futures_position_information(symbol=config.SYMBOL)
    for p in positions:
        if p["symbol"] == config.SYMBOL:
            return float(p["positionAmt"])
    return 0.0


def cancel_open_orders(client: Client):
    try:
        client.futures_cancel_all_open_orders(symbol=config.SYMBOL)
    except BinanceAPIException as e:
        log.warning(f"No se pudieron cancelar órdenes abiertas: {e}")


def manual_close_position(client: Client, side: str, quantity: float, reason: str) -> bool:
    """Cierra la posición a mercado (reduceOnly). Devuelve True si se logró."""
    close_side = "SELL" if side == "LONG" else "BUY"
    try:
        client.futures_create_order(
            symbol=config.SYMBOL,
            side=close_side,
            type="MARKET",
            quantity=quantity,
            reduceOnly=True,
        )
        log.info(f"Cierre manual ejecutado ({reason}).")
        return True
    except BinanceAPIException as e:
        log.error(f"No se pudo cerrar la posición manualmente ({reason}): {e}")
        return False


def emergency_close_position(client: Client, side: str, quantity: float):
    """
    Failsafe: se usa cuando TP y/o SL no pudieron colocarse. Nunca debe
    quedar una posición abierta sin ninguna protección.
    """
    ok = manual_close_position(client, side, quantity, reason="FAILSAFE: TP/SL no se pudieron colocar")
    if not ok:
        log.critical(
            f"⚠️⚠️ FALLO CRÍTICO: no se pudo colocar TP/SL NI cerrar la posición de emergencia. "
            f"Hay una posición ABIERTA Y DESPROTEGIDA en {config.SYMBOL}. Revisa manualmente en Binance AHORA."
        )


def open_position(client: Client, side: str, filters: dict, df: pd.DataFrame):
    """
    Abre una posición a mercado y coloca TP/SL como órdenes reduce-only,
    calculados según config.SL_MODE / config.TP_MODE + riesgo fijo en USDT.
    side: 'LONG' o 'SHORT'
    """
    mark_price = float(client.futures_mark_price(symbol=config.SYMBOL)["markPrice"])
    sizing = calc_bollinger_sl_tp(df, mark_price, side, filters)
    quantity = sizing["quantity"]

    if quantity < filters["min_qty"]:
        log.warning("Cantidad calculada menor al mínimo permitido; se omite la operación.")
        return None

    order_side = "BUY" if side == "LONG" else "SELL"
    close_side = "SELL" if side == "LONG" else "BUY"

    client.futures_create_order(
        symbol=config.SYMBOL,
        side=order_side,
        type="MARKET",
        quantity=quantity,
    )

    # Precio real de entrada (aprox., usando mark price ya que el fill exacto
    # requiere consultar el trade; suficiente para fines de TP/SL en testnet)
    entry_price = mark_price
    tp_price = sizing["tp_price"]
    sl_price = sizing["sl_price"]

    tp_ok = False
    sl_ok = False

    try:
        client.futures_create_order(
            symbol=config.SYMBOL,
            side=close_side,
            type="TAKE_PROFIT_MARKET",
            stopPrice=tp_price,
            closePosition=True,
            timeInForce="GTE_GTC",
            workingType="MARK_PRICE",
        )
        tp_ok = True
    except BinanceAPIException as e:
        log.error(f"No se pudo colocar el TAKE_PROFIT: {e}")

    try:
        client.futures_create_order(
            symbol=config.SYMBOL,
            side=close_side,
            type="STOP_MARKET",
            stopPrice=sl_price,
            closePosition=True,
            timeInForce="GTE_GTC",
            workingType="MARK_PRICE",
        )
        sl_ok = True
    except BinanceAPIException as e:
        log.error(f"No se pudo colocar el STOP_LOSS: {e}")

    if not (tp_ok and sl_ok):
        # Nunca dejar una posición abierta con protección parcial o nula:
        # cancelamos lo que sí se haya colocado y cerramos a mercado.
        cancel_open_orders(client)
        emergency_close_position(client, side, quantity)
        return None

    log.info(
        f"Posición {side} abierta | qty={quantity} entry≈{entry_price:.2f} "
        f"TP={tp_price:.2f} (+{sizing['tp_pct']:.3f}%) SL={sl_price:.2f} (-{sizing['sl_pct']:.3f}%) "
        f"riesgo≈{sizing['risk_usdt']:.2f} USDT margen≈{sizing['margin_used']:.2f} USDT"
    )

    return {
        "side": side,
        "entry_price": entry_price,
        "quantity": quantity,
        "notional": sizing["notional"],
        "margin_used": sizing["margin_used"],
        "tp_price": tp_price,
        "sl_price": sl_price,
    }


def wait_for_position_close(client: Client, position: dict, guard: DailyGuard):
    """
    Espera (polling) hasta que la posición se cierre. Se cierra de una de tres formas:
    1. TP o SL (órdenes ya colocadas en Binance) se ejecutan solas.
    2. Salida anticipada: si la posición está en ganancia y el Estocástico o
       el Alligator avisan que la tendencia se revierte, se cierra a mercado
       para asegurar esa ganancia (en vez de esperar al TP original).
    """
    reason = "TP/SL"

    while True:
        time.sleep(config.LOOP_SLEEP_SECONDS)
        amt = get_open_position_amt(client)
        if amt == 0:
            reason = "TP/SL"
            break

        mark_price = float(client.futures_mark_price(symbol=config.SYMBOL)["markPrice"])
        if position["side"] == "LONG":
            unrealized = (mark_price - position["entry_price"]) * position["quantity"]
        else:
            unrealized = (position["entry_price"] - mark_price) * position["quantity"]

        # Solo se evalúa la salida anticipada si la posición está en ganancia:
        # el objetivo es asegurar ganancia neta, nunca adelantar una pérdida
        # (de eso ya se encarga el SL).
        if unrealized > 0:
            df, df_trend = get_market_data(client)
            if get_exit_signal(df, position["side"], config, df_trend=df_trend):
                cancel_open_orders(client)
                closed = manual_close_position(
                    client, position["side"], position["quantity"],
                    reason="salida anticipada por reversión de tendencia",
                )
                if closed:
                    reason = "Salida anticipada (reversión de tendencia, ganancia asegurada)"
                    break
                # Si el cierre manual falló, seguimos el loop; el TP/SL
                # original sigue en pie como red de seguridad. Hay que
                # recolocarlo porque lo cancelamos arriba.
                log.warning("No se pudo cerrar anticipadamente; se re-colocan TP/SL originales.")
                _restore_tp_sl(client, position)

    mark_price = float(client.futures_mark_price(symbol=config.SYMBOL)["markPrice"])
    if position["side"] == "LONG":
        pnl = (mark_price - position["entry_price"]) * position["quantity"]
    else:
        pnl = (position["entry_price"] - mark_price) * position["quantity"]

    today = datetime.now(timezone.utc).date()
    guard.register_trade(pnl, today)

    log_trade({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "side": position["side"],
        "entry_price": position["entry_price"],
        "exit_price": mark_price,
        "quantity": position["quantity"],
        "notional": position["notional"],
        "margin_used": position["margin_used"],
        "pnl_usdt": round(pnl, 4),
        "reason": reason,
    })

    log.info(f"Posición cerrada | PnL≈{pnl:.4f} USDT | motivo={reason} | trades hoy={guard.trades_today}")
    cancel_open_orders(client)


def _restore_tp_sl(client: Client, position: dict):
    close_side = "SELL" if position["side"] == "LONG" else "BUY"
    try:
        client.futures_create_order(
            symbol=config.SYMBOL, side=close_side, type="TAKE_PROFIT_MARKET",
            stopPrice=position["tp_price"], closePosition=True,
            timeInForce="GTE_GTC", workingType="MARK_PRICE",
        )
        client.futures_create_order(
            symbol=config.SYMBOL, side=close_side, type="STOP_MARKET",
            stopPrice=position["sl_price"], closePosition=True,
            timeInForce="GTE_GTC", workingType="MARK_PRICE",
        )
    except BinanceAPIException as e:
        log.critical(
            f"⚠️⚠️ No se pudieron re-colocar TP/SL tras un cierre anticipado fallido. "
            f"Posición podría quedar desprotegida. Revisa manualmente. Error: {e}"
        )


def main():
    log.info("Iniciando bot de scalping (TESTNET)" if config.TESTNET else "⚠️ INICIANDO EN PRODUCCIÓN REAL ⚠️")
    client = make_client()
    setup_account(client)
    filters = get_symbol_filters(client, config.SYMBOL)
    guard = DailyGuard()

    ensure_log_file()

    balance = get_available_balance(client)
    log.info(f"Balance disponible: {balance:.2f} USDT (config: {config.CAPITAL_USDT} USDT)")
    log.info(
        f"Riesgo por operación: {config.RISK_USDT_PER_TRADE} USDT | "
        f"Ratio ganancia/riesgo objetivo: {config.REWARD_RISK_RATIO}x | "
        f"Timeframe entrada: {config.INTERVAL} | Timeframe tendencia: {config.TREND_INTERVAL}"
    )

    while True:
        try:
            today = datetime.now(timezone.utc).date()
            can_trade, reason = guard.can_trade(today)
            if not can_trade:
                log.warning(f"Bot pausado: {reason}. Reintentando en el próximo ciclo del día.")
                time.sleep(60)
                continue

            df, df_trend = get_market_data(client)
            signal = get_entry_signal(df, config, df_trend=df_trend)

            if signal:
                mark_price = float(client.futures_mark_price(symbol=config.SYMBOL)["markPrice"])
                if config.USE_ORDERBOOK_FILTER and not orderbook_filter_ok(client, signal, mark_price):
                    time.sleep(config.LOOP_SLEEP_SECONDS)
                    continue

                log.info(f"Señal detectada: {signal}")
                position = open_position(client, signal, filters, df)
                if position:
                    wait_for_position_close(client, position, guard)
            else:
                time.sleep(config.LOOP_SLEEP_SECONDS)

        except BinanceAPIException as e:
            log.error(f"Error de la API de Binance: {e}")
            time.sleep(10)
        except KeyboardInterrupt:
            log.info("Detenido manualmente.")
            break
        except Exception as e:
            log.exception(f"Error inesperado: {e}")
            time.sleep(10)


if __name__ == "__main__":
    main()
