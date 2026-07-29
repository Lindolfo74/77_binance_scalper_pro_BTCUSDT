# Bot de Scalping — Binance Futures TESTNET (BTC/USDT)

⚠️ **Lee esto antes de usarlo.**

- Este bot está diseñado para operar contra la **Testnet de Binance Futures**
  (dinero ficticio). Sirve para **probar lógica de entradas/salidas y TP/SL**,
  no para "validar" que vayas a ganar dinero en real.
- La estrategia incluida (Alligator + Estocástico + Bandas de Bollinger) es
  la que definió el propio usuario del proyecto, pero **ningún bot puede
  garantizar ganancias**, y menos con apalancamiento.
- Con apalancamiento 7x aislado, un movimiento en contra de ~14% liquida el
  margen de esa posición. El diseño de este bot dimensiona cada operación
  para arriesgar solo un monto fijo en USDT (ver sección de riesgo) mucho
  antes de llegar a ese punto, pero en mercados muy volátiles el precio
  puede saltar ("slippage") y el resultado real puede diferir.
- Si en algún momento cambias `TESTNET=false`, estarás operando con dinero
  real. Este proyecto no fue pensado ni probado para eso — hazlo bajo tu
  propio riesgo y solo si entendés completamente el código.

---

## 1. Crear cuenta y API Key en Binance Futures Demo Trading

Binance retiró el sitio viejo de Testnet (`testnet.binancefuture.com`) y lo
reemplazó por **Demo Trading**, con una nueva URL base de API
(`demo-fapi.binance.com`). Este proyecto ya está actualizado para usarla.

1. Entra a https://demo.binance.com
2. Inicia sesión (revisa en pantalla el método de login vigente; Binance ha
   ido cambiando esto, puede pedir tu cuenta normal de Binance o GitHub).
3. Te dan automáticamente un balance ficticio en USDT.
4. Busca la sección de **API Key** dentro de Demo Trading y genera un par
   API Key / Secret. Estas claves **solo sirven para la cuenta demo**, no dan
   acceso a tu cuenta real de Binance.
5. Como Binance ha estado migrando este sistema, si algo no coincide con lo
   descrito aquí, revisa el aviso oficial más reciente en su página de soporte.

## 2. Configuración local

```bash
cd binance_scalper
cp .env.example .env
# Edita .env y pega tu BINANCE_API_KEY y BINANCE_API_SECRET de testnet
```

Revisa también en `.env` (todos los parámetros se explican con detalle en la
sección 8, "Cómo ajustar la estrategia"):
- `CAPITAL_USDT`: pon el mismo monto que tengas en la testnet (por defecto 200).
- `LEVERAGE`, `MARGIN_TYPE`: 7x / ISOLATED por defecto.
- `RISK_USDT_PER_TRADE` / `REWARD_RISK_RATIO`: cuánto arriesgas por operación
  en dólares y qué múltiplo de eso buscas de ganancia.
- `BB_PERIOD`, `BB_STD`, `MIN_SL_PCT`, `MAX_SL_PCT`: cómo se calcula el SL a
  partir de las Bandas de Bollinger.
- `STOCH_*`: parámetros del Estocástico (entradas y salidas).
- `ALLIGATOR_*`: parámetros del Alligator (filtro de tendencia).
- `MAX_TRADES_PER_DAY`, `MAX_CONSECUTIVE_LOSSES`, `DAILY_MAX_LOSS_PCT`: son
  "kill-switches" para que el bot se detenga solo si algo va mal.

## 3. Ejecutar localmente (sin Docker)

```bash
python3 -m venv venv
source venv/bin/activate          # En Windows: venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

Vas a ver logs en consola cada vez que detecta una señal, abre una posición,
coloca TP/SL, y cuando la posición se cierra (por TP/SL o por salida
anticipada). Todas las operaciones quedan además registradas en `trades_log.csv`.

## 4. Ejecutar con Docker (recomendado, incluso en local)

```bash
docker compose up --build -d     # -d para correrlo en segundo plano
docker compose logs -f           # ver logs en vivo
docker compose down              # detenerlo
```

## 5. Desplegar en la nube

Cualquier proveedor que soporte Docker sirve. Dos caminos simples:

### Opción A — VPS económico (DigitalOcean, Hetzner, Linode, AWS Lightsail, etc.)

1. Crea una VM pequeña (1 vCPU / 1GB RAM alcanza de sobra para este bot).
2. Instala Docker: `curl -fsSL https://get.docker.com | sh`
3. Sube el proyecto (por ejemplo con `scp -r binance_scalper usuario@ip:~/` o clonándolo desde tu propio repo git privado).
4. En el servidor:
   ```bash
   cd binance_scalper
   cp .env.example .env   # y edítalo con tus claves (usa `nano .env`)
   docker compose up --build -d
   ```
5. El bot queda corriendo 24/7 gracias a `restart: unless-stopped` en el compose.
   Revisa logs con `docker compose logs -f`.

### Opción B — Plataformas "sin servidor que gestionar" (Railway, Render, Fly.io)

Estas plataformas detectan el `Dockerfile` automáticamente:

1. Sube el proyecto a un repositorio (puede ser privado) en GitHub/GitLab.
2. Conecta ese repo en la plataforma elegida.
3. En la sección de **variables de entorno / secrets** del proyecto, agrega
   una por una todas las variables que están en `.env.example` (con tus
   valores reales de testnet).
4. Despliega. Estas plataformas suelen tener capas gratuitas limitadas;
   revisa los precios actuales de cada una antes de dejarlo corriendo mucho tiempo.

⚠️ En cualquiera de los dos casos: **nunca subas tu archivo `.env` a un
repositorio público**. Usa siempre variables de entorno / secrets del
proveedor, y agrega `.env` a tu `.gitignore` (ya viene incluido).

## 6. Buenas prácticas de seguridad (para cuando pienses en producción)

- En Binance real, crea API keys con **permisos solo de trading de
  futuros**, nunca de retiro ("withdrawal").
- Usa **whitelisting de IP** en la API key, restringido a la IP fija de tu
  servidor en la nube.
- Nunca hardcodees claves en el código ni las subas a git.
- Considera empezar con un capital mucho menor al que estás dispuesto a
  perder por completo, y correr el bot en real varias semanas en paralelo
  con la testnet antes de confiar en sus resultados.

## 7. Estructura del proyecto

```
binance_scalper/
├── bot.py              # loop principal: conecta a Binance, evalúa señales, opera
├── strategy.py         # Alligator (tendencia), Estocástico (entradas/salidas), Bollinger
├── risk.py             # tamaño de posición, cálculo de SL/TP, kill-switches diarios
├── config.py           # carga de configuración desde .env
├── requirements.txt
├── .env.example
├── Dockerfile
├── docker-compose.yml
└── trades_log.csv      # se genera solo, historial de operaciones
```

## 8. Cómo funciona la estrategia

El bot combina tres indicadores, cada uno con un rol distinto:

| Indicador | Rol |
|---|---|
| **Alligator** (Bill Williams) | Define la **tendencia**. Solo abre LONG si la tendencia es alcista y SHORT si es bajista. Si las líneas están entrelazadas ("boca cerrada" / mercado lateral), el bot no opera. |
| **Estocástico** | Marca las **entradas** (cruce %K/%D saliendo de sobrecompra/sobreventa, siempre a favor de la tendencia del Alligator) y las **salidas anticipadas** (cruce en contra). |
| **Bandas de Bollinger** | Se usan para calcular el **SL**: la distancia desde el precio de entrada hasta la banda opuesta refleja la volatilidad reciente. No se usan para decidir si entrar. |

### Entradas
- **LONG**: tendencia UP (Alligator) + el Estocástico cruza hacia arriba viniendo de zona de sobreventa.
- **SHORT**: tendencia DOWN (Alligator) + el Estocástico cruza hacia abajo viniendo de zona de sobrecompra.

### Tamaño de posición y SL/TP (riesgo fijo en dólares)
En vez de arriesgar un % variable del capital, el bot arriesga siempre un
**monto fijo en USDT** (`RISK_USDT_PER_TRADE`, por defecto 4 USDT):

1. Se calcula la distancia del SL según la Banda de Bollinger opuesta al
   precio de entrada (acotada entre `MIN_SL_PCT` y `MAX_SL_PCT` para que
   nunca sea absurdamente chica ni grande).
2. El tamaño de la posición se ajusta para que, si se toca ese SL, la
   pérdida sea exactamente `RISK_USDT_PER_TRADE`.
3. El TP se coloca a `distancia_SL × REWARD_RISK_RATIO` (por defecto 4x), de
   modo que la ganancia potencial es siempre varias veces mayor que el
   riesgo — con los valores por defecto, arriesgar ~4 USDT buscando ~16 USDT.

### Salida anticipada (asegurar ganancia antes de que se revierta la tendencia)
Mientras la posición está abierta, el bot revisa periódicamente si está en
**ganancia** (`unrealized > 0`). Si lo está, y el Estocástico o el Alligator
muestran que la tendencia se está revirtiendo, el bot **cierra la posición a
mercado de inmediato** para asegurar esa ganancia neta, en vez de esperar a
que el precio retroceda hasta el TP original (o peor, hasta el SL). Esto
nunca se activa si la posición está en pérdida — de eso se sigue encargando
el SL normal.

### Protección ante fallos (failsafe)
Si al abrir una posición el TP y/o el SL no se pudieran colocar (por ejemplo,
un error de precisión de precio de Binance), el bot **cancela lo que sí se
haya colocado y cierra la posición a mercado de inmediato**. Nunca debe
quedar una posición abierta sin ningún tipo de protección — si aun así el
cierre de emergencia fallara, el bot lo deja registrado en el log como
`CRITICAL` para que lo revises manualmente cuanto antes.

## 9. Ajustar la estrategia

Todo lo que puedes tunear está en `.env`, sin tocar código:
- `RISK_USDT_PER_TRADE` / `REWARD_RISK_RATIO`: cuánto arriesgas y qué
  múltiplo de ganancia buscas.
- `BB_PERIOD`, `BB_STD`, `MIN_SL_PCT`, `MAX_SL_PCT`: qué tan sensible es el
  SL a la volatilidad reciente.
- `STOCH_K_PERIOD`, `STOCH_D_PERIOD`, `STOCH_SMOOTH_K`, `STOCH_OVERSOLD`,
  `STOCH_OVERBOUGHT`: sensibilidad del Estocástico para entradas/salidas.
- `ALLIGATOR_JAW/TEETH/LIPS_PERIOD` y `_SHIFT`, `ALLIGATOR_MIN_SEPARATION_PCT`:
  qué tan estricta es la detección de tendencia (subir la separación mínima
  reduce operaciones en mercado lateral).
- Temporalidad de las velas (`INTERVAL`: 1m, 3m, 5m...). En 1m las señales
  son más frecuentes pero también más ruidosas; si ves que opera demasiado
  seguido, prueba con 3m o 5m.
- `MAX_TRADES_PER_DAY`, `MAX_CONSECUTIVE_LOSSES`, `DAILY_MAX_LOSS_PCT`,
  `LOOP_SLEEP_SECONDS`: límites diarios de seguridad.

Si quieres una estrategia distinta, lo mejor es modificar `strategy.py` — las
funciones `get_entry_signal()` y `get_exit_signal()` son los puntos que el
bot consulta para decidir si entra o sale de una posición.
