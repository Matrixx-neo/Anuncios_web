"""
AdGenius — Generador de publicidad con IA a partir del análisis de la competencia
===================================================================================

Instalación:
    pip install fastapi "uvicorn[standard]" httpx beautifulsoup4 jinja2 \
                google-generativeai fal-client python-multipart

Variables de entorno requeridas (.env o export):
    GEMINI_API_KEY   -> clave de Google AI Studio (https://aistudio.google.com/apikey)
    FAL_KEY          -> clave de Fal.ai (https://fal.ai/dashboard/keys)

Ejecutar:
    python app.py
    # o: uvicorn app:app --reload
"""

import json
import logging
import os
from typing import Optional

import google.generativeai as genai
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
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
# gemini-2.5-flash es estable; si tu cuenta tiene acceso a la familia Gemini 3
# (gemini-3-flash / gemini-3.5-flash) cámbialo aquí o vía env var GEMINI_MODEL.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
# Modelo Flux en Fal.ai. "flux-pro/v1.1" da la mejor calidad fotorrealista;
# "fal-ai/flux/dev" es una alternativa más económica.
FAL_FLUX_MODEL = os.getenv("FAL_FLUX_MODEL", "fal-ai/flux-pro/v1.1")

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

# --------------------------------------------------------------------------------------
# Modelos de entrada
# --------------------------------------------------------------------------------------


class AnalyzeRequest(BaseModel):
    business_url: str = Field(..., description="URL o handle del negocio propio")
    competitor_url: str = Field(..., description="URL o nombre del competidor")


# --------------------------------------------------------------------------------------
# Módulo Scraper
# --------------------------------------------------------------------------------------


def scrape_url(raw_url: str) -> dict:
    """Extrae título, descripción y texto visible de una URL.

    Sitios que renderizan con JavaScript (Instagram, TikTok, Facebook) suelen
    bloquear el scraping directo sin sesión iniciada. Aquí se aprovechan las
    meta-etiquetas Open Graph, que la mayoría de perfiles públicos exponen
    igualmente en el HTML estático. Si falla, se degrada con elegancia
    devolviendo el propio texto de entrada como contexto para la IA, en lugar
    de romper el flujo. Para scraping robusto de redes sociales en producción,
    se recomienda integrar un proveedor dedicado (p. ej. Apify) detrás de esta
    misma función.
    """
    url = raw_url.strip()
    if not url.lower().startswith("http"):
        # El usuario puede escribir solo "@marca" o "Nombre Competidor"
        return {"url": url, "title": url, "content": f"Marca/competidor de referencia: {url}", "scraped": False}

    try:
        with httpx.Client(headers=HEADERS, timeout=12, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        def meta(*names):
            for n in names:
                tag = soup.find("meta", property=n) or soup.find("meta", attrs={"name": n})
                if tag and tag.get("content"):
                    return tag["content"].strip()
            return ""

        title = meta("og:title", "twitter:title") or (soup.title.string.strip() if soup.title and soup.title.string else url)
        description = meta("og:description", "twitter:description", "description")
        body_text = " ".join(
            t.get_text(" ", strip=True) for t in soup.find_all(["h1", "h2", "h3", "p", "li"])[:60]
        )
        content = f"Título: {title}\nDescripción: {description}\nContenido: {body_text}".strip()[:6000]
        return {"url": url, "title": title or url, "content": content or url, "scraped": True}
    except Exception as exc:  # noqa: BLE001 — degradamos con contexto, no rompemos el flujo
        log.warning("No se pudo scrapear %s: %s", url, exc)
        return {
            "url": url,
            "title": url,
            "content": f"No se pudo acceder al contenido de {url} (posible bloqueo anti-bot). "
                       f"Trabaja en base al nombre/URL como contexto general de la marca.",
            "scraped": False,
        }


# --------------------------------------------------------------------------------------
# Módulos de IA (Gemini) — análisis de competencia y estrategia publicitaria
# --------------------------------------------------------------------------------------

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "ganchos_exitosos": {"type": "array", "items": {"type": "string"}},
        "debilidades": {"type": "array", "items": {"type": "string"}},
        "tono_de_marca": {"type": "string"},
        "oportunidad_clave": {"type": "string"},
    },
    "required": ["ganchos_exitosos", "debilidades", "tono_de_marca", "oportunidad_clave"],
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
                    "tipo": {"type": "string", "enum": ["Gancho", "Beneficios", "CTA"]},
                    "titulo": {"type": "string"},
                    "copy": {"type": "string"},
                    "image_prompt": {
                        "type": "string",
                        "description": "Prompt en inglés, muy visual y detallado, para un modelo de imagen fotorrealista",
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

Analiza al competidor y responde en JSON:
- ganchos_exitosos: 3 a 5 ganchos, ideas o ángulos de marketing que el competidor usa bien.
- debilidades: 3 a 5 debilidades, vacíos de mensaje o públicos desatendidos que el negocio propio puede explotar.
- tono_de_marca: describe en una frase el tono de comunicación del competidor.
- oportunidad_clave: la única oportunidad más contundente que el negocio propio debería aprovechar frente a este competidor.
"""
    return _gemini_json(prompt, ANALYSIS_SCHEMA)


def generate_strategy(business: dict, analysis: dict) -> dict:
    prompt = f"""Eres un director creativo publicitario experto en carruseles de alto rendimiento para Instagram/TikTok Ads.

NEGOCIO PROPIO ({business['url']}):
{business['content']}

ANÁLISIS DE LA COMPETENCIA:
{json.dumps(analysis, ensure_ascii=False)}

Diseña una campaña de carrusel de exactamente 3 placas explotando la "oportunidad_clave" y evitando los
"ganchos_exitosos" ya saturados por el competidor. Responde en JSON con:
- nombre_campana: nombre corto y memorable de la campaña.
- hashtags: 6 a 8 hashtags relevantes en español, sin espacios, con #.
- placas: array de exactamente 3 objetos en este orden:
    1) tipo="Gancho": detiene el scroll, plantea el problema o deseo.
    2) tipo="Beneficios": comunica 2-3 beneficios diferenciales concretos.
    3) tipo="CTA": llamado a la acción claro y urgente.
  Cada placa necesita:
    - titulo: texto corto tipo headline para sobreponer en la imagen (máx 8 palabras, en español).
    - copy: copy de apoyo para el pie de foto (1-2 frases, en español).
    - image_prompt: prompt EN INGLÉS, extremadamente descriptivo, para generar una imagen publicitaria
      fotorrealista 3D/producto de altísima calidad (estilo comercial, iluminación de estudio, 8k,
      composición profesional). No incluyas texto ni logos en la descripción de la imagen.
"""
    return _gemini_json(prompt, STRATEGY_SCHEMA)


# --------------------------------------------------------------------------------------
# Módulo Visual — Flux.1 vía Fal.ai
# --------------------------------------------------------------------------------------


def generate_image(prompt: str) -> str:
    if not FAL_KEY or fal_client is None:
        raise RuntimeError("Falta configurar FAL_KEY o instalar el paquete fal-client")

    full_prompt = (
        f"{prompt}, photorealistic advertising photography, commercial product render, "
        f"studio lighting, ultra sharp focus, 8k, high-end ad campaign, no text, no watermark, no logo"
    )
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
        raise RuntimeError("Fal.ai no devolvió ninguna imagen")
    return images[0]["url"]


# --------------------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------------------


@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/analyze")
async def analyze(payload: AnalyzeRequest):
    """Orquesta scraping -> análisis -> estrategia -> imágenes, emitiendo el
    progreso en tiempo real mediante Server-Sent Events para que el frontend
    actualice la pantalla sin recargar ni esperar a la respuesta completa."""

    def sse(stage: str, status: str, data: Optional[dict] = None) -> str:
        payload_json = json.dumps({"stage": stage, "status": status, "data": data}, ensure_ascii=False)
        return f"data: {payload_json}\n\n"

    async def event_stream():
        try:
            yield sse("scraping", "start")
            business = await run_in_threadpool(scrape_url, payload.business_url)
            competitor = await run_in_threadpool(scrape_url, payload.competitor_url)
            yield sse("scraping", "done", {"business_title": business["title"], "competitor_title": competitor["title"]})

            yield sse("analisis", "start")
            analysis = await run_in_threadpool(analyze_competitor, business, competitor)
            yield sse("analisis", "done", analysis)

            yield sse("estrategia", "start")
            strategy = await run_in_threadpool(generate_strategy, business, analysis)
            yield sse("estrategia", "done", {"nombre_campana": strategy["nombre_campana"], "hashtags": strategy["hashtags"]})

            placas_finales = []
            for i, placa in enumerate(strategy["placas"]):
                yield sse("imagen", "start", {"index": i, "tipo": placa["tipo"], "titulo": placa["titulo"]})
                try:
                    image_url = await run_in_threadpool(generate_image, placa["image_prompt"])
                except Exception as img_exc:  # noqa: BLE001
                    log.warning("Fallo generando imagen de placa %s: %s", i, img_exc)
                    image_url = None
                placa_final = {**placa, "image_url": image_url}
                placas_finales.append(placa_final)
                yield sse("imagen", "done", {"index": i, "image_url": image_url})

            yield sse(
                "completo",
                "done",
                {
                    "nombre_campana": strategy["nombre_campana"],
                    "hashtags": strategy["hashtags"],
                    "placas": placas_finales,
                    "analisis": analysis,
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("Error en el pipeline de análisis")
            yield sse("error", "error", {"mensaje": str(exc)})

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

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
