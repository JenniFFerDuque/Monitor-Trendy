"""
Monitor de stock del Calendario de Adviento Trendy (tienda VTEX)
- Consulta la API pública de catálogo de VTEX con intervalo configurable + segundos aleatorios.
- Avisa por Telegram con botón "Comprar ahora" en cuanto hay stock.
- Panel web con registro de actividad en vivo (SSE), configuración y botones de prueba.
"""
import asyncio
import html
import json
import os
import random
import secrets
import time
import unicodedata
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from datalog import KEYS, QueryLog

# ---------------------------------------------------------------- ajustes base
STORE = os.getenv("STORE_URL", "https://www.maquillajetrendyshop.com").rstrip("/")
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = DATA_DIR / "config.json"
STATE_FILE = DATA_DIR / "state.json"
LOG_FILE = DATA_DIR / "activity.log"
STATIC = Path(__file__).parent / "static"

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()
PANEL_USER = os.getenv("PANEL_USER", "admin")
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "")
TZ = timezone(timedelta(hours=-5))  # Bogotá

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; SM-A546E) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
]

DEFAULT_CONFIG = {
    # Modo normal (fuera de la ventana de lanzamiento)
    "interval_min": 2.0,              # minutos
    "interval_max": 5.0,              # minutos
    "jitter_max_seconds": 45,         # segundos aleatorios extra
    # Ventana de lanzamiento: revisiones livianas y más frecuentes
    "window_enabled": True,
    "window_start": "2026-11-10",
    "window_end": "2026-11-20",        # inclusive
    "window_interval_min_seconds": 50,
    "window_interval_max_seconds": 100,
    "window_jitter_max_seconds": 15,
    # Búsqueda
    "search_terms": ["calendario"],
    "required_words": ["calendario"],  # el nombre debe contener todas
    "exclude_words": [],               # descarta si contiene alguna
    "use_intelligent_search": True,    # alterna catálogo e Intelligent Search
    # Alertas
    "notify_published_without_stock": True,
    "turbo_enabled": True,             # cuando aparece publicado sin stock
    "turbo_interval_seconds": 30,
    "turbo_duration_minutes": 180,
    "alert_repeats": 3,                # avisos de "disponible" (cada 20 s)
    "cart_qty": 1,                     # unidades que agrega el botón del carrito
    "sales_channel": 1,                # canal de venta de VTEX (sc) para el enlace al carrito
    "heartbeat_hours": 6,              # "sigo vivo" (0 = apagado)
    "error_alert_threshold": 5,        # errores seguidos antes de avisar
    "log_verbose": False,
    "paused": False,
}


def now() -> datetime:
    return datetime.now(TZ)


def fmt(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(path)


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return " ".join(text.lower().split())


def as_list(value) -> list[str]:
    if isinstance(value, str):
        value = value.replace("\n", ",").split(",")
    return [str(v).strip() for v in (value or []) if str(v).strip()]


def sanitize_config(raw: dict, base: dict) -> dict:
    cfg = {**base, **{k: v for k, v in (raw or {}).items() if k in DEFAULT_CONFIG}}

    def num(key, lo, hi, cast=float):
        try:
            val = cast(cfg[key])
        except (TypeError, ValueError):
            val = DEFAULT_CONFIG[key]
        cfg[key] = max(lo, min(hi, val))

    num("interval_min", 1, 60)
    num("interval_max", 1, 60)
    if cfg["interval_max"] < cfg["interval_min"]:
        cfg["interval_min"], cfg["interval_max"] = cfg["interval_max"], cfg["interval_min"]
    num("jitter_max_seconds", 0, 180, int)
    num("window_interval_min_seconds", 30, 600, int)
    num("window_interval_max_seconds", 30, 600, int)
    if cfg["window_interval_max_seconds"] < cfg["window_interval_min_seconds"]:
        cfg["window_interval_min_seconds"], cfg["window_interval_max_seconds"] = \
            cfg["window_interval_max_seconds"], cfg["window_interval_min_seconds"]
    num("window_jitter_max_seconds", 0, 120, int)
    for key in ("window_start", "window_end"):
        try:
            datetime.strptime(str(cfg[key]), "%Y-%m-%d")
        except ValueError:
            cfg[key] = DEFAULT_CONFIG[key]
    if cfg["window_end"] < cfg["window_start"]:
        cfg["window_start"], cfg["window_end"] = cfg["window_end"], cfg["window_start"]
    num("turbo_interval_seconds", 20, 300, int)
    num("turbo_duration_minutes", 5, 1440, int)
    num("alert_repeats", 1, 5, int)
    num("cart_qty", 1, 5, int)
    num("sales_channel", 1, 99, int)
    num("heartbeat_hours", 0, 168, int)
    num("error_alert_threshold", 1, 100, int)
    for key in ("search_terms", "required_words", "exclude_words"):
        cfg[key] = as_list(cfg[key])
    if not cfg["search_terms"]:
        cfg["search_terms"] = list(DEFAULT_CONFIG["search_terms"])
    for key in ("use_intelligent_search", "notify_published_without_stock",
                "turbo_enabled", "window_enabled", "log_verbose", "paused"):
        v = cfg[key]
        cfg[key] = v.lower() in ("1", "true", "si", "sí", "on") if isinstance(v, str) else bool(v)
    return cfg


def money(value) -> str:
    return f"$ {value:,.0f}".replace(",", ".") if value else "precio no disponible"


def parse_product(p: dict) -> dict:
    """Normaliza un producto de la API de catálogo o de Intelligent Search."""
    name = p.get("productName") or p.get("productTitle") or ""
    if p.get("linkText"):
        link = f"{STORE}/{p['linkText']}/p"
    else:
        link = p.get("link") or STORE
        if link.startswith("/"):
            link = STORE + link
    available, qty, price, image = False, 0, None, None
    sku = seller_id = None      # la variante que se agrega al carrito
    for item in p.get("items") or []:
        if not image and item.get("images"):
            image = item["images"][0].get("imageUrl")
        for seller in item.get("sellers") or []:
            offer = seller.get("commertialOffer") or {}
            q = offer.get("AvailableQuantity") or 0
            in_stock = q > 0 or bool(offer.get("IsAvailable"))
            if in_stock:
                available = True
            # prioriza una variante con stock; si ninguna tiene, se queda con la primera
            if item.get("itemId") and (sku is None or (in_stock and not sku[1])):
                sku = (str(item["itemId"]), in_stock)
                seller_id = str(seller.get("sellerId") or "1")
            qty += q
            pr = offer.get("Price")
            if pr and (price is None or pr < price):
                price = pr
    return {
        "id": str(p.get("productId") or link),
        "name": name,
        "link": link,
        "available": available,
        "qty": qty,
        "price": price,
        "image": image,
        "sku": sku[0] if sku else None,
        "seller": seller_id or "1",
    }


def cart_link(p: dict, qty: int = 1, sc: int = 1) -> str | None:
    """Enlace oficial de VTEX que agrega la variante al carrito y abre el checkout en TU navegador."""
    if not p.get("sku"):
        return None
    return f"{STORE}/checkout/cart/add?sku={p['sku']}&qty={qty}&seller={p.get('seller') or 1}&sc={sc}"


class Monitor:
    def __init__(self):
        self.config = sanitize_config(load_json(CONFIG_FILE, {}), DEFAULT_CONFIG)
        self.state = load_json(STATE_FILE, {})
        self.state.setdefault("products", {})
        self.state.setdefault("checks", 0)
        self.state.setdefault("last_heartbeat_ts", time.time())
        self.logs: deque = deque(maxlen=1500)
        self.subscribers: set[asyncio.Queue] = set()
        self.wake = asyncio.Event()
        self.wake_reason: str | None = None
        self.busy = asyncio.Lock()
        self.next_check_at: datetime | None = None
        self.next_detail = ""
        self.last_check_at: datetime | None = None
        self.last_result: dict | None = None
        self.consecutive_errors = 0
        self.rotation: list = []
        self.qlog = QueryLog(DATA_DIR / "consultas.csv", os.getenv("SHEETS_WEBHOOK_URL", ""),
                             os.getenv("SHEETS_TOKEN", ""))
        self.started_at = now()
        self.client: httpx.AsyncClient | None = None
        self.bg_tasks: set[asyncio.Task] = set()   # referencias fuertes: evita que el GC cancele tareas
        self._load_log_tail()

    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.bg_tasks.add(task)
        task.add_done_callback(self.bg_tasks.discard)
        return task

    # ---------------------------------------------------------------- registro
    def _load_log_tail(self):
        try:
            lines = LOG_FILE.read_text("utf-8").splitlines()[-1000:]
            for line in lines:
                self.logs.append(json.loads(line))
        except Exception:
            pass

    def log(self, level: str, msg: str):
        if level == "debug" and not self.config["log_verbose"]:
            return
        entry = {"t": fmt(now()), "level": level, "msg": msg}
        self.logs.append(entry)
        print(f"[{entry['t']}] {level.upper():6} {msg}", flush=True)
        try:
            with LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            if LOG_FILE.stat().st_size > 2_000_000:
                keep = LOG_FILE.read_text("utf-8").splitlines()[-1000:]
                LOG_FILE.write_text("\n".join(keep) + "\n", "utf-8")
        except Exception:
            pass
        self.broadcast("log", entry)

    def broadcast(self, event: str, data):
        for q in list(self.subscribers):
            try:
                q.put_nowait((event, data))
            except asyncio.QueueFull:
                pass

    def broadcast_status(self):
        self.broadcast("status", self.status())

    def status(self) -> dict:
        cfg = self.config
        products = list(self.state["products"].values())
        live = next((p for p in products if p["available"] and p.get("id") != "simulacion"), None)
        if live:
            live = {**live, "cart": cart_link(live, cfg["cart_qty"], cfg["sales_channel"])}
        return {
            "server_now": time.time() * 1000,          # el panel corrige el reloj del celular con esto
            "window_enabled": cfg["window_enabled"],
            "window_start": cfg["window_start"],
            "window_end": cfg["window_end"],
            "available_product": live,
            "intervals": {"normal_min": cfg["interval_min"], "normal_max": cfg["interval_max"],
                          "window_min": cfg["window_interval_min_seconds"], "window_max": cfg["window_interval_max_seconds"],
                          "turbo": cfg["turbo_interval_seconds"]},
            "paused": self.config["paused"],
            "busy": self.busy.locked(),
            "next_check_ts": self.next_check_at.timestamp() * 1000 if self.next_check_at else None,
            "next_detail": self.next_detail,
            "last_check_at": fmt(self.last_check_at),
            "last_result": self.last_result,
            "checks": self.state["checks"],
            "consecutive_errors": self.consecutive_errors,
            "started_at": fmt(self.started_at),
            "turbo": self.turbo_active(),
            "mode": self.mode(),
            "window": f"{self.config['window_start']} → {self.config['window_end']}" if self.config["window_enabled"] else None,
            "telegram_ready": bool(TG_TOKEN and TG_CHAT),
            "password_set": bool(PANEL_PASSWORD),
            "products": products,
            "store": STORE,
            "queries_total": self.qlog.total,
            "sheets": self.qlog.sheets_status(),
        }

    # ---------------------------------------------------------------- consultas VTEX
    async def get_json(self, url: str, params: dict):
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": random.choice(["es-CO,es;q=0.9", "es-419,es;q=0.9,en;q=0.8", "es-CO,es;q=0.9,en-US;q=0.7"]),
            "Referer": STORE + "/",
        }
        return await self.client.get(url, params=params, headers=headers)

    # Cada consulta es un "plan": (etiqueta, url, parámetros). Se piden pocos resultados
    # para que cada respuesta sea pequeña.
    def search_plans(self) -> list[tuple[str, str, dict]]:
        plans = []
        for term in self.config["search_terms"]:
            plans.append((f"catálogo «{term}»", f"{STORE}/api/catalog_system/pub/products/search",
                          {"ft": term, "_from": 0, "_to": 19}))
            if self.config["use_intelligent_search"]:
                plans.append((f"intelligent-search «{term}»",
                              f"{STORE}/api/io/_v/api/intelligent-search/product_search/",
                              {"query": term, "count": 20, "hideUnavailableItems": "false"}))
        return plans

    def pending_ids(self) -> list[str]:
        limit = self.config["turbo_duration_minutes"] * 60
        return [pid for pid, r in self.state["products"].items()
                if not r["available"] and time.time() - r.get("first_seen_ts", 0) < limit]

    def next_plans(self, reason: str) -> list[tuple[str, str, dict]]:
        # Turbo: solo pregunta por el producto ya conocido (respuesta mínima)
        if self.turbo_active() and reason == "programada":
            return [(f"producto {pid}", f"{STORE}/api/catalog_system/pub/products/search",
                     {"fq": f"productId:{pid}"}) for pid in self.pending_ids()]
        # Revisión manual, de inicio o de reanudación: todas las rutas
        if reason != "programada":
            plans = self.search_plans()
            random.shuffle(plans)
            return plans
        # Revisión programada: UNA sola consulta, rotando entre fuentes y términos
        if not self.rotation:
            self.rotation = self.search_plans()
            random.shuffle(self.rotation)
        return [self.rotation.pop()]

    async def fetch_plan(self, plan, reason: str = "", mode: str = "") -> list[dict]:
        """Hace UNA consulta, devuelve los productos ya normalizados y la registra como fila de datos."""
        label, url, params = plan
        source = label.split(" ")[0]
        term = params.get("ft") or params.get("query") or params.get("fq", "").replace("productId:", "")
        when, t0 = now(), time.perf_counter()
        status, size, found, err = None, 0, [], ""
        try:
            r = await self.get_json(url, params)
            status, size = r.status_code, len(r.content)
            if status not in (200, 206):
                raise RuntimeError(f"HTTP {status}")
            data = r.json()
            raw = data if isinstance(data, list) else (data.get("products") or [])
            found = [p for p in map(parse_product, raw) if p["name"]]
            self.log("debug", f"{label}: {len(found)} resultados")
            return found
        except Exception as e:
            err = str(e) or e.__class__.__name__
            raise
        finally:
            hits = [p for p in found if self.matches(p["name"])]
            prices = [p["price"] for p in hits if p["price"]]
            self.qlog.record(
                when, revision=self.state["checks"] + 1, motivo=reason, modo=mode, fuente=source,
                termino=term, http=status or "", ok=0 if err else 1,
                ms=round((time.perf_counter() - t0) * 1000), kb=round(size / 1024, 1),
                productos=len(found), coincidencias=len(hits),
                con_stock=int(any(p["available"] for p in hits)),
                unidades=sum(p["qty"] for p in hits), precio=min(prices) if prices else "",
                error=err[:200])

    def matches(self, name: str) -> bool:
        n = norm(name)
        req = [norm(w) for w in self.config["required_words"]]
        exc = [norm(w) for w in self.config["exclude_words"]]
        return all(w in n for w in req) and not any(w and w in n for w in exc)

    async def check_once(self, reason: str):
        async with self.busy:
            self.broadcast_status()
            mode = self.mode()
            plans = self.next_plans(reason)
            found: dict[str, dict] = {}
            errors = []
            for i, plan in enumerate(plans):
                if i:
                    await asyncio.sleep(random.uniform(1.5, 4.0))
                try:
                    items = await self.fetch_plan(plan, reason, mode)
                except Exception as e:
                    errors.append(f"{plan[0]}: {e}")
                    self.log("warn", f"Falló la consulta {plan[0]}: {e}")
                    continue
                for p in items:
                    if p["id"] in found:  # combina las dos fuentes
                        old = found[p["id"]]
                        old["available"] = old["available"] or p["available"]
                        old["qty"] = max(old["qty"], p["qty"])
                    else:
                        found[p["id"]] = p
            if plans and len(errors) >= len(plans):
                raise RuntimeError("la tienda no respondió → " + " | ".join(errors[:3]))

            matches = [p for p in found.values() if self.matches(p["name"])]
            self.state["checks"] += 1
            self.last_check_at = now()
            for p in matches:
                await self.handle_match(p)
            save_json(STATE_FILE, self.state)

            n = self.state["checks"]
            via = plans[0][0] if len(plans) == 1 else f"{len(plans)} consultas"
            if matches:
                desc = ", ".join(f"{m['name']} [{'CON STOCK' if m['available'] else 'sin stock'}]" for m in matches)
                self.log("check", f"Revisión #{n} ({reason} · {via}): {len(matches)} coincidencia(s) → {desc}")
            else:
                self.log("check", f"Revisión #{n} ({reason} · {via}): aún no hay calendario · {len(found)} productos vistos")
            self.last_result = {"matches": len(matches), "analyzed": len(found), "errors": len(errors)}

    async def handle_match(self, p: dict):
        prev = self.state["products"].get(p["id"])
        rec = {
            **p,
            "first_seen": prev["first_seen"] if prev else fmt(now()),
            "first_seen_ts": prev["first_seen_ts"] if prev else time.time(),
            "last_seen": fmt(now()),
            "notified_available": prev.get("notified_available", False) if prev else False,
            "notified_published": prev.get("notified_published", False) if prev else False,
        }
        if p["available"]:
            if not rec["notified_available"]:
                self.log("alert", f"🎄 ¡DISPONIBLE! {p['name']} · {money(p['price'])} → {p['link']}")
                rec["notified_available"] = await self.notify_available(p)
        else:
            if rec["notified_available"]:
                self.log("warn", f"Se agotó: {p['name']}")
                rec["notified_available"] = False
                await self.telegram(f"⚠️ <b>Se agotó</b> {html.escape(p['name'])}. Sigo vigilando por si reponen.")
            if not rec["notified_published"] and self.config["notify_published_without_stock"]:
                self.log("alert", f"👀 Publicado SIN stock todavía: {p['name']} → {p['link']}")
                rec["notified_published"] = await self.telegram(
                    f"👀 <b>Ya aparece publicado (aún sin stock)</b>\n{html.escape(p['name'])}\n{html.escape(p['link'])}\n\n"
                    "Activé el modo turbo y te aviso apenas tenga unidades.",
                    ("Ver producto", p["link"]),
                )
        self.state["products"][p["id"]] = rec

    def turbo_active(self) -> bool:
        return self.config["turbo_enabled"] and bool(self.pending_ids())

    def in_window(self) -> bool:
        cfg = self.config
        today = now().strftime("%Y-%m-%d")
        return cfg["window_enabled"] and cfg["window_start"] <= today <= cfg["window_end"]

    def mode(self) -> str:
        if self.turbo_active():
            return "turbo"
        return "ventana" if self.in_window() else "normal"

    # ---------------------------------------------------------------- Telegram
    async def telegram(self, text: str, button: tuple[str, str] | list[tuple[str, str]] | None = None) -> bool:
        if not (TG_TOKEN and TG_CHAT):
            self.log("error", "Telegram no está configurado (faltan TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
            return False
        payload = {"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        buttons = [button] if isinstance(button, tuple) else (button or [])
        if buttons:  # un botón por fila: más fácil de tocar en el celular
            payload["reply_markup"] = {"inline_keyboard": [[{"text": t, "url": u}] for t, u in buttons]}
        err = None
        for attempt in range(1, 4):
            try:
                r = await self.client.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                                           json=payload, timeout=15)
                data = r.json()
                if data.get("ok"):
                    self.log("debug", "Mensaje de Telegram entregado")
                    return True
                err = data.get("description", f"HTTP {r.status_code}")
                if r.status_code == 429:
                    await asyncio.sleep(data.get("parameters", {}).get("retry_after", 3))
                    continue
                if r.status_code in (400, 401, 403, 404):
                    self.log("error", f"Telegram rechazó el mensaje: {err}")
                    return False
            except Exception as e:
                err = e
            self.log("warn", f"Telegram intento {attempt}/3 falló: {err}")
            await asyncio.sleep(2 * attempt)
        self.log("error", f"No se pudo enviar a Telegram: {err}")
        return False

    def buy_buttons(self, p: dict) -> list[tuple[str, str]]:
        cart = cart_link(p, self.config["cart_qty"], self.config["sales_channel"])
        if cart:
            return [("🛒 Agregar al carrito y pagar", cart), ("Ver producto", p["link"])]
        return [("🛒 Comprar ahora", p["link"])]

    async def notify_available(self, p: dict, simulated: bool = False) -> bool:
        head = "🧪 <b>SIMULACIÓN</b> — " if simulated else ""
        qty = f" · 📦 {p['qty']} und." if p.get("qty") else ""
        cart = cart_link(p, self.config["cart_qty"], self.config["sales_channel"])
        tip = ("Toca <b>Agregar al carrito y pagar</b>: abre la tienda con el calendario ya en tu carrito."
               if cart else f"👉 {html.escape(p['link'])}")
        text = (f"{head}🎄 <b>¡CALENDARIO DE ADVIENTO DISPONIBLE!</b>\n\n"
                f"<b>{html.escape(p['name'])}</b>\n💰 {money(p.get('price'))}{qty}\n\n"
                f"{tip}\n⏱ Detectado {fmt(now())}")
        buttons = self.buy_buttons(p)
        ok = await self.telegram(text, buttons)
        if ok and self.config["alert_repeats"] > 1:
            self.spawn(self.repeat_alert(p, self.config["alert_repeats"] - 1, head, buttons))
        return ok

    async def repeat_alert(self, p: dict, times: int, head: str, buttons: list[tuple[str, str]]):
        """Repite el aviso para que el celular vuelva a sonar por si no viste el primero."""
        for i in range(times):
            await asyncio.sleep(20)
            await self.telegram(f"{head}⏰ <b>Recordatorio {i + 2}:</b> el calendario está disponible, ¡compra ya!",
                                buttons)

    async def maybe_heartbeat(self):
        hours = self.config["heartbeat_hours"]
        if not hours or time.time() - self.state["last_heartbeat_ts"] < hours * 3600:
            return
        self.state["last_heartbeat_ts"] = time.time()
        save_json(STATE_FILE, self.state)
        await self.telegram(f"💓 Monitor activo · {self.state['checks']} revisiones · "
                            f"última {fmt(self.last_check_at)} · sin novedades del calendario.")

    # ---------------------------------------------------------------- bucle 24/7
    def compute_wait(self) -> float:
        cfg = self.config
        mode = self.mode()
        if mode == "turbo":
            base = cfg["turbo_interval_seconds"] * random.uniform(0.8, 1.2)
            jitter = random.uniform(0, 8)
        elif mode == "ventana":
            base = random.uniform(cfg["window_interval_min_seconds"], cfg["window_interval_max_seconds"])
            jitter = random.uniform(0, cfg["window_jitter_max_seconds"])
        else:
            base = random.uniform(cfg["interval_min"], cfg["interval_max"]) * 60
            jitter = random.uniform(0, cfg["jitter_max_seconds"])
        wait = base + jitter
        if self.consecutive_errors:  # se aleja un poco si la tienda falla
            cap = 10 * 60 if mode == "normal" else 4 * 60
            wait = min(wait * (1 + 0.25 * self.consecutive_errors), cap)
            mode += f", espera extra por {self.consecutive_errors} error(es)"
        self.next_detail = f"{base:.0f} s base + {jitter:.0f} s aleatorios (modo {mode})"
        return wait

    async def wait_wake(self, timeout: float | None) -> str:
        try:
            await asyncio.wait_for(self.wake.wait(), timeout)
        except asyncio.TimeoutError:
            return "timeout"
        self.wake.clear()
        reason, self.wake_reason = self.wake_reason or "reschedule", None
        return reason

    def poke(self, reason: str):
        self.wake_reason = reason
        self.wake.set()

    async def safe_check(self, reason: str):
        threshold = self.config["error_alert_threshold"]
        try:
            await self.check_once(reason)
            if self.consecutive_errors >= threshold:
                await self.telegram("✅ El monitor se recuperó y volvió a consultar la tienda con normalidad.")
            self.consecutive_errors = 0
        except Exception as e:
            self.consecutive_errors += 1
            self.log("error", f"Revisión fallida ({self.consecutive_errors} seguidas): {e}")
            if self.consecutive_errors == threshold:
                await self.telegram(f"⚠️ El monitor lleva {threshold} errores seguidos consultando Trendy: "
                                    f"{html.escape(str(e))[:300]}\nSigo intentando automáticamente.")
        self.broadcast_status()

    async def loop(self):
        self.log("info", f"Monitor iniciado · intervalo {self.config['interval_min']}–{self.config['interval_max']} min "
                         f"+ hasta {self.config['jitter_max_seconds']} s aleatorios")
        reason: str | None = "inicio"
        while True:
            if reason and (not self.config["paused"] or reason == "manual"):
                await self.safe_check(reason)
                await self.maybe_heartbeat()
            if self.config["paused"]:
                self.next_check_at = None
                self.next_detail = "en pausa"
                self.broadcast_status()
                woke = await self.wait_wake(None)
            else:
                wait = self.compute_wait()
                self.next_check_at = now() + timedelta(seconds=wait)
                self.log("debug", f"Próxima revisión {self.next_check_at:%H:%M:%S} · {self.next_detail}")
                self.broadcast_status()
                woke = await self.wait_wake(wait)
            reason = {"timeout": "programada", "reschedule": None}.get(woke, woke)

    async def run_forever(self):
        while True:  # supervisor: si algo inesperado rompe el bucle, se reinicia solo
            try:
                await self.loop()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.log("error", f"El bucle se detuvo inesperadamente ({e!r}); reiniciando en 10 s")
                await asyncio.sleep(10)


mon = Monitor()


@asynccontextmanager
async def lifespan(app: FastAPI):
    mon.client = httpx.AsyncClient(
        timeout=httpx.Timeout(20, connect=10), follow_redirects=True,
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=4))
    if not PANEL_PASSWORD:
        mon.log("warn", "PANEL_PASSWORD no está definido: el panel está abierto. Defínelo antes de publicarlo.")
    mon.spawn(mon.run_forever())
    mon.spawn(mon.qlog.sync_forever(mon.client, mon.log))
    yield
    for t in list(mon.bg_tasks):
        t.cancel()
    try:  # al redesplegar, sube a Sheets lo pendiente antes de apagar
        await asyncio.wait_for(mon.qlog.flush(mon.client, mon.log), 15)
    except Exception:
        pass
    await mon.client.aclose()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)
security = HTTPBasic(auto_error=False)


def auth(creds: HTTPBasicCredentials | None = Depends(security)):
    if not PANEL_PASSWORD:
        return
    # compare_digest con str falla si hay tildes o ñ: se compara en bytes
    ok = creds is not None and \
        secrets.compare_digest(creds.username.encode(), PANEL_USER.encode()) & \
        secrets.compare_digest(creds.password.encode(), PANEL_PASSWORD.encode())
    if not ok:
        raise HTTPException(401, "Credenciales inválidas", headers={"WWW-Authenticate": "Basic"})


# ---------------------------------------------------------------- rutas
@app.get("/health")
async def health():
    """503 si el bucle dejó de revisar (sirve para monitores externos como UptimeRobot)."""
    cfg = mon.config
    limit = max(cfg["interval_max"] * 60 + cfg["jitter_max_seconds"], 600) * 2 + 300
    ref = mon.last_check_at or mon.started_at
    stale = not cfg["paused"] and (now() - ref).total_seconds() > limit
    body = {"ok": not stale, "checks": mon.state["checks"], "last_check": fmt(mon.last_check_at),
            "mode": mon.mode(), "paused": cfg["paused"]}
    return JSONResponse(body, status_code=503 if stale else 200)


@app.get("/", dependencies=[Depends(auth)])
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status", dependencies=[Depends(auth)])
async def api_status():
    return mon.status()


@app.get("/api/config", dependencies=[Depends(auth)])
async def get_config():
    return mon.config


@app.post("/api/config", dependencies=[Depends(auth)])
async def set_config(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "Se esperaba un objeto JSON")
    body.pop("paused", None)  # la pausa se cambia con su propio botón
    mon.config = sanitize_config(body, mon.config)
    mon.rotation = []
    save_json(CONFIG_FILE, mon.config)
    c = mon.config
    mon.log("info", f"Configuración guardada · {c['interval_min']}–{c['interval_max']} min + 0–{c['jitter_max_seconds']} s · "
                    f"búsquedas: {', '.join(c['search_terms'])}")
    mon.poke("reschedule")
    return c


@app.post("/api/pause", dependencies=[Depends(auth)])
async def toggle_pause():
    mon.config["paused"] = not mon.config["paused"]
    save_json(CONFIG_FILE, mon.config)
    mon.log("info", "Monitor en PAUSA" if mon.config["paused"] else "Monitor reanudado")
    mon.poke("reschedule" if mon.config["paused"] else "reanudación")
    return {"paused": mon.config["paused"]}


@app.post("/api/check-now", dependencies=[Depends(auth)])
async def check_now():
    mon.log("info", "Revisión manual solicitada desde el panel")
    mon.poke("manual")
    return {"ok": True}


@app.post("/api/test-telegram", dependencies=[Depends(auth)])
async def test_telegram():
    mon.log("test", "Enviando mensaje de prueba a Telegram…")
    nxt = f"{mon.next_check_at:%H:%M:%S}" if mon.next_check_at else "—"
    ok = await mon.telegram(f"✅ <b>Prueba de Telegram</b>\nEl monitor del calendario Trendy está funcionando.\n"
                            f"Revisiones hechas: {mon.state['checks']} · próxima: {nxt}")
    mon.log("test" if ok else "error", "Prueba de Telegram entregada ✔" if ok else "La prueba de Telegram falló")
    return {"ok": ok}


async def real_sample_product() -> dict | None:
    """Un producto real con stock de la tienda, para que la simulación pruebe el enlace al carrito de verdad."""
    try:
        r = await mon.get_json(f"{STORE}/api/catalog_system/pub/products/search", {"_from": 0, "_to": 19})
        if r.status_code in (200, 206):
            for p in map(parse_product, r.json()):
                if p["name"] and p["available"] and p["sku"]:
                    return p
    except Exception as e:
        mon.log("warn", f"No se pudo traer un producto real para la simulación: {e}")
    return None


@app.post("/api/simulate", dependencies=[Depends(auth)])
async def simulate():
    mon.log("test", "🧪 Simulando detección del calendario…")
    sample = await real_sample_product()
    if sample:
        fake = {**sample, "id": "simulacion", "name": f"{sample['name']} (producto de prueba en lugar del calendario)"}
        mon.log("test", f"Producto real usado para probar el carrito: {sample['name']} (SKU {sample['sku']})")
    else:
        fake = {"id": "simulacion", "name": "Calendario de Adviento Trendy 2026 (simulado)",
                "link": STORE, "available": True, "qty": 120, "price": 189900, "sku": None}
        mon.log("warn", "Simulación sin producto real: la alerta llevará solo el enlace a la tienda")
    mon.log("alert", f"🧪 SIMULACIÓN · ¡DISPONIBLE! {fake['name']} · {money(fake.get('price'))} → "
                     f"{cart_link(fake, mon.config['cart_qty'], mon.config['sales_channel']) or fake['link']}")
    ok = await mon.notify_available(fake, simulated=True)
    mon.log("test" if ok else "error", "Alerta simulada entregada en Telegram ✔" if ok else "La alerta simulada no llegó a Telegram")
    return {"ok": ok}


@app.get("/api/logs", dependencies=[Depends(auth)])
async def get_logs(limit: int = 500):
    return list(mon.logs)[-limit:]


@app.post("/api/logs/clear", dependencies=[Depends(auth)])
async def clear_logs():
    mon.logs.clear()
    LOG_FILE.write_text("", "utf-8")
    mon.log("info", "Registro borrado desde el panel")
    return {"ok": True}


@app.get("/api/logs/download", dependencies=[Depends(auth)])
async def download_logs():
    return FileResponse(LOG_FILE, filename="registro-monitor-trendy.jsonl")


@app.get("/api/queries", dependencies=[Depends(auth)])
async def queries(limit: int = 50):
    return {"headers": KEYS, "rows": mon.qlog.tail(limit), "total": mon.qlog.total}


# Generar el Excel con miles de filas toma segundos: se hace en otro hilo para no frenar el monitor.
@app.get("/api/queries/download.xlsx", dependencies=[Depends(auth)])
async def queries_xlsx():
    stamp = now().strftime("%Y%m%d-%H%M")
    return Response(await asyncio.to_thread(mon.qlog.to_xlsx),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="consultas-trendy-{stamp}.xlsx"'})


@app.get("/api/queries/download.csv", dependencies=[Depends(auth)])
async def queries_csv():
    stamp = now().strftime("%Y%m%d-%H%M")
    return Response(await asyncio.to_thread(mon.qlog.to_csv), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="consultas-trendy-{stamp}.csv"'})


@app.post("/api/queries/sync", dependencies=[Depends(auth)])
async def queries_sync():
    if not mon.qlog.webhook:
        return {"ok": False, "error": "Google Sheets no está configurado (falta SHEETS_WEBHOOK_URL)"}
    n = len(mon.qlog.pending)
    ok = await mon.qlog.flush(mon.client, mon.log)
    mon.log("test" if ok else "error",
            f"Sincronización manual con Google Sheets: {n} fila(s) enviadas ✔" if ok
            else f"Sincronización con Google Sheets falló: {mon.qlog.sheets_last_error}")
    mon.broadcast_status()
    return {"ok": ok, "sent": n if ok else 0, "error": mon.qlog.sheets_last_error}


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/api/stream", dependencies=[Depends(auth)])
async def stream(request: Request):
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    mon.subscribers.add(q)

    async def gen():
        try:
            yield sse("status", mon.status())
            while not await request.is_disconnected():
                try:
                    event, data = await asyncio.wait_for(q.get(), 15)
                    yield sse(event, data)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            mon.subscribers.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    mon.log("error", f"Error en {request.url.path}: {exc!r}")
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
