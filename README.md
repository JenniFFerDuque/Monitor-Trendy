# 🎄 Monitor del Calendario de Adviento Trendy

Vigila la tienda oficial (www.maquillajetrendyshop.com, plataforma VTEX) las 24 horas y te manda a Telegram el link de compra con un botón **🛒 Comprar ahora** apenas el calendario tenga stock. Corre en la nube, así que funciona con tu computador apagado.

## Cómo funciona

```
 Servidor en la nube (Railway, 24/7)
 ┌──────────────────────────────────────────────┐
 │  Bucle del monitor                           │
 │   cada 2–5 min + segundos aleatorios ───────────► API pública VTEX de Trendy
 │   (turbo ~1 min si aparece publicado)        │     · catálogo  · intelligent search
 │                    │ detecta                 │
 │                    ▼                         │
 │  Alerta ───────────────────────────────────────► Telegram (tu celular)
 │                                              │
 │  Panel web: registro en vivo, configuración, │
 │  probar Telegram, simular detección          │◄── tú, desde cualquier navegador
 └──────────────────────────────────────────────┘
```

- **Una consulta liviana por revisión**: cada revisión pide solo 20 productos y alterna entre el catálogo y la búsqueda inteligente de VTEX. Así se puede revisar más seguido con la misma carga.
- **Tres modos automáticos** (según la fecha de Bogotá):
  | Modo | Cuándo | Frecuencia |
  |---|---|---|
  | Normal | fuera de la ventana | 2–5 min + 0–45 s al azar |
  | Ventana | 10 al 20 de noviembre (configurable) | 50–100 s + 0–15 s al azar |
  | Turbo | el calendario aparece publicado sin stock (3 h) | ~30 s, solo consulta ese producto |
- **Tráfico natural**: tiempos aleatorios, fuente y orden rotativos, navegador e idioma variables.
- **Avisos**:
  - `👀 Publicado (aún sin stock)` → activa el turbo.
  - `🎄 ¡DISPONIBLE!` → nombre, precio, unidades y botón **Comprar ahora**, repetido 3 veces cada 20 s. Si se agota y reponen, vuelve a avisar.
  - `💓 Monitor activo` cada 6 h.
  - `⚠️` si la tienda falla 5 veces seguidas, y `✅` cuando se recupera.
- **No se detiene**: el bucle se reinicia solo ante cualquier fallo; si la tienda da errores espera un poco más (máx. 4 min en la ventana, 10 min fuera) y reintenta. Configuración, detecciones y registro se guardan en `/data`.

## Puesta en marcha (≈15 minutos)

### 1. Crea el bot de Telegram
1. En Telegram abre **@BotFather** → `/newbot` → ponle nombre. Copia el **token** (`123456:ABC…`).
2. Abre el chat con tu bot nuevo y envíale `/start` (sin esto no puede escribirte).
3. Para tu **chat ID**: abre en el navegador `https://api.telegram.org/bot<TU_TOKEN>/getUpdates` y copia el número de `"chat":{"id": …}`. (Alternativa: escribe a **@userinfobot**.)

### 2. Sube el código a GitHub
Crea un repositorio **privado** (por ejemplo `trendy-monitor`) y sube esta carpeta tal cual.

### 3. Despliega en Railway (24/7)
1. Entra a railway.com con tu cuenta de GitHub → **New Project → Deploy from GitHub repo** → elige el repositorio. Detecta el `Dockerfile` solo.
2. En el servicio → **Variables**, agrega:

   | Variable | Valor |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | el token de BotFather |
   | `TELEGRAM_CHAT_ID` | tu chat ID |
   | `PANEL_PASSWORD` | una contraseña para el panel |
   | `PANEL_USER` | (opcional) usuario, por defecto `admin` |
   | `PORT` | `8000` |

3. Clic derecho en el servicio → **Attach Volume** → ruta de montaje **`/data`** (aquí se guarda el registro y la configuración).
4. **Settings → Networking → Generate Domain**. Esa URL es tu panel.
5. Deja **1 sola réplica** (el monitor vive dentro del servidor; dos réplicas duplicarían avisos).

Costo: el plan Hobby de Railway cuesta 5 USD/mes e incluye 5 USD de uso; un proceso tan pequeño como este normalmente cabe dentro de eso.

### 4. Prueba
Abre la URL, entra con `admin` + tu contraseña y:
1. **📨 Probar notificación de Telegram** → debe llegarte un mensaje.
2. **🧪 Simular detección** → te llega la alerta completa con el botón de compra (marcada como SIMULACIÓN; no altera nada real).
3. Mira el **registro en vivo**: cada revisión aparece con su resultado.

## Panel

- **Registro en vivo** (sin recargar): filtros por tipo (revisiones, alertas, avisos, errores, pruebas, detalle), número de líneas visibles, seguir lo nuevo, descargar y borrar.
- **Configuración** (se aplica de inmediato y reprograma la próxima revisión):
  - Intervalo mínimo / máximo en minutos (recomendado 2–5) y segundos aleatorios extra.
  - Términos de búsqueda, palabras obligatorias en el nombre y palabras para descartar.
  - Modo turbo, aviso de "publicado sin stock", mensaje "sigo vivo", umbral de errores.
  - Registro detallado: muestra cada consulta individual y la hora exacta de la próxima.
- **Revisar ahora** y **Pausar / Reanudar**.

> Si aparece un falso positivo (otro producto con "calendario" en el nombre), agrega una palabra en **Descartar si contiene**.

## Alternativas de hosting

- **Gratis**: una VM "Always Free" de Oracle Cloud con Docker (`docker build -t trendy . && docker run -d --restart=always -p 80:8000 -v trendy:/data --env-file .env trendy`). Más pasos, y Oracle puede reclamar instancias que considere inactivas.
- **No recomendado**: el plan gratis de Render; apaga el servicio tras 15 min sin visitas y borra el disco al reiniciar, así que el monitor se detendría.

## Probar en tu computador

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
uvicorn app:app --port 8000
# abre http://localhost:8000
```

## Registro de consultas (Excel / Google Sheets)

Cada consulta HTTP a la tienda se guarda como una fila en `/data/consultas.csv`. Columnas: fecha y hora, hora del día, día de la semana, revisión, motivo, modo, fuente VTEX, término, código HTTP, éxito, tiempo de respuesta (ms), tamaño (KB), productos devueltos, coincidencias, con stock, unidades, precio, segundos desde la consulta anterior y error.

- **Excel**: panel → *Datos para análisis* → **Descargar Excel** (hoja `Consultas` como tabla con filtros + hoja `Diccionario`).
- **CSV**: mismo contenido, para pandas / Power BI.
- **Google Sheets (opcional)**: pega `google-sheets/Codigo.gs` en *Extensiones → Apps Script* de una hoja, cambia `TOKEN`, implementa como *Aplicación web* (ejecutar como yo, acceso: cualquier usuario) y agrega en Railway:

  | Variable | Valor |
  |---|---|
  | `SHEETS_WEBHOOK_URL` | URL `/exec` de la aplicación web |
  | `SHEETS_TOKEN` | la misma clave de `TOKEN` |

  Las filas se envían en lote cada 5 min (de inmediato si aparece el calendario). Si Google falla, se guardan y se reintenta.
