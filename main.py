"""
AdGenius — SaaS Freemium: dashboard de inteligencia competitiva + carrusel IA
================================================================================

Instalación:
    pip install fastapi "uvicorn[standard]" httpx beautifulsoup4 jinja2 \
                google-generativeai python-multipart authlib itsdangerous pyjwt

Variables de entorno OBLIGATORIAS (Render -> Environment):
    GEMINI_API_KEYS      -> una o varias claves separadas por coma (rotación automática)
    GOOGLE_CLIENT_ID      -> credencial OAuth de Google Cloud Console
    GOOGLE_CLIENT_SECRET
    JWT_SECRET             -> string aleatorio largo, firma la cookie de sesión
    SESSION_SECRET         -> string aleatorio largo, usado por Authlib para el state OAuth
    BASE_URL               -> https://tu-app.onrender.com (sin slash final)
    ADMIN_EMAILS            -> correos gmail separados por coma con rol admin

Variables opcionales:
    GEMINI_MODEL (default "gemini-2.5-flash")
    DB_PATH (default "adgenius.db")
    TEST_MODE (default "true") -> muestra QR/datos bancarios de prueba en el modal de recarga

En Google Cloud Console, el Redirect URI autorizado debe ser exactamente:
    {BASE_URL}/auth/callback

Render (Start Command):
    uvicorn main:app --host 0.0.0.0 --port $PORT
"""

import asyncio
import json
import logging
import os
import random
import time
import urllib.parse
from typing import Optional
from urllib.parse import urlparse

import google.generativeai as genai
import httpx
import jwt
from authlib.integrations.starlette_client import OAuth
from bs4 import BeautifulSoup
from fastapi import FastAPI, Form, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from pydantic import BaseModel, Field

from database import (
    init_db, upsert_user, get_user, consumir_credito,
    crear_recarga, listar_recargas_pendientes, resolver_recarga,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("adgenius")

# --------------------------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------------------------

GEMINI_API_KEYS = [k.strip() for k in os.getenv("GEMINI_API_KEYS", os.getenv("GEMINI_API_KEY", "")).split(",") if k.strip()]
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
SCRAPE_TIMEOUT = 4.0
POLLINATIONS_BASE = "https://image.pollinations.ai/prompt"

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
JWT_SECRET = os.getenv("JWT_SECRET", "cambia-esto-en-produccion")
SESSION_SECRET = os.getenv("SESSION_SECRET", "cambia-esto-tambien")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
TEST_MODE = os.getenv("TEST_MODE", "true").lower() == "true"
PRECIO_PEN = "S/ 10.00"
PRECIO_USD = "$2.99 USD"

app = FastAPI(title="AdGenius API")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)
templates = Jinja2Templates(directory="templates")

oauth = OAuth()
oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
SOCIAL_DOMAINS = ("instagram.com", "tiktok.com", "facebook.com", "twitter.com", "x.com")


@app.on_event("startup")
async def _startup():
    init_db()
    os.makedirs("uploads/comprobantes", exist_ok=True)
    if not GEMINI_API_KEYS:
        log.warning("GEMINI_API_KEYS no configurada: /api/analyze fallará hasta que la definas.")
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        log.warning("GOOGLE_CLIENT_ID/SECRET no configurados: el login con Google no funcionará.")


# --------------------------------------------------------------------------------------
# Autenticación — Google OAuth + JWT en cookie httpOnly
# --------------------------------------------------------------------------------------


def get_current_user(request: Request):
    token = request.cookies.get("session_token")
    if not token:
        return None
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return get_user(payload["uid"])


@app.get("/auth/login")
async def auth_login(request: Request):
    redirect_uri = f"{BASE_URL}/auth/callback"
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/auth/callback")
async def auth_callback(request: Request):
    token = await oauth.google.authorize_access_token(request)
    userinfo = token.get("userinfo") or await oauth.google.userinfo(token=token)
    email = (userinfo.get("email") or "").lower()
    if not email.endswith("@gmail.com"):
        return HTMLResponse(
            "<h3>Solo se permiten cuentas @gmail.com. Cierra esta pestaña e inicia sesión con una cuenta Gmail.</h3>",
            status_code=403,
        )
    user = upsert_user(
        user_id=userinfo["sub"], email=email,
        name=userinfo.get("name", ""), picture=userinfo.get("picture", ""),
    )
    jwt_token = jwt.encode({"uid": user["id"], "exp": int(time.time()) + 60 * 60 * 24 * 7}, JWT_SECRET, algorithm="HS256")
    resp = RedirectResponse(url="/")
    resp.set_cookie("session_token", jwt_token, httponly=True, secure=True, samesite="lax", max_age=60 * 60 * 24 * 7)
    return resp


@app.get("/auth/logout")
async def auth_logout():
    resp = RedirectResponse(url="/")
    resp.delete_cookie("session_token")
    return resp


@app.get("/api/me")
async def api_me(request: Request):
    user = get_current_user(request)
    if not user:
        return {"autenticado": False}
    return {
        "autenticado": True, "email": user["email"], "name": user["name"],
        "picture": user["picture"], "role": user["role"], "credits": user["credits"],
        "precio_pen": PRECIO_PEN, "precio_usd": PRECIO_USD,
    }


# --------------------------------------------------------------------------------------
# Módulo Scraper — robusto, con timeout estricto y fallback suave por marca
# --------------------------------------------------------------------------------------


def extract_brand_from_url(raw_url: str) -> str:
    try:
        url = raw_url if raw_url.startswith("http") else f"https://{raw_url}"
        parsed = urlparse(url)
        netloc = parsed.netloc.replace("www.", "")
        if any(d in netloc for d in SOCIAL_DOMAINS):
            partes = [p for p in parsed.path.split("/") if p]
            if partes:
                return partes[0].lstrip("@").replace("-", " ").replace("_", " ").title()
        dominio = netloc.split(".")[0]
        return dominio.replace("-", " ").replace("_", " ").title() or raw_url
    except Exception:
        return raw_url


def scrape_url(raw_url: str) -> dict:
    url = raw_url.strip()
    if not url.lower().startswith("http"):
        return {"url": url, "title": url, "content": f"Marca de referencia: {url}", "scraped": False}
    try:
        timeout = httpx.Timeout(SCRAPE_TIMEOUT, connect=SCRAPE_TIMEOUT)
        with httpx.Client(headers=HEADERS, timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        def meta(*names):
            for n in names:
                tag = soup.find("meta", property=n) or soup.find("meta", attrs={"name": n})
                if tag and tag.get("content"):
                    return tag["content"].strip()
            return ""

        title = meta("og:title", "twitter:title") or (soup.title.string.strip() if soup.title and soup.title.string else "")
        description = meta("og:description", "twitter:description", "description")
        body_text = " ".join(t.get_text(" ", strip=True) for t in soup.find_all(["h1", "h2", "h3", "p", "li"])[:60])
        if not title and not description and not body_text:
            raise ValueError("Respuesta sin contenido útil (posible bloqueo silencioso)")
        marca = title or extract_brand_from_url(url)
        content = f"Título: {marca}\nDescripción: {description}\nContenido: {body_text}".strip()[:6000]
        return {"url": url, "title": marca, "content": content, "scraped": True}
    except Exception as exc:  # noqa: BLE001
        marca = extract_brand_from_url(url)
        log.warning("Scraping degradado para %s (%s) -> usando marca '%s'", url, exc, marca)
        return {
            "url": url, "title": marca,
            "content": f"No se pudo acceder al contenido de {url} (bloqueo o timeout). "
                       f"Trabaja con '{marca}' como marca de referencia usando conocimiento general del sector.",
            "scraped": False,
        }


# --------------------------------------------------------------------------------------
# Módulo de IA (Gemini) — inteligencia competitiva + AdGenius/Google Ads + carrusel
# --------------------------------------------------------------------------------------

_METRICA = {"type": "object", "properties": {"tu_negocio": {"type": "integer"}, "competencia": {"type": "integer"}}, "required": ["tu_negocio", "competencia"]}
_DIA_PLAN = {"type": "object", "properties": {"idea": {"type": "string"}, "objetivo": {"type": "string"}}, "required": ["idea", "objetivo"]}
_COPY_AIDA = {
    "type": "object",
    "properties": {"atencion": {"type": "string"}, "interes": {"type": "string"}, "deseo": {"type": "string"}, "accion": {"type": "string"}},
    "required": ["atencion", "interes", "deseo", "accion"],
}
_METRICAS_ADS = {
    "type": "object",
    "properties": {"ctr_estimado": {"type": "string"}, "cpc_estimado": {"type": "string"}, "roi_estimado": {"type": "string"}},
    "required": ["ctr_estimado", "cpc_estimado", "roi_estimado"],
}

CAMPAIGN_SCHEMA = {
    "type": "object",
    "properties": {
        "nombre_campana": {"type": "string"},
        "diagnostico_general": {"type": "string", "description": "1 párrafo breve, resumen ejecutivo del diagnóstico"},
        "score_competencia": {"type": "integer"},
        "metricas_comparativas": {
            "type": "object",
            "properties": {"engagement": _METRICA, "calidad_contenido": _METRICA, "frecuencia": _METRICA},
            "required": ["engagement", "calidad_contenido", "frecuencia"],
        },
        "matriz_swot": {
            "type": "object",
            "properties": {"fortalezas_rival": {"type": "array", "items": {"type": "string"}}, "puntos_debiles_rival": {"type": "array", "items": {"type": "string"}}},
            "required": ["fortalezas_rival", "puntos_debiles_rival"],
        },
        "plan_semanal": {
            "type": "object",
            "properties": {"lunes": _DIA_PLAN, "miercoles": _DIA_PLAN, "viernes": _DIA_PLAN},
            "required": ["lunes", "miercoles", "viernes"],
        },
        "recomendaciones_clave": {"type": "array", "items": {"type": "string"}},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "ad_rank_score": {"type": "integer", "description": "1 a 100, calidad/relevancia estimada del anuncio estilo Google Ads"},
        "palabras_clave_positivas": {"type": "array", "items": {"type": "string"}},
        "palabras_clave_negativas": {"type": "array", "items": {"type": "string"}},
        "copy_aida": _COPY_AIDA,
        "titulares": {"type": "array", "items": {"type": "string"}},
        "metricas_ads": _METRICAS_ADS,
        "carrusel_placas": {
            "type": "array",
            "minItems": 5,
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "tipo": {"type": "string", "enum": ["Gancho", "Problema", "Solucion", "Beneficios", "CTA"]},
                    "titulo": {"type": "string"},
                    "descripcion": {"type": "string"},
                    "image_prompt": {"type": "string", "description": "Prompt en inglés, conciso (15-20 palabras), fondo fotográfico neutro/estudio"},
                },
                "required": ["tipo", "titulo", "descripcion", "image_prompt"],
            },
        },
    },
    "required": [
        "nombre_campana", "diagnostico_general", "score_competencia", "metricas_comparativas",
        "matriz_swot", "plan_semanal", "recomendaciones_clave", "hashtags",
        "ad_rank_score", "palabras_clave_positivas", "palabras_clave_negativas",
        "copy_aida", "titulares", "metricas_ads", "carrusel_placas",
    ],
}

_key_index = 0


def generate_campaign(business: dict, competitor: dict, angulo: Optional[str] = None) -> dict:
    global _key_index
    if not GEMINI_API_KEYS:
        raise RuntimeError("Falta configurar GEMINI_API_KEYS (o GEMINI_API_KEY) en las variables de entorno.")

    linea_angulo = (
        f'Ángulo/enfoque solicitado para esta versión de la campaña: "{angulo}". Adapta tono, ganchos y CTA a ese enfoque.'
        if angulo else "Usa un ángulo equilibrado, profesional y persuasivo por defecto."
    )
    prompt = f"""Eres un estratega senior de marketing digital, especialista certificado en Google Ads, analista
de inteligencia competitiva y director creativo publicitario de agencia premium.

NEGOCIO PROPIO ({business['url']}):
{business['content']}

COMPETIDOR A ANALIZAR ({competitor['url']}):
{competitor['content']}

{linea_angulo}

Genera un análisis y UN ÚNICO carrusel narrativo completos en JSON con:
- nombre_campana: nombre corto y memorable de la campaña.
- diagnostico_general: 1 párrafo breve (2-3 frases) con el diagnóstico ejecutivo de la situación competitiva.
- score_competencia: entero 0-100, qué tan bien posicionado está el negocio propio frente al rival.
- metricas_comparativas: engagement, calidad_contenido y frecuencia, puntaje 0-100 para "tu_negocio" y "competencia".
- matriz_swot: fortalezas_rival (3-4) y puntos_debiles_rival (3-4 oportunidades a explotar).
- plan_semanal: idea táctica + objetivo para lunes, miércoles y viernes.
- recomendaciones_clave: exactamente 3 acciones inmediatas accionables.
- hashtags: 6 a 8 hashtags en español, con #.
- ad_rank_score: entero 1-100, calidad/relevancia estimada del anuncio estilo "Ad Rank" de Google Ads.
- palabras_clave_positivas: 6 a 10 palabras clave de alta conversión para pautar en Google/Meta Ads.
- palabras_clave_negativas: 4 a 6 palabras clave negativas (tráfico irrelevante a excluir).
- copy_aida: copy estructurado en fórmula AIDA (atencion, interes, deseo, accion), cada campo 1-2 frases en español.
- titulares: exactamente 3 variaciones de titulares (headlines) de alto impacto, en español, máx 8 palabras c/u.
- metricas_ads: ctr_estimado, cpc_estimado y roi_estimado como texto (ej. "2.3% (referencial)", "S/ 1.20 - S/ 2.50"),
  adaptados al mercado/local del negocio según el contenido analizado. Deja claro que son ESTIMACIONES de IA,
  no datos reales de Google Ads.
- carrusel_placas: array de EXACTAMENTE 5 objetos, en este orden narrativo obligatorio:
    1) tipo="Gancho": título de alto impacto + subtítulo breve.
    2) tipo="Problema": riesgo/molestia/punto de dolor actual del cliente.
    3) tipo="Solucion": propuesta de valor del negocio propio como solución.
    4) tipo="Beneficios": 2-3 diferenciadores frente al rival.
    5) tipo="CTA": oferta, incentivo y paso a seguir.
  Cada placa: titulo (máx 8 palabras, español), descripcion (máx 2 frases, español, legible como overlay),
  image_prompt (EN INGLÉS, conciso 15-20 palabras, fondo fotográfico neutro/minimalista tipo estudio, sin texto/logos).
"""
    ultimo_error = None
    for intento in range(len(GEMINI_API_KEYS)):
        key = GEMINI_API_KEYS[(_key_index + intento) % len(GEMINI_API_KEYS)]
        try:
            genai.configure(api_key=key)
            model = genai.GenerativeModel(GEMINI_MODEL)
            response = model.generate_content(
                prompt,
                generation_config={"response_mime_type": "application/json", "response_schema": CAMPAIGN_SCHEMA, "temperature": 0.85},
            )
            _key_index = (_key_index + intento) % len(GEMINI_API_KEYS)
            return json.loads(response.text)
        except Exception as exc:  # noqa: BLE001 — probar la siguiente llave
            log.warning("Fallo con llave Gemini #%s: %s", intento, exc)
            ultimo_error = exc
    raise RuntimeError(f"Todas las llaves de Gemini fallaron. Último error: {ultimo_error}")


# --------------------------------------------------------------------------------------
# Módulo Visual — Pollinations.ai (Flux), gratuito y sin API key
# --------------------------------------------------------------------------------------


def build_pollinations_url(prompt: str, fallback_text: str = "AdGenius") -> str:
    texto = (prompt or fallback_text).strip()
    encoded = urllib.parse.quote(texto)
    seed = random.randint(1, 999_999)
    return f"{POLLINATIONS_BASE}/{encoded}?model=flux&width=1080&height=1350&nologo=true&seed={seed}"


# --------------------------------------------------------------------------------------
# Endpoints principales
# --------------------------------------------------------------------------------------


class AnalyzeRequest(BaseModel):
    business_url: str = Field(..., description="URL o handle del negocio propio")
    competitor_url: str = Field(..., description="URL o nombre del competidor")
    angulo: Optional[str] = Field(default=None)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={"test_mode": TEST_MODE})


@app.post("/api/analyze")
async def analyze(payload: AnalyzeRequest, request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "login_required", "mensaje": "Inicia sesión con Google para generar tu análisis."}, status_code=401)

    es_admin = user["role"] == "admin"
    desbloqueado = es_admin or user["credits"] > 0

    def sse(stage: str, status: str, mensaje: str, data: Optional[dict] = None) -> str:
        body = json.dumps({"stage": stage, "status": status, "mensaje": mensaje, "data": data}, ensure_ascii=False)
        return f"data: {body}\n\n"

    async def event_stream():
        try:
            yield sse("scraping", "start", "🔍 Extrayendo y analizando la marca/competencia...")
            business = await run_in_threadpool(scrape_url, payload.business_url)
            competitor = await run_in_threadpool(scrape_url, payload.competitor_url)
            yield sse("scraping", "done", "🔍 Marca y competencia identificadas.",
                       {"business_title": business["title"], "competitor_title": competitor["title"]})

            yield sse("estrategia", "start", "🧠 Generando análisis competitivo, métricas Ads y carrusel con IA...")
            campana = await run_in_threadpool(generate_campaign, business, competitor, payload.angulo)

            if not desbloqueado:
                preview = campana["carrusel_placas"][0]
                yield sse("estrategia", "done", "🔒 Vista previa gratuita generada.", {
                    "bloqueado": True,
                    "nombre_campana": campana["nombre_campana"],
                    "diagnostico_general": campana["diagnostico_general"],
                    "matriz_swot": campana["matriz_swot"],
                    "preview_copy": {"titulo": preview["titulo"], "descripcion": preview["descripcion"]},
                    "precio_pen": PRECIO_PEN, "precio_usd": PRECIO_USD,
                })
                yield sse("completo", "done", "🔒 Desbloquea el análisis completo y el carrusel HD para continuar.", {"bloqueado": True})
                return

            if not es_admin:
                consumir_credito(user["id"])

            yield sse("estrategia", "done", "🧠 Estrategia, métricas Ads y carrusel listos.", {
                "bloqueado": False,
                "nombre_campana": campana["nombre_campana"],
                "diagnostico_general": campana["diagnostico_general"],
                "score_competencia": campana["score_competencia"],
                "metricas_comparativas": campana["metricas_comparativas"],
                "matriz_swot": campana["matriz_swot"],
                "plan_semanal": campana["plan_semanal"],
                "recomendaciones_clave": campana["recomendaciones_clave"],
                "hashtags": campana["hashtags"],
                "ad_rank_score": campana["ad_rank_score"],
                "palabras_clave_positivas": campana["palabras_clave_positivas"],
                "palabras_clave_negativas": campana["palabras_clave_negativas"],
                "copy_aida": campana["copy_aida"],
                "titulares": campana["titulares"],
                "metricas_ads": campana["metricas_ads"],
            })

            total = len(campana["carrusel_placas"])
            for i, placa in enumerate(campana["carrusel_placas"]):
                yield sse("imagen", "start", f"🎨 Renderizando fondo fotorrealista con Pollinations.ai (Placa {i + 1}/{total})...",
                           {"index": i, "total": total, "tipo": placa["tipo"], "titulo": placa["titulo"], "descripcion": placa["descripcion"]})
                await asyncio.sleep(0.3)
                image_url = build_pollinations_url(placa["image_prompt"], placa["titulo"])
                yield sse("imagen", "done", f"🎨 Placa {i + 1}/{total} lista.", {"index": i, "image_url": image_url})

            creditos_restantes = get_user(user["id"])["credits"]
            yield sse("completo", "done", "✨ Carrusel listo para lanzar.",
                       {"nombre_campana": campana["nombre_campana"], "creditos_restantes": creditos_restantes})
        except Exception as exc:  # noqa: BLE001
            log.exception("Error en el pipeline de análisis")
            yield sse("error", "error", "⚠️ Ocurrió un error durante la generación.", {"mensaje": str(exc)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# --------------------------------------------------------------------------------------
# Recargas — subida de comprobante (modo prueba incluido)
# --------------------------------------------------------------------------------------


@app.post("/api/recargas")
async def crear_recarga_endpoint(
    request: Request,
    monto: str = Form(...),
    metodo: str = Form(...),
    creditos: int = Form(1),
    comprobante: UploadFile = File(...),
):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "login_required"}, status_code=401)
    os.makedirs("uploads/comprobantes", exist_ok=True)
    ext = os.path.splitext(comprobante.filename or "")[1] or ".jpg"
    nombre_archivo = f"{user['id']}_{int(time.time())}{ext}"
    ruta = f"uploads/comprobantes/{nombre_archivo}"
    with open(ruta, "wb") as f:
        f.write(await comprobante.read())
    recarga_id = crear_recarga(user["id"], monto, metodo, creditos, ruta)
    return {"ok": True, "recarga_id": recarga_id, "mensaje": "Comprobante recibido. Un administrador validará tu pago pronto."}


# --------------------------------------------------------------------------------------
# Panel administrador
# --------------------------------------------------------------------------------------


def _requiere_admin(request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return None
    return user


@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request):
    user = _requiere_admin(request)
    if not user:
        return HTMLResponse("<h3>403 — Acceso solo para administradores.</h3>", status_code=403)
    pendientes = listar_recargas_pendientes()
    return templates.TemplateResponse(request=request, name="admin.html", context={"pendientes": pendientes, "usuario": dict(user)})


@app.get("/admin/comprobante/{recarga_id}")
async def ver_comprobante(recarga_id: int, request: Request):
    user = _requiere_admin(request)
    if not user:
        return HTMLResponse("403", status_code=403)
    from database import obtener_recarga
    r = obtener_recarga(recarga_id)
    if not r or not os.path.exists(r["comprobante_path"]):
        return HTMLResponse("No encontrado", status_code=404)
    return FileResponse(r["comprobante_path"])


@app.post("/admin/recargas/{recarga_id}/resolver")
async def resolver_recarga_endpoint(recarga_id: int, request: Request, accion: str = Form(...)):
    user = _requiere_admin(request)
    if not user:
        return HTMLResponse("403", status_code=403)
    resolver_recarga(recarga_id, aprobar=(accion == "aprobar"))
    return RedirectResponse(url="/admin", status_code=303)


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "gemini_llaves_configuradas": len(GEMINI_API_KEYS),
        "gemini_modelo": GEMINI_MODEL,
        "google_oauth_configurado": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
        "test_mode": TEST_MODE,
        "motor_imagenes": "pollinations.ai (flux, gratuito, sin API key)",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=True)
