# AGENT.md — idealista-notifier

## Descripción del proyecto

Bot de scraping de [idealista.com](https://www.idealista.com) para **pisos en alquiler en Sevilla**.
Monitoriza continuamente nuevos anuncios y envía notificaciones a un **grupo de Telegram**.

Objetivo: detectar nuevos pisos disponibles en alquiler en Sevilla antes de que desaparezcan.

---

## Stack tecnológico

| Capa | Tecnología |
|---|---|
| Lenguaje | Python 3.9 |
| Scraping | `cloudscraper` (bypass Cloudflare) + `requests` |
| Parsing HTML | `beautifulsoup4` |
| Notificaciones | Telegram Bot API |
| User-Agent spoofing | `fake_useragent` |
| Config / secrets | `python-dotenv` |
| Contenedor | Docker + Docker Compose |
| Deploy cloud | Railway.app |

**Dependencias** (`requirements.txt`):
```
requests==2.31.0
beautifulsoup4==4.12.2
python-dotenv==1.0.0
fake_useragent==2.0.3
cloudscraper==1.2.71
```

---

## Estructura del proyecto

```
idealista-notifier/
├── src/
│   └── scraper.py          # Entrypoint principal — loop de scraping y notificaciones
├── data/                   # Generado en runtime (no en repo)
│   ├── seen_listings.json  # Deduplicación (deque de últimas 100 URLs)
│   └── error_log.json      # Estado de errores 403 (evita spam de alertas)
├── .env                    # Secrets (gitignored)
├── .env.example            # Plantilla de secrets
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## Arquitectura y flujo de datos

```
[Loop infinito, intervalo ~3h]
        │
        ▼
scrape_idealista()
        │
        ├─ cloudscraper GET → página de resultados Idealista
        │   (Sevilla, alquiler, ordenado por fecha de publicación)
        │
        ├─ BeautifulSoup parsea <article class="item">
        │
        ├─ Por cada anuncio:
        │   ├─ Extrae: título, URL, precio, habitaciones, tamaño, planta, descripción
        │   ├─ Aplica filtros: EXCLUDED_AREAS, EXCLUDED_TERMS, EXCLUDED_FLOORS
        │   ├─ Comprueba deduplicación (seen_listings.json)
        │   │
        │   └─ [Solo anuncios NUEVOS]
        │       ├─ Extrae imagen del anuncio
        │       └─ Encola notificación
        │
        ├─ Envío por lotes de notificaciones Telegram (0.5s entre cada una)
        └─ Guarda seen_listings.json actualizado
```

### Formato del mensaje Telegram

```
🏠 Nuevo piso en alquiler en Sevilla

<b>Título del anuncio</b>

💰 800 €/mes
🛏 3 hab.
📐 75 m²
🏢 2ª planta

🔗 Ver anuncio
```

Con imagen adjunta si está disponible. Para áticos, el header cambia a `🚨 ÁTICO DISPONIBLE 🚨`.

---

## Configuración

### Variables de entorno (`.env`)

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_group_chat_id_here
```

> El `TELEGRAM_CHAT_ID` de un grupo es un número negativo (ej: `-1001234567890`).
> El bot debe estar añadido como miembro del grupo.

### Parámetros in-code (`src/scraper.py`)

```python
IDEALISTA_URL = "https://www.idealista.com/alquiler-viviendas/sevilla-sevilla/?ordenado-por=fecha-publicacion-desc"
EXCLUDED_AREAS = []             # Barrios a excluir
EXCLUDED_TERMS = []             # Palabras clave a filtrar en descripción
EXCLUDED_FLOORS = []            # Tipos de planta a excluir (ej. "Bajo", "Semisótano")
MAX_LISTINGS = 100              # Tamaño máximo del deque de deduplicación
```

---

## Comandos

### Ejecución local

```bash
# Instalar dependencias
pip install -r requirements.txt

# Crear directorio de datos (paths hardcodeados a /app/data/)
mkdir -p /app/data

# Configurar secrets
cp .env.example .env
# Editar .env con tu token de Telegram y el chat ID del grupo

# Arrancar el bot
python3 src/scraper.py
```

### Docker

```bash
# Con Docker Compose (requiere .env con TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID)
docker-compose up -d
docker-compose logs -f
```

### Railway.app (deploy cloud)

```bash
railway init && railway up
railway variables set TELEGRAM_BOT_TOKEN=xxx
railway variables set TELEGRAM_CHAT_ID=xxx
railway logs -f
```

---

## Patrones y convenciones

- **Anti-bot evasion**: `cloudscraper` + `fake_useragent` (Chrome en Windows) + delays aleatorios.
- **Deduplicación stateful**: `collections.deque(maxlen=100)` persistida como JSON.
- **One-shot error alerting**: `error_log.json` previene spam — el alert de 403 se envía una sola vez, se limpia cuando vuelve el acceso.
- **Notificaciones en lote**: todos los nuevos anuncios de un ciclo se encolan y envían en batch al final, no mid-loop.
- **sendPhoto con fallback**: intenta enviar la imagen como foto de Telegram; si falla, envía el mensaje como texto.

---

## Bugs conocidos / deuda técnica

| Problema | Archivo | Descripción |
|---|---|---|
| Paths hardcodeados | `src/scraper.py` | Los datos se guardan en `/app/data/` (path de contenedor Docker). Ejecutar localmente sin Docker requiere crear ese directorio manualmente. |
| Sin test suite | — | No hay tests automatizados. |
| Sin CI/CD | — | No hay pipeline de integración continua. |

---

## Datos persistentes en runtime

| Archivo | Descripción |
|---|---|
| `/app/data/seen_listings.json` | URLs ya procesadas (deque de max 100 elementos) |
| `/app/data/error_log.json` | Estado del último error 403 (para evitar notificaciones duplicadas) |

Estos archivos se montan como volumen en Docker para sobrevivir reinicios del contenedor.
