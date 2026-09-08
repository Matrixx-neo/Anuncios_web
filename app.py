"""
SaaS Marketing & Ad Carousel Generator - AI Engine
=================================================
"""

import asyncio
import json
import logging
import os
import random
import urllib.parse
from typing import List, Optional
from urllib.parse import urlparse

import google.generativeai as genai
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from PIL import Image
import io

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
SCRAPE_TIMEOUT = 3.5
POLLINATIONS_BASE = "https://image.pollinations.ai/prompt"

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

app = FastAPI(title="AI Marketing Studio")
templates = Jinja2Templates(directory="templates")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

SOCIAL_DOMAINS = ("instagram.com", "tiktok.com", "facebook.com", "twitter.com", "x.com")

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
            body_text = " ".join(t.get_text(" ", strip=True) for t in soup.find_all(["h1", "h2", "h3", "p", "li"])[:50])

            marca = title or extract_brand_from_url(url)
            content = f"Título: {marca}\nDescripción: {description}\nContenido: {body_text}".strip()[:4000]
            return {"url": url, "title": marca, "content": content, "scraped": True}
    except Exception as exc:
        marca = extract_brand_from_url(url)
        return {"url": url, "title": marca, "content": f"Marca de referencia: {marca}", "scraped": False}

_METRICA = {"type": "object", "properties": {"tu_negocio": {"type": "integer"}, "competencia": {"type": "integer"}}, "required": ["tu_negocio", "competencia"]}
_DIA_PLAN = {"type": "object", "properties": {"idea": {"type": "string"}, "objetivo": {"type": "string"}}, "required": ["idea", "objetivo"]}

CAMPAIGN_SCHEMA = {
    "type": "object",
    "properties": {
        "nombre_campana": {"type": "string"},
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
        "carrusel_placas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tipo": {"type": "string", "enum": ["Gancho", "Problema", "Solucion", "Beneficios", "CTA"]},
                    "titulo": {"type": "string"},
                    "descripcion": {"type": "string"},
                    "image_prompt": {"type": "string"},
                },
                "required": ["tipo", "titulo", "descripcion", "image_prompt"],
            },
        },
    },
    "required": ["nombre_campana", "score_competencia", "metricas_comparativas", "matriz_swot", "plan_semanal", "recomendaciones_clave", "hashtags", "carrusel_placas"],
}

def optimize_image(image_bytes: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((1024, 1024))
    return img

def generate_campaign(business: dict, competitor: dict, angulo: Optional[str] = None, image_pil_list: Optional[List[Image.Image]] = None) -> dict:
    if not GEMINI_API_KEY:
        raise RuntimeError("Falta GEMINI_API_KEY en variables de entorno")

    linea_angulo = f"Enfoque solicitado: '{angulo}'." if angulo else "Enfoque publicitario equilibrado y de alto impacto."
    
    prompt = f"""Eres un director creativo publicitario de nivel mundial.

NEGOCIO PROPIO ({business['url']}):
{business['content']}

COMPETIDOR ({competitor['url']}):
{competitor['content']}

{linea_angulo}

REGLAS STRICTAS PARA IMÁGENES:
- Identifica el producto o servicio exacto comercializado.
- Si hay fotos adjuntas del producto, analiza sus texturas, empaque y colores para mantener coherencia de marca.
- Cada 'image_prompt' debe estar en INGLÉS y mencionar explícitamente el nombre del producto/rubro exacto. Jamás temas genéricos ajenos.
- Estilo: 'Professional product photography, sharp focus, 8k resolution, studio soft lighting, hyper-realistic, pristine detail'.
- Sin texto ni letras en la imagen.
"""

    model = genai.GenerativeModel(GEMINI_MODEL)
    contents = [prompt]
    if image_pil_list:
        contents.extend(image_pil_list)

    response = model.generate_content(
        contents,
        generation_config={
            "response_mime_type": "application/json",
            "response_schema": CAMPAIGN_SCHEMA,
            "temperature": 0.7,
        },
    )
    return json.loads(response.text)

def build_pollinations_url(prompt: str, fallback_text: str = "product") -> str:
    base_prompt = (prompt or fallback_text).strip()
    hd_prompt = f"{base_prompt}, sharp focus, 8k resolution, ultra detailed, professional studio shot"
    encoded = urllib.parse.quote(hd_prompt)
    seed = random.randint(1, 999_999)
    return f"{POLLINATIONS_BASE}/{encoded}?model=flux&width=1080&height=1350&nologo=true&enhance=true&quality=100&seed={seed}"

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.post("/api/analyze")
async def analyze(
    business_url: str = Form(...),
    competitor_url: str = Form(...),
    angulo: Optional[str] = Form(None),
    images: List[UploadFile] = File(None)
):
    def sse(stage: str, status: str, mensaje: str, data: Optional[dict] = None) -> str:
        body = json.dumps({"stage": stage, "status": status, "mensaje": mensaje, "data": data}, ensure_ascii=False)
        return f"data: {body}\n\n"

    async def event_stream():
        try:
            yield sse("scraping", "start", "🔍 Analizando marca y mercado...")
            business = await run_in_threadpool(scrape_url, business_url)
            competitor = await run_in_threadpool(scrape_url, competitor_url)

            pil_images = []
            if images:
                yield sse("scraping", "progress", "📸 Optimizando imágenes cargadas...")
                for img in images:
                    if img.content_type and img.content_type.startswith("image/"):
                        contents = await img.read()
                        if contents:
                            optimized_img = await run_in_threadpool(optimize_image, contents)
                            pil_images.append(optimized_img)

            yield sse("scraping", "done", "🔍 Información procesada con éxito.")

            yield sse("estrategia", "start", "🧠 Generando estrategia y conceptos de carrusel HD...")
            campana = await run_in_threadpool(generate_campaign, business, competitor, angulo, pil_images)

            yield sse(
                "estrategia",
                "done",
                "🧠 Estrategia completada.",
                {
                    "nombre_campana": campana["nombre_campana"],
                    "score_competencia": campana["score_competencia"],
                    "metricas_comparativas": campana["metricas_comparativas"],
                    "matriz_swot": campana["matriz_swot"],
                    "plan_semanal": campana["plan_semanal"],
                    "recomendaciones_clave": campana["recomendaciones_clave"],
                    "hashtags": campana["hashtags"],
                },
            )

            total = len(campana["carrusel_placas"])
            for i, placa in enumerate(campana["carrusel_placas"]):
                yield sse(
                    "imagen",
                    "start",
                    f"🎨 Diseñando placa visual {i + 1}/{total}...",
                    {
                        "index": i,
                        "total": total,
                        "tipo": placa["tipo"],
                        "titulo": placa["titulo"],
                        "descripcion": placa["descripcion"],
                    },
                )
                await asyncio.sleep(0.1)
                image_url = build_pollinations_url(placa["image_prompt"], placa["titulo"])
                yield sse(
                    "imagen",
                    "done",
                    f"🎨 Placa {i + 1}/{total} lista.",
                    {"index": i, "image_url": image_url},
                )

            yield sse("completo", "done", "✨ Proceso finalizado con éxito.", {"nombre_campana": campana["nombre_campana"]})

        except Exception as exc:
            log.exception("Error en pipeline")
            yield sse("error", "error", f"⚠️ Error: {str(exc)}")

    return StreamingResponse(event_stream(), media_type="text/event-stream")

@app.get("/api/health")
async def health():
    return {"status": "ok", "gemini_configurado": bool(GEMINI_API_KEY)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=True)
