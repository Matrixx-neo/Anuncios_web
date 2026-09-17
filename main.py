import os
import json
import asyncio
import urllib.parse
import re
import random
from typing import List, Optional
from io import BytesIO
from PIL import Image

# Para web scraping de las URLs de competidores
import httpx 

from fastapi import FastAPI, Request, Form, File, UploadFile, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from authlib.integrations.starlette_client import OAuth
from dotenv import load_dotenv
import google.generativeai as genai

import database

load_dotenv()

BASE_URL = os.getenv("BASE_URL", "https://anuncios-web-c4bv.onrender.com").replace("http://", "https://").rstrip("/")
ADMIN_EMAILS = [e.strip() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()]
SESSION_SECRET = os.getenv("SESSION_SECRET", "super-secret-session-key")

app = FastAPI()

# Middlewares Anti-Proxy (Render) y Sesiones Seguras
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, https_only=True, same_site="lax")

templates = Jinja2Templates(directory="templates")
database.init_db()

# Configuración OAuth
oauth = OAuth()
oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

def get_current_user(request: Request):
    user_info = request.session.get('user')
    if not user_info:
        return None
    email = user_info.get('email')
    db_user = database.get_user(email)
    if not db_user:
        db_user = database.create_user(email)
    return dict(db_user)

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    user = get_current_user(request)
    is_admin = user['email'] in ADMIN_EMAILS if user else False
    return templates.TemplateResponse(
        request=request, 
        name="index.html", 
        context={"user": user, "is_admin": is_admin}
    )

@app.get('/auth/login')
async def login(request: Request):
    # REDIRECT_URI ESTRICTAMENTE MANTENIDO PARA GOOGLE OAUTH
    redirect_uri = "https://anuncios-web-c4bv.onrender.com/auth/callback"
    return await oauth.google.authorize_redirect(request, redirect_uri)

@app.get('/auth/callback')
async def auth_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
        user = token.get('userinfo')
        if user:
            request.session['user'] = user
            database.create_user(user['email'])
    except Exception as e:
        print(f"OAuth Error: {str(e)}")
    return RedirectResponse(url='/')

@app.get('/auth/logout')
async def logout(request: Request):
    request.session.pop('user', None)
    return RedirectResponse(url='/')

@app.get('/login')
async def login_redirect(): return RedirectResponse(url='/auth/login')

@app.get('/logout')
async def logout_redirect(): return RedirectResponse(url='/auth/logout')

# --- FUNCIONES CORE: SCRAPING & IA ---

async def fetch_url_text(url: str) -> str:
    """Extrae texto limpio de una URL usando regex para evitar dependencias pesadas si no están instaladas."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            text = re.sub(r'<style.*?>.*?</style>', ' ', resp.text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<script.*?>.*?</script>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            return text[:2500]
    except Exception as e:
        return f"[Error extrayendo {url}: {str(e)}]"

@app.post("/api/generate")
async def generate_content(
    request: Request,
    input_text: str = Form(...),
    competitor_text: str = Form(...),
    files: List[UploadFile] = File(None)
):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")

    email = user['email']
    is_admin = email in ADMIN_EMAILS
    credits = user['credits']

    pil_images = []
    if files:
        for file in files:
            if file.filename and file.content_type.startswith("image/"):
                contents = await file.read()
                img = Image.open(BytesIO(contents))
                pil_images.append(img)

    async def sse_generator():
        # Setup GenAI con Rotación de Keys
        api_keys = [k.strip() for k in os.getenv("GEMINI_API_KEYS", "").split(",") if k.strip()]
        active_key = random.choice(api_keys) if api_keys else os.getenv("GEMINI_API_KEY", "")
        genai.configure(api_key=active_key)
        model = genai.GenerativeModel('gemini-1.5-flash')

        yield f"data: {json.dumps({'type': 'log', 'text': 'Iniciando pipeline estratégico...'})}\n\n"
        await asyncio.sleep(0.1)

        # 1. Scraping Concurrente si se detectan URLs
        url_pattern = re.compile(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')
        my_urls = url_pattern.findall(input_text)
        comp_urls = url_pattern.findall(competitor_text)

        my_context, comp_context = input_text, competitor_text

        if my_urls or comp_urls:
            yield f"data: {json.dumps({'type': 'log', 'text': 'Extrayendo datos de URLs en tiempo real...'})}\n\n"
            tasks = []
            if my_urls: tasks.append(fetch_url_text(my_urls[0]))
            else: tasks.append(asyncio.sleep(0)) # dummy task para mantener orden
            
            if comp_urls: tasks.append(fetch_url_text(comp_urls[0]))
            else: tasks.append(asyncio.sleep(0))

            results = await asyncio.gather(*tasks)
            if my_urls and results[0]: my_context += f"\n[Contenido web escaneado: {results[0]}]"
            if comp_urls and results[1]: comp_context += f"\n[Contenido web escaneado: {results[1]}]"

        yield f"data: {json.dumps({'type': 'log', 'text': 'Sintetizando Matriz FODA y comparativa de mercado...'})}\n\n"

        # TIEMPO 1: Análisis FODA (Gratis)
        prompt_foda = f"""Analiza detalladamente mi negocio: '{my_context}' frente al competidor: '{comp_context}'.
        Devuelve el análisis en este orden y con encabezados Markdown limpios (##):
        ## Diagnóstico Comparativo
        (Resumen de la situación en 1 párrafo)
        ## Puntos Fuertes y Débiles
        (Lista de ventajas y desventajas directas)
        ## Matriz FODA
        (Desglose claro con viñetas: Fortalezas, Oportunidades, Debilidades, Amenazas)."""
        
        contents_foda = pil_images + [prompt_foda] if pil_images else [prompt_foda]
        
        try:
            response_foda = model.generate_content(contents_foda, stream=True)
            for chunk in response_foda:
                yield f"data: {json.dumps({'type': 'foda', 'text': chunk.text})}\n\n"
                await asyncio.sleep(0.01)
        except Exception as e:
            yield f"data: {json.dumps({'type': 'log', 'text': f'Aviso IA (Rotando llave en próximo intento): {str(e)}'})}\n\n"

        # PAYWALL CHECK SERVIDOR
        if not is_admin and credits <= 0:
            yield f"data: {json.dumps({'type': 'log', 'text': 'Análisis parcial completado. Requiere recarga.'})}\n\n"
            yield f"data: {json.dumps({'type': 'paywall_active'})}\n\n"
            return 

        # Descuento de crédito
        if not is_admin:
            database.update_credits(email, credits - 1)
            yield f"data: {json.dumps({'type': 'credit_update', 'credits': credits - 1})}\n\n"

        yield f"data: {json.dumps({'type': 'log', 'text': 'Estructurando Copy AIDA y proyecciones financieras...'})}\n\n"

        # TIEMPO 2: Premium Text & Images
        prompt_premium = f"""Para '{input_text}':
        Genera un Copy publicitario letal utilizando la metodología AIDA (Atención, Interés, Deseo, Acción) para vencer a '{competitor_text}'.
        Incluye luego un apartado 'Métricas Estimadas' (CTR %, CPC, y ROI proyectado). Formato Markdown."""
        contents_premium = pil_images + [prompt_premium] if pil_images else [prompt_premium]
        
        try:
            response_premium = model.generate_content(contents_premium, stream=True)
            for chunk in response_premium:
                yield f"data: {json.dumps({'type': 'premium_text', 'text': chunk.text})}\n\n"
                await asyncio.sleep(0.01)
        except Exception as e:
            yield f"data: {json.dumps({'type': 'log', 'text': 'Error en fase premium.'})}\n\n"
            
        yield f"data: {json.dumps({'type': 'log', 'text': 'Renderizando Carrusel HD vía GPU...'})}\n\n"

        # Generador HD Pollinations (1080x1080 Cuadrado)
        clean_name = my_context[:60].replace('\n', ' ')
        p1 = urllib.parse.quote(f"Professional cinematic product advertising photography for {clean_name}, hyperrealistic, 8k, studio lighting, highly detailed")
        p2 = urllib.parse.quote(f"Modern neon aesthetic social media banner background for {clean_name}, dark mode style, glowing cyan and purple accents, 4k")
        images = [
            f"https://pollinations.ai/p/{p1}?width=1080&height=1080&nologo=true",
            f"https://pollinations.ai/p/{p2}?width=1080&height=1080&nologo=true"
        ]
        
        yield f"data: {json.dumps({'type': 'images', 'urls': images})}\n\n"
        yield f"data: {json.dumps({'type': 'log', 'text': 'Proceso 100% Completado.'})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    # Headers obligatorios para bypass del buffering en Nginx/Render
    return StreamingResponse(sse_generator(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no"
    })

@app.post("/api/checkout")
async def process_checkout(
    request: Request,
    operation_code: str = Form(None),
    voucher_file: UploadFile = File(None)
):
    user = get_current_user(request)
    if not user: raise HTTPException(status_code=401, detail="No autorizado")

    voucher_b64 = None
    if voucher_file and voucher_file.filename:
        import base64
        contents = await voucher_file.read()
        voucher_b64 = base64.b64encode(contents).decode('utf-8')

    database.create_transaction(
        email=user['email'], amount=15.00,
        operation_code=operation_code, voucher_b64=voucher_b64
    )
    return {"status": "success"}
