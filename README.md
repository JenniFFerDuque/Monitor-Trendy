# 🎄 Monitor del Calendario de Adviento Trendy

Consulta la tienda oficial (www.maquillajetrendyshop.com, plataforma VTEX) las 24 horas y te manda a Telegram el link de compra con un botón **🛒 Comprar ahora** apenas el calendario tenga stock. Corre en la nube, así que funciona con tu computador apagado.

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



## Registro de consultas (Excel / Google Sheets)

Cada consulta HTTP a la tienda se guarda como una fila en `/data/consultas.csv`. Columnas: fecha y hora, hora del día, día de la semana, revisión, motivo, modo, fuente VTEX, término, código HTTP, éxito, tiempo de respuesta (ms), tamaño (KB), productos devueltos, coincidencias, con stock, unidades, precio, segundos desde la consulta anterior y error.

- **Excel**: panel → *Datos para análisis* → **Descargar Excel** (hoja `Consultas` como tabla con filtros + hoja `Diccionario`).
- **CSV**: mismo contenido, para pandas / Power BI.
- **Google Sheets (opcional)**: 

  Las filas se envían en lote cada 5 min (de inmediato si aparece el calendario). Si Google falla, se guardan y se reintenta.
