import os
import json
import asyncio
import logging
from typing import Optional

import google.generativeai as genai
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

try:
    import fal_client
except ImportError:
    fal_client = None

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("adgenius")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
FAL_KEY = os.getenv("FAL_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
FAL_FLUX_MODEL = os.getenv("FAL_FLUX_MODEL", "fal-ai/flux/dev")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
if FAL_KEY:
    os.environ.setdefault("FAL_KEY", FAL_KEY)

app = FastAPI(title="AdGenius API")
templates = Jinja2Templates(directory="templates")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

def scrape_url(raw_url: str) -> dict:
    url = raw_url.strip()
    if not url.lower().startswith("http"):
        return {"url": url, "title": url, "content": f"Marca/competidor de referencia: {url}", "scraped": False}

    try:
        with httpx.Client(headers=HEADERS, timeout=12, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            def meta(names):
                for n in names:
                    tag = soup.find("meta", property=n) or soup.find("meta", attrs={"name": n})
                    if tag and tag.get("content"):
                        return tag["content"].strip()
                return ""

            title = meta(["og:title", "twitter:title"]) or (soup.title.string.strip() if soup.title and soup.title.string else url)
            description = meta(["og:description", "twitter:description", "description"])
            body_text = " ".join([t.get_text(strip=True) for t in soup.find_all(["h1", "h2", "h3", "p", "li"])])[:600]

            content = f"Título: {title}\nDescripción: {description}\nContenido: {body_text}".strip()[:1000]
            return {"url": url, "title": title, "content": content or url, "scraped": True}
    except Exception as exc:
        log.warning("No se pudo scrapear %s: %s", url, exc)
        return {"url": url, "title": url, "content": f"Trabaja en base al nombre/URL como contexto general de la marca: {url}", "scraped": False}

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "ganchos_exitosos": {"type": "array", "items": {"type": "string"}},
        "debilidades": {"type": "array", "items": {"type": "string"}},
        "tono_de_marca": {"type": "string"},
        "oportunidad_clave": {"type": "string"}
    },
    "required": ["ganchos_exitosos", "debilidades", "tono_de_marca", "oportunidad_clave"]
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
                    "image_prompt": {"type": "string", "description": "Prompt en inglés, fotorrealista, para modelo de imagen."}
                },
                "required": ["tipo", "titulo", "copy", "image_prompt"]
            }
        }
    },
    "required": ["nombre_campana", "hashtags", "placas"]
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
            "temperature": 0.7
        }
    )
    return json.loads(response.text)

def analyze_competitor(business: dict, competitor: dict) -> dict:
    prompt = f"""Eres un estratega sénior de marketing digital.
NEGOCIO PROPIO: {business['content']}
COMPETIDOR: {competitor['content']}
Analiza al competidor y responde en JSON según el schema."""
    return _gemini_json(prompt, ANALYSIS_SCHEMA)

def generate_strategy(business: dict, analysis: dict) -> dict:
    prompt = f"""Eres un director creativo publicitario experto en carruseles para Instagram/TikTok.
NEGOCIO PROPIO: {business['content']}
ANÁLISIS DE COMPETENCIA: {json.dumps(analysis, ensure_ascii=False)}
Diseña una campaña de carrusel de exactamente 3 placas explotando la oportunidad_clave."""
    return _gemini_json(prompt, STRATEGY_SCHEMA)

def generate_image(prompt: str) -> str:
    if not FAL_KEY or fal_client is None:
        raise RuntimeError("Falta configurar FAL_KEY o instalar el paquete fal-client")
    full_prompt = f"{prompt}, photorealistic advertising photography, commercial product render, studio lighting, ultra sharp focus, 8k"
    result = fal_client.subscribe(
        FAL_FLUX_MODEL,
        arguments={"prompt": full_prompt, "image_size": "square_hd", "num_images": 1, "enable_safety_checker": True}
    )
    images = result.get("images") or []
    if not images:
        raise RuntimeError("Fal.ai no devolvió ninguna imagen")
    return images[0]["url"]

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/generar")
async def generar_form(request: Request, producto: str = Form(...), descripcion: str = Form(...)):
    async def event_stream():
        def sse(stage: str, status: str, data: Optional[dict] = None) -> str:
            payload_json = json.dumps({"stage": stage, "status": status, "data": data}, ensure_ascii=False)
            return f"data: {payload_json}\n\n"

        try:
            yield sse("scraping", "start")
            business = await run_in_threadpool(scrape_url, producto)
            competitor = await run_in_threadpool(scrape_url, descripcion)
            yield sse("scraping", "done", {"business_title": business["title"], "competitor_title": competitor["title"]})

            yield sse("analisis", "start")
            analysis = await run_in_threadpool(analyze_competitor, business, competitor)
            yield sse("analisis", "done", analysis)

            yield sse("estrategia", "start")
            strategy = await run_in_threadpool(generate_strategy, business, analysis)
            yield sse("estrategia", "done", {"nombre_campana": strategy["nombre_campana"], "hashtags": strategy["hashtags"]})

            placas_finales = []
            for i, place in enumerate(strategy.get("placas", [])):
                yield sse("imagen", "start", {"index": i, "tipo": place["tipo"], "titulo": place["titulo"]})
                try:
                    img_url = await run_in_threadpool(generate_image, place["image_prompt"])
                except Exception as img_exc:
                    log.warning("Fallo generando imagen de placa %s: %s", i, img_exc)
                    img_url = None

                placa_final = {**place, "image_url": img_url}
                placas_finales.append(placa_final)
                yield sse("imagen", "done", {"index": i, "image_url": img_url})

            yield sse("complete", "done", {
                "nombre_campana": strategy["nombre_campana"],
                "hashtags": strategy["hashtags"],
                "placas": placas_finales,
                "analisis": analysis
            })
        except Exception as exc:
            log.exception("Error en el pipeline de análisis")
            yield sse("error", "error", {"mensaje": str(exc)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "gemini_configurado": bool(GEMINI_API_KEY),
        "fal_configurado": bool(FAL_KEY)
    }
