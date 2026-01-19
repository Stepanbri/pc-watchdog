# Použijeme lehký Linux (Debian Bookworm) s Pythonem 3.11
FROM python:3.11-slim-bookworm

# Nastavení proměnných prostředí
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Nastavíme pracovní adresář
WORKDIR /app

# 1. Instalace systémových závislostí a Chromia
# Instalujeme 'chromium' a 'chromium-driver' přímo z repozitáře Debianu.
# Je to stabilnější než stahování přes webdriver-manager uvnitř Dockeru.
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# 2. Instalace Python knihoven
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. Zkopírování skriptu
COPY watchdog.py .

# 4. Spuštění
CMD ["python", "watchdog.py"]