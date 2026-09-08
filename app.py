"""
AdGenius — Dashboard SaaS de publicidad e inteligencia competitiva con IA
=========================================================================

Instalación:
    pip install fastapi "uvicorn[standard]" httpx beautifulsoup4 jinja2 google-generativeai python-multipart

Variables de entorno (Render -> Environment):
    GEMINI_API_KEY   -> clave de Google AI Studio
    GEMINI_MODEL     -> por defecto "gemini-2.5-flash"

Las imágenes se generan con Pollinations.ai (modelo Flux), sin API key ni costo.

Ejecutar local:
    python app.py
Render (Start Command):
    uvicorn app:app --host 0.0.0.0 --port $PORT
"""

import asyncio
import json
import logging
import os
import random
import urllib.parse
from typing import Optional
from urllib.parse import urlparse

import google.generativeai as genai
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("adgenius")

# --------------------------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------------------------

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
SCRAPE_TIMEOUT = 4.0  # segundos, estricto — nunca debe congelar la app
POLLINATIONS_BASE = "https://image.pollinations.ai/prompt"

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

app = FastAPI(title="AdGenius API")
templates = Jinja2Templates(directory="templates")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

SOCIAL_DOMAINS = ("instagram.com", "tiktok.com", "facebook.com", "twitter.com", "x.com")


class AnalyzeRequest(BaseModel):
    business_url: str = Field(..., description="URL o handle del negocio propio")
    competitor_url: str = Field(..., description="URL o nombre del competidor")
    angulo: Optional[str] = Field(default=None, description="Enfoque opcional: Educativo, Oferta Agresiva, Comparativa, Emocional, Urgencia...")


# --------------------------------------------------------------------------------------
# Módulo Scraper — robusto, con timeout estricto y fallback suave por marca
# --------------------------------------------------------------------------------------


def extract_brand_from_url(raw_url: str) -> str:
    """Deriva un nombre de marca legible directamente de la URL, sin red.
    Se usa como fallback cuando el scraping falla o la red social bloquea el acceso."""
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
    """Extrae título/descripción/texto visible de una URL con timeout estricto de 4s.
    Si la red social bloquea el acceso, hay error de red, o la respuesta no es HTML útil,
    se activa un fallback suave: se deriva el nombre de marca desde la propia URL y el
    flujo continúa sin interrupciones ni pantallas congeladas."""
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
    except Exception as exc:  # noqa: BLE001 — fallback suave, jamás rompe el flujo
        marca = extract_brand_from_url(url)
        log.warning("Scraping degradado para %s (%s) -> usando marca '%s'", url, exc, marca)
        return {
            "url": url,
            "title": marca,
            "content": f"No se pudo acceder al contenido de {url} (bloqueo o timeout). "
                       f"Trabaja con '{marca}' como marca de referencia usando conocimiento general del sector.",
            "scraped": False,
        }


# --------------------------------------------------------------------------------------
# Módulo de IA (Gemini) — inteligencia competitiva + carrusel narrativo en UNA sola llamada
# --------------------------------------------------------------------------------------

_METRICA = {
    "type": "object",
    "properties": {"tu_negocio": {"type": "integer"}, "competencia": {"type": "integer"}},
    "required": ["tu_negocio", "competencia"],
}

_DIA_PLAN = {
    "type": "object",
    "properties": {"idea": {"type": "string"}, "objetivo": {"type": "string"}},
    "required": ["idea", "objetivo"],
}

CAMPAIGN_SCHEMA = {
    "type": "object",
    "properties": {
        "nombre_campana": {"type": "string"},
        "score_competencia": {"type": "integer", "description": "0 a 100, dominancia de mercado del negocio propio frente al rival"},
        "metricas_comparativas": {
            "type": "object",
            "properties": {
                "engagement": _METRICA,
                "calidad_contenido": _METRICA,
                "frecuencia": _METRICA,
            },
            "required": ["engagement", "calidad_contenido", "frecuencia"],
        },
        "matriz_swot": {
            "type": "object",
            "properties": {
                "fortalezas_rival": {"type": "array", "items": {"type": "string"}},
                "puntos_debiles_rival": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["fortalezas_rival", "puntos_debiles_rival"],
        },
        "plan_semanal": {
            "type": "object",
            "properties": {"lunes": _DIA_PLAN, "miercoles": _DIA_PLAN, "viernes": _DIA_PLAN},
            "required": ["lunes", "miercoles", "viernes"],
        },
        "recomendaciones_clave": {"type": "array", "items": {"type": "string"}},
        "hashtags": {"type": "array", "items": {"type": "string"}},
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
                    "image_prompt": {
                        "type": "string",
                        "description": "Prompt en inglés, conciso (15-20 palabras), fondo fotográfico neutro/estudio",
                    },
                },
                "required": ["tipo", "titulo", "descripcion", "image_prompt"],
            },
        },
    },
    "required": [
        "nombre_campana", "score_competencia", "metricas_comparativas",
        "matriz_swot", "plan_semanal", "recomendaciones_clave", "hashtags", "carrusel_placas",
    ],
}


def generate_campaign(business: dict, competitor: dict, angulo: Optional[str] = None) -> dict:
    if not GEMINI_API_KEY:
        raise RuntimeError("Falta configurar la variable de entorno GEMINI_API_KEY")

    linea_angulo = (
        f'Ángulo/enfoque solicitado para esta versión de la campaña: "{angulo}". Adapta tono, ganchos y CTA a ese enfoque.'
        if angulo else
        "Usa un ángulo equilibrado, profesional y persuasivo por defecto."
    )

    prompt = f"""Eres un estratega senior de marketing digital, analista de inteligencia competitiva y director
creativo publicitario de agencia premium, experto en carruseles narrativos de alto rendimiento para
Instagram/TikTok Ads.

NEGOCIO PROPIO ({business['url']}):
{business['content']}

COMPETIDOR A ANALIZAR ({competitor['url']}):
{competitor['content']}

{linea_angulo}

Genera un análisis y UN ÚNICO carrusel narrativo completos en JSON con:
- nombre_campana: nombre corto y memorable de la campaña.
- score_competencia: entero 0-100 que representa qué tan bien posicionado está el negocio propio frente al rival
  (más alto = mejor posicionado). Sé realista y variado, no siempre uses 50.
- metricas_comparativas: para engagement, calidad_contenido y frecuencia, un puntaje 0-100 estimado para
  "tu_negocio" y para "competencia" en cada una, basado en el contenido analizado.
- matriz_swot: fortalezas_rival (3 a 4 puntos fuertes del competidor) y puntos_debiles_rival (3 a 4 debilidades
  u oportunidades que el negocio propio puede explotar).
- plan_semanal: una idea táctica concreta y su objetivo de negocio para lunes, miércoles y viernes.
- recomendaciones_clave: exactamente 3 acciones inmediatas y accionables para el negocio propio.
- hashtags: 6 a 8 hashtags relevantes en español, con #, para toda la campaña.
- carrusel_placas: array de EXACTAMENTE 5 objetos, en este orden narrativo obligatorio:
    1) tipo="Gancho": título de alto impacto que detiene el scroll + subtítulo breve.
    2) tipo="Problema": explica el riesgo, la molestia o el punto de dolor actual del cliente.
    3) tipo="Solucion": presenta la propuesta de valor del negocio propio como la solución.
    4) tipo="Beneficios": 2-3 puntos fuertes/diferenciadores frente al rival (puedes usar viñetas dentro de "descripcion").
    5) tipo="CTA": oferta concreta, incentivo y el paso a seguir (ej. "Escríbenos ahora").
  Cada placa necesita:
    - titulo: headline corto y contundente (máx 8 palabras, en español).
    - descripcion: texto de apoyo breve (máx 2 frases cortas, en español) legible como overlay sobre una foto.
    - image_prompt: prompt EN INGLÉS, CONCISO (15-20 palabras), enfocado SOLO en una fotografía de fondo
      neutra/minimalista tipo estudio, coherente con el momento narrativo de esa placa — ej. estilo
      "clean minimal aesthetic background, natural soft lighting, studio shot, high resolution".
      La composición debe quedar despejada para que un texto se pueda superponer con buena legibilidad.
      No incluyas texto, letras ni logos dentro de la imagen.
"""
    model = genai.GenerativeModel(GEMINI_MODEL)
    response = model.generate_content(
        prompt,
        generation_config={
            "response_mime_type": "application/json",
            "response_schema": CAMPAIGN_SCHEMA,
            "temperature": 0.85,
        },
    )
    return json.loads(response.text)


# --------------------------------------------------------------------------------------
# Módulo Visual — Pollinations.ai (Flux), gratuito y sin API key
# --------------------------------------------------------------------------------------


def build_pollinations_url(prompt: str, fallback_text: str = "AdGenius") -> str:
    texto = (prompt or fallback_text).strip()
    encoded = urllib.parse.quote(texto)  # sanitización obligatoria del prompt
    seed = random.randint(1, 999_999)
    return f"{POLLINATIONS_BASE}/{encoded}?model=flux&width=1080&height=1350&nologo=true&seed={seed}"


# --------------------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.post("/api/analyze")
async def analyze(payload: AnalyzeRequest):
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

            yield sse("estrategia", "start", "🧠 Generando análisis competitivo, métricas y carrusel narrativo con IA...")
            campana = await run_in_threadpool(generate_campaign, business, competitor, payload.angulo)
            yield sse("estrategia", "done", "🧠 Estrategia, métricas y carrusel listos.", {
                "nombre_campana": campana["nombre_campana"],
                "score_competencia": campana["score_competencia"],
                "metricas_comparativas": campana["metricas_comparativas"],
                "matriz_swot": campana["matriz_swot"],
                "plan_semanal": campana["plan_semanal"],
                "recomendaciones_clave": campana["recomendaciones_clave"],
                "hashtags": campana["hashtags"],
            })

            total = len(campana["carrusel_placas"])
            for i, placa in enumerate(campana["carrusel_placas"]):
                yield sse("imagen", "start", f"🎨 Renderizando fondo fotorrealista con Pollinations.ai (Placa {i + 1}/{total})...",
                           {"index": i, "total": total, "tipo": placa["tipo"], "titulo": placa["titulo"], "descripcion": placa["descripcion"]})
                await asyncio.sleep(0.3)  # pacing visual: construir la URL es instantáneo
                image_url = build_pollinations_url(placa["image_prompt"], placa["titulo"])
                yield sse("imagen", "done", f"🎨 Placa {i + 1}/{total} lista.", {"index": i, "image_url": image_url})

            yield sse("completo", "done", "✨ Carrusel listo para lanzar.", {"nombre_campana": campana["nombre_campana"]})
        except Exception as exc:  # noqa: BLE001
            log.exception("Error en el pipeline de análisis")
            yield sse("error", "error", "⚠️ Ocurrió un error durante la generación.", {"mensaje": str(exc)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "gemini_configurado": bool(GEMINI_API_KEY),
        "gemini_modelo": GEMINI_MODEL,
        "motor_imagenes": "pollinations.ai (flux, gratuito, sin API key)",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=True)
