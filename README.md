# KIV/PC Watchdog

Monitor výsledků semestrální práce předmětu KIV/PC na ZČU.
Automaticky kontroluje změny v hodnocení a odesílá notifikace na Discord.

## Konfigurace

Zkopíruj šablony a vyplň své údaje:

```bash
cp .env.template .env
cp config.json.template config.json
cp users.json.template users.json
```

### .env

```env
ORION_USERNAME=tvuj_orion_login
ORION_PASSWORD=tvoje_heslo
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

### config.json

```json
{
    "my_student_id": "A00B0000P",
    "discord_user_id_to_ping": "123456789",
    "check_interval_seconds": 60
}
```

### users.json (volitelné)

Mapování osobních čísel na Discord user ID pro pingování studentů:

```json
{
    "A00B0000P": "discord_user_id",
    "A00B0001P": "discord_user_id"
}
```

## Spuštění (Docker - Doporučeno)

**První spuštění:**

```bash
docker compose up -d --build
```

**Ovládání:**

```bash
docker compose stop      # Zastavit
docker compose start     # Spustit
docker compose restart   # Restartovat
docker compose down      # Úplně vypnout a odstranit kontejner
docker compose logs -f   # Zobrazit logy
```

**Rebuild (po změně kódu):**

```bash
docker compose up -d --build
```

### Kdy je potřeba restart/rebuild?

| Změna | Akce |
|-------|------|
| `users.json` | Nic - načítá se automaticky při každé kontrole |
| `.env` | `docker compose restart` |
| `config.json` | `docker compose restart` |
| `watchdog.py` | `docker compose up -d --build` |

## Spuštění (Lokálně)

```bash
pip install -r requirements.txt
python watchdog.py
```
