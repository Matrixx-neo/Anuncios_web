"""
AdGenius — SaaS de publicidad generada por IA (versión Enterprise Ready)
=========================================================================

Instalación:
    pip install fastapi "uvicorn[standard]" httpx beautifulsoup4 jinja2 \
                google-generativeai fal-client python-multipart

Variables de entorno (Render -> Environment):
    GEMINI_API_KEY   -> clave de Google AI Studio
    FAL_KEY          -> clave de Fal.ai (opcional: si falta, se usan placeholders)
    GEMINI_MODEL     -> por defecto "gemini-2.5-flash"
    FAL_FLUX_MODEL   -> por defecto "fal-ai/flux/schnell"

Ejecutar local:
    python app.py
Render (Start Command):
    uvicorn app:app --host 0.0.0.0 --port $PORT
"""

import json
import logging
import os
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

try:
    import fal_client
except ImportError:
    fal_client = None

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("adgenius")

# --------------------------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------------------------

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
FAL_KEY = os.getenv("FAL_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
FAL_FLUX_MODEL = os.getenv("FAL_FLUX_MODEL", "fal-ai/flux/schnell")
SCRAPE_TIMEOUT = 4.0  # segundos, estricto — nunca debe congelar la app

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
if FAL_KEY:
    os.environ.setdefault("FAL_KEY", FAL_KEY)

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
# Módulos de IA (Gemini) — análisis estratégico y copywriting de campaña
# --------------------------------------------------------------------------------------

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "puntos_fuertes": {"type": "array", "items": {"type": "string"}},
        "oportunidad_clave": {"type": "string"},
        "tono_voz_sugerido": {"type": "string"},
    },
    "required": ["puntos_fuertes", "oportunidad_clave", "tono_voz_sugerido"],
}

STRATEGY_SCHEMA = {
    "type": "object",
    "properties": {
        "nombre_campana": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "placas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tipo": {"type": "string", "enum": ["Gancho", "Beneficio", "CTA"]},
                    "titulo": {"type": "string"},
                    "copy": {"type": "string"},
                    "image_prompt": {
                        "type": "string",
                        "description": "Prompt en inglés, muy visual y detallado, para imagen fotorrealista de producto",
                    },
                },
                "required": ["tipo", "titulo", "copy", "image_prompt"],
            },
        },
    },
    "required": ["nombre_campana", "hashtags", "placas"],
}


def _gemini_json(prompt: str, schema: dict) -> dict:
    if not GEMINI_API_KEY:
        raise RuntimeError("Falta configurar la variable de entorno GEMINI_API_KEY")
    model = genai.GenerativeModel(GEMINI_MODEL)
    response = model.generate_content(
        prompt,
        generation_config={
            "response_mime_type": "application/json",
            "response_schema": schema,
            "temperature": 0.8,
        },
    )
    return json.loads(response.text)


def analyze_competitor(business: dict, competitor: dict) -> dict:
    prompt = f"""Eres un estratega senior de marketing digital y publicidad performance.

NEGOCIO PROPIO ({business['url']}):
{business['content']}

COMPETIDOR A ANALIZAR ({competitor['url']}):
{competitor['content']}

Responde en JSON con:
- puntos_fuertes: 3 a 5 puntos fuertes o ganchos comerciales que el competidor usa bien.
- oportunidad_clave: la oportunidad más contundente que el negocio propio debería explotar frente a este competidor.
- tono_voz_sugerido: el tono de voz que el NEGOCIO PROPIO debería usar en su campaña para diferenciarse (una frase).
"""
    return _gemini_json(prompt, ANALYSIS_SCHEMA)


def generate_strategy(business: dict, analysis: dict) -> dict:
    prompt = f"""Eres un director creativo publicitario experto en carruseles de alto rendimiento para Instagram/TikTok Ads.

NEGOCIO PROPIO ({business['url']}):
{business['content']}

ANÁLISIS ESTRATÉGICO:
{json.dumps(analysis, ensure_ascii=False)}

Diseña una campaña de carrusel de exactamente 3 placas explotando la "oportunidad_clave" y usando el
"tono_voz_sugerido". Responde en JSON con:
- nombre_campana: nombre corto y memorable de la campaña.
- hashtags: 6 a 8 hashtags relevantes en español, sin espacios, con #.
- placas: array de exactamente 3 objetos en este orden:
    1) tipo="Gancho": detiene el scroll, plantea el problema o deseo.
    2) tipo="Beneficio": comunica el valor diferencial concreto.
    3) tipo="CTA": llamado a la acción claro y urgente.
  Cada placa necesita:
    - titulo: headline corto para sobreponer en la imagen (máx 8 palabras, en español).
    - copy: copy de apoyo para el pie de foto (1-2 frases, en español).
    - image_prompt: prompt EN INGLÉS, extremadamente descriptivo, para imagen publicitaria fotorrealista
      3D/producto de altísima calidad (estilo comercial, iluminación de estudio, 8k, composición profesional).
      No incluyas texto ni logos en la descripción de la imagen.
"""
    return _gemini_json(prompt, STRATEGY_SCHEMA)


# --------------------------------------------------------------------------------------
# Módulo Visual — Flux.1 vía Fal.ai, con fallback a placeholder premium
# --------------------------------------------------------------------------------------


def placeholder_image(texto: str) -> str:
    txt = urllib.parse.quote((texto or "AdGenius")[:40])
    return f"https://placehold.co/1024x1024/1e1b2e/818cf8?text={txt}&font=raleway"


def generate_image(prompt: str, fallback_text: str) -> str:
    if not FAL_KEY or fal_client is None:
        return placeholder_image(fallback_text)

    full_prompt = (
        f"{prompt}, photorealistic advertising photography, commercial product render, "
        f"studio lighting, ultra sharp focus, 8k, high-end ad campaign, no text, no watermark, no logo"
    )
    try:
        result = fal_client.subscribe(
            FAL_FLUX_MODEL,
            arguments={
                "prompt": full_prompt,
                "image_size": "square_hd",
                "num_images": 1,
                "enable_safety_checker": True,
            },
        )
        images = result.get("images") or []
        if not images:
            raise RuntimeError("Fal.ai no devolvió imágenes")
        return images[0]["url"]
    except Exception as exc:  # noqa: BLE001 — jamás rompe la app: placeholder de respaldo
        log.warning("Fal.ai falló, usando placeholder: %s", exc)
        return placeholder_image(fallback_text)


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

            yield sse("analisis", "start", "🧠 Analizando ganchos comerciales y ángulo estratégico con IA...")
            analysis = await run_in_threadpool(analyze_competitor, business, competitor)
            yield sse("analisis", "done", "🧠 Análisis estratégico completo.", analysis)

            yield sse("estrategia", "start", "✍️ Redactando copies de alta conversión y estructurando carrusel...")
            strategy = await run_in_threadpool(generate_strategy, business, analysis)
            yield sse("estrategia", "done", "✍️ Estrategia de campaña lista.",
                       {"nombre_campana": strategy["nombre_campana"], "hashtags": strategy["hashtags"]})

            placas_finales = []
            total = len(strategy["placas"])
            for i, placa in enumerate(strategy["placas"]):
                yield sse("imagen", "start", f"🎨 Generando imágenes fotorrealistas de producto (Placa {i + 1}/{total})...",
                           {"index": i, "tipo": placa["tipo"], "titulo": placa["titulo"]})
                image_url = await run_in_threadpool(generate_image, placa["image_prompt"], placa["titulo"])
                placa_final = {**placa, "image_url": image_url}
                placas_finales.append(placa_final)
                yield sse("imagen", "done", f"🎨 Placa {i + 1}/{total} generada.", {"index": i, "image_url": image_url})

            yield sse("completo", "done", "✨ Campaña lista para lanzar.", {
                "nombre_campana": strategy["nombre_campana"],
                "hashtags": strategy["hashtags"],
                "placas": placas_finales,
                "analisis": analysis,
            })
        except Exception as exc:  # noqa: BLE001
            log.exception("Error en el pipeline de análisis")
            yield sse("error", "error", "⚠️ Ocurrió un error durante la generación.", {"mensaje": str(exc)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "gemini_configurado": bool(GEMINI_API_KEY),
        "fal_configurado": bool(FAL_KEY),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=True)
