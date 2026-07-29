FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# El archivo .env NO se copia a la imagen; se inyecta en tiempo de ejecución
# como variables de entorno (ver README, sección de despliegue en la nube).

# trades_log.csv se genera solo al arrancar el bot y se persiste vía el
# volumen definido en docker-compose.yml.

CMD ["python", "bot.py"]
