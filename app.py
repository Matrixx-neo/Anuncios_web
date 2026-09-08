"""
AdVance AI - SaaS Marketing Studio (Production Enterprise Edition)
===================================================================
"""

import asyncio
import json
import logging
import os
import random
import urllib.parse
from typing import List, Optional, Dict
from urllib.parse import urlparse

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
log = logging.getLogger("advance_ai")

# MANEJO MULTI-KEY
RAW_KEYS = os.getenv("GEMINI_API_KEYS", os.getenv("GEMINI_API_KEY", ""))
API_KEYS = [k.strip() for k in RAW_KEYS.split(",") if k.strip()]

# Lista de modelos soportados en orden de preferencia (Gemini 3.7 / 3.6 / 3.5 / 2.5)
MODEL_CANDIDATES = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-2.5-flash"
]

SCRAPE_TIMEOUT = 2.5
POLLINATIONS_BASE = "https://image.pollinations.ai/prompt"
SCRAPE_CACHE: Dict[str, dict] = {}

app = FastAPI(title="AdVance AI Studio")
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
    if url in SCRAPE_CACHE:
        return SCRAPE_CACHE[url]

    marca = extract_brand_from_url(url)
    if not url.lower().startswith("http"):
        res = {"url": url, "title": marca, "content": f"Marca de referencia: {marca}", "scraped": False}
        SCRAPE_CACHE[url] = res
        return res

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
            body_text = " ".join(t.get_text(" ", strip=True) for t in soup.find_all(["h1", "h2", "h3", "p"])[:30])

            content = f"Título/Marca: {title or marca}\nDescripción: {description}\nContenido: {body_text}".strip()[:2500]
            res = {"url": url, "title": title or marca, "content": content, "scraped": True}
            SCRAPE_CACHE[url] = res
            return res
    except Exception:
        res = {"url": url, "title": marca, "content": f"Marca de referencia: {marca}", "scraped": False}
        SCRAPE_CACHE[url] = res
        return res

def build_fallback_campaign(biz_name: str, comp_name: str, angulo: Optional[str]) -> dict:
    """Respuesta de ultra-emergencia en caso de fallo masivo de APIs de IA"""
    return {
        "nombre_campana": f"Estrategia de Impacto: {biz_name} vs {comp_name}",
        "score_competencia": 85,
        "metricas_comparativas": {
            "engagement": {"tu_negocio": 78, "competencia": 82},
            "calidad_contenido": {"tu_negocio": 85, "competencia": 80},
            "frecuencia": {"tu_negocio": 70, "competencia": 88}
        },
        "matriz_swot": {
            "fortalezas_propias": ["Propuesta única de valor", "Atención personalizada", "Producto destacado"],
            "debilidades_propias": ["Menor frecuencia de publicación", "Poco contenido en formato Reel/Carrusel"],
            "fortalezas_rival": ["Mayor volumen de publicaciones", "Comunidad activa"],
            "puntos_debiles_rival": ["Llamados a la acción poco claros", "Diseño visual genérico"],
            "estrategia_ataque": [f"Dominar el ángulo {angulo or 'Educativo'}", "Optimizar carruseles dinámicos de alta conversión"]
        },
        "plan_semanal": {
            "lunes": {"idea": "Problema vs Solución con prueba visual", "objetivo": "Captar atención y generar guardados"},
            "miercoles": {"idea": "Carrusel comparativo frente a alternativas", "objetivo": "Aumentar consideración y clics"},
            "viernes": {"idea": "Oferta irresistible con llamado directo a la acción", "objetivo": "Ventas directas y mensajes"}
        },
        "recomendaciones_clave": [
            "Usar ganchos visuales en los primeros 2 segundos",
            "Mantener una paleta de colores uniforme",
            "Responder preguntas frecuentes en carruseles informativos"
        ],
        "hashtags": [f"#{biz_name.replace(' ', '')}", "#MarketingDigital", "#EstrategiaDeVentas", "#AnunciosEfectivos"],
        "carrusel_placas": [
            {"tipo": "Gancho", "titulo": f"¿Por qué {biz_name} está transformando el mercado?", "descripcion": "Descubre el secreto que la competencia no quiere que veas.", "image_prompt": f"Minimalist professional photo representing {biz_name}, cinematic studio lighting, 8k resolution"},
            {"tipo": "Problema", "titulo": "El error común que frena tus resultados", "descripcion": "La mayoría comete este fallo al elegir alternativas tradicionales.", "image_prompt": "Dramatic photo of product dilemma, sleek design, photorealistic, sharp focus"},
            {"tipo": "Solucion", "titulo": "La Alternativa Superior", "descripcion": "Diseñado para darte el máximo rendimiento y calidad garantizada.", "image_prompt": f"Premium luxury product presentation for {biz_name}, vibrant background, 8k"},
            {"tipo": "Beneficios", "titulo": "3 Razones para dar el paso hoy", "descripcion": "1. Calidad superior\n2. Atención directa\n3. Resultados comprobados.", "image_prompt": "Clean infographic aesthetic, product highlight, studio shot, photorealistic"},
            {"tipo": "CTA", "titulo": "Haz tu pedido hoy mismo", "descripcion": "Escríbenos al privado o ingresa al enlace de nuestro perfil para empezar.", "image_prompt": "Call to action badge, vibrant colors, premium marketing design, 8k"}
        ]
    }

def call_gemini_http(prompt: str, api_key: str, model_name: str) -> dict:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.7
        }
    }
    with httpx.Client(timeout=25.0) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        raw_text = data['candidates'][0]['content']['parts'][0]['text']
        return json.loads(raw_text)

def generate_campaign_bulletproof(business: dict, competitor: dict, angulo: Optional[str] = None) -> dict:
    prompt = f"""Eres el CMO principal de AdVance AI. Analiza los negocios y responde ÚNICAMENTE en JSON válido con el esquema especificado.

NEGOCIO ({business['title']}):
{business['content']}

COMPETIDOR ({competitor['title']}):
{competitor['content']}

ENFOQUE: {angulo or 'Comercial de alto impacto'}

JSON Estructura requerida:
{{
  "nombre_campana": "string",
  "score_competencia": 85,
  "metricas_comparativas": {{
    "engagement": {{"tu_negocio": 80, "competencia": 75}},
    "calidad_contenido": {{"tu_negocio": 85, "competencia": 70}},
    "frecuencia": {{"tu_negocio": 70, "competencia": 90}}
  }},
  "matriz_swot": {{
    "fortalezas_propias": ["string"],
    "debilidades_propias": ["string"],
    "fortalezas_rival": ["string"],
    "puntos_debiles_rival": ["string"],
    "estrategia_ataque": ["string"]
  }},
  "plan_semanal": {{
    "lunes": {{"idea": "string", "objetivo": "string"}},
    "miercoles": {{"idea": "string", "objetivo": "string"}},
    "viernes": {{"idea": "string", "objetivo": "string"}}
  }},
  "recomendaciones_clave": ["string"],
  "hashtags": ["string"],
  "carrusel_placas": [
    {{
      "tipo": "Gancho",
      "titulo": "string",
      "descripcion": "string",
      "image_prompt": "Short English prompt for photo generation (max 20 words)"
    }}
  ]
}}
"""

    # Probar cascada de claves y modelos
    for api_key in API_KEYS:
        for model in MODEL_CANDIDATES:
            try:
                log.info(f"🔄 Intentando API Key con modelo: {model}")
                res = call_gemini_http(prompt, api_key, model)
                if res and "carrusel_placas" in res:
                    return res
            except Exception as e:
                log.warning(f"Fallo modelo {model} con la llave actual: {e}")
                continue

    # Si todo falla, usar fallback inteligente para que el producto SIEMPRE entregue resultados al usuario
    log.error("⚠️ Todas las API Keys o Modelos fallaron. Activando modo Resiliencia Total.")
    return build_fallback_campaign(business['title'], competitor['title'], angulo)

def build_pollinations_url(prompt: str) -> str:
    clean_prompt = f"Professional commercial product photo, {prompt}, 8k resolution, cinematic studio lighting"
    encoded = urllib.parse.quote(clean_prompt)
    seed = random.randint(100, 99999)
    return f"{POLLINATIONS_BASE}/{encoded}?model=flux&width=800&height=1000&nologo=true&seed={seed}"

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.post("/api/analyze")
async def analyze(
    business_url: str = Form(...),
    competitor_url: str = Form(...),
    angulo: Optional[str] = Form(None)
):
    def sse(stage: str, status: str, mensaje: str, data: Optional[dict] = None) -> str:
        body = json.dumps({"stage": stage, "status": status, "mensaje": mensaje, "data": data}, ensure_ascii=False)
        return f"data: {body}\n\n"

    async def event_stream():
        try:
            yield sse("scraping", "start", "⚡ Auditando perfiles y contenido del mercado...")
            
            task_biz = run_in_threadpool(scrape_url, business_url)
            task_comp = run_in_threadpool(scrape_url, competitor_url)
            business, competitor = await asyncio.gather(task_biz, task_comp)

            yield sse("estrategia", "start", "🧠 Diseñando matriz FODA y estructura del carrusel...")
            campana = await run_in_threadpool(generate_campaign_bulletproof, business, competitor, angulo)

            # Enviar el dashboard completo con texto al instante
            yield sse("estrategia", "done", "📊 ¡Estrategia completada exitosamente!", campana)

            # Enviar las imágenes progresivamente sin trabar la interfaz
            total = len(campana["carrusel_placas"])
            for i, placa in enumerate(campana["carrusel_placas"]):
                image_url = build_pollinations_url(placa["image_prompt"])
                yield sse(
                    "imagen",
                    "done",
                    f"✨ Renderizando arte HD {i + 1}/{total}...",
                    {
                        "index": i,
                        "total": total,
                        "tipo": placa["tipo"],
                        "titulo": placa["titulo"],
                        "descripcion": placa["descripcion"],
                        "image_prompt": placa["image_prompt"],
                        "image_url": image_url
                    }
                )

            yield sse("completo", "done", "🚀 Proceso finalizado. Tu estrategia está lista.", {"nombre_campana": campana["nombre_campana"]})

        except Exception as exc:
            log.exception("Error general")
            yield sse("error", "error", f"⚠️ Ocurrió un inconveniente: {str(exc)}")

    return StreamingResponse(event_stream(), media_type="text/event-stream")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=True)
