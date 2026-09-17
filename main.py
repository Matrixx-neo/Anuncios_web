import os
import json
import asyncio
import urllib.parse
import re
import random
from typing import List, Optional
from io import BytesIO
from PIL import Image

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

app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, https_only=True, same_site="lax")

templates = Jinja2Templates(directory="templates")
database.init_db()

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
    if not user_info: return None
    email = user_info.get('email')
    db_user = database.get_user(email)
    if not db_user: db_user = database.create_user(email)
    return dict(db_user)

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    user = get_current_user(request)
    is_admin = user['email'] in ADMIN_EMAILS if user else False
    return templates.TemplateResponse(
        request=request, name="index.html", context={"user": user, "is_admin": is_admin}
    )

@app.get('/auth/login')
async def login(request: Request):
    # OAUTH EXACTO: No tocar
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
    except Exception as e: print(f"OAuth Error: {str(e)}")
    return RedirectResponse(url='/')

@app.get('/auth/logout')
async def logout(request: Request):
    request.session.pop('user', None)
    return RedirectResponse(url='/')

@app.get('/login')
async def login_redirect(): return RedirectResponse(url='/auth/login')

@app.get('/logout')
async def logout_redirect(): return RedirectResponse(url='/auth/logout')

# --- EXTRACCIÓN Y PIPELINE IA ---

async def fetch_url_text(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            text = re.sub(r'<style.*?>.*?</style>', ' ', resp.text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<script.*?>.*?</script>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<[^>]+>', ' ', text)
            return re.sub(r'\s+', ' ', text).strip()[:1500]
    except:
        return ""

@app.post("/api/generate")
async def generate_content(
    request: Request,
    input_text: str = Form(...),
    competitor_text: str = Form(...),
    focus_text: str = Form(""),
    files: List[UploadFile] = File(None)
):
    user = get_current_user(request)
    if not user: raise HTTPException(status_code=401, detail="No autorizado")

    email = user['email']
    is_admin = email in ADMIN_EMAILS
    credits = user['credits']

    pil_images = []
    if files:
        for file in files:
            if file.filename and file.content_type.startswith("image/"):
                contents = await file.read()
                pil_images.append(Image.open(BytesIO(contents)))

    async def sse_generator():
        # Configuración IA con Rotación
        api_keys = [k.strip() for k in os.getenv("GEMINI_API_KEYS", "").split(",") if k.strip()]
        active_key = random.choice(api_keys) if api_keys else os.getenv("GEMINI_API_KEY", "")
        genai.configure(api_key=active_key)
        # Forzar JSON response_mime_type en Gemini 1.5
        model = genai.GenerativeModel('gemini-1.5-flash', generation_config={"response_mime_type": "application/json"})

        yield f"data: {json.dumps({'type': 'log', 'text': 'Analizando parámetros de entrada...'})}\n\n"
        await asyncio.sleep(0.5)

        url_pattern = re.compile(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')
        my_urls = url_pattern.findall(input_text)
        comp_urls = url_pattern.findall(competitor_text)

        my_context, comp_context = input_text, competitor_text

        if my_urls or comp_urls:
            yield f"data: {json.dumps({'type': 'log', 'text': 'Extrayendo competidor y escaneando URLs (timeout 3s)...'})}\n\n"
            tasks = []
            tasks.append(fetch_url_text(my_urls[0]) if my_urls else asyncio.sleep(0))
            tasks.append(fetch_url_text(comp_urls[0]) if comp_urls else asyncio.sleep(0))
            results = await asyncio.gather(*tasks)
            if my_urls and results[0]: my_context += f" | Web: {results[0]}"
            if comp_urls and results[1]: comp_context += f" | Web: {results[1]}"

        yield f"data: {json.dumps({'type': 'log', 'text': 'Estructurando matriz FODA y estrategia JSON...'})}\n\n"

        prompt = f"""
        Actúa como Estratega de Marketing Senior. 
        Analiza Mi Negocio: '{my_context}'. 
        Competidor: '{comp_context}'.
        Enfoque/Ángulo: '{focus_text}'.

        Debes devolver ÚNICAMENTE un objeto JSON con esta estructura exacta:
        {{
            "campaign_name": "Nombre creativo de la campaña",
            "score": <número 1-100>,
            "metrics": {{
                "engagement": {{"me": <1-10>, "rival": <1-10>}},
                "visual": {{"me": <1-10>, "rival": <1-10>}},
                "frequency": {{"me": <1-10>, "rival": <1-10>}}
            }},
            "attack_opportunities": [
                {{"weakness": "Debilidad 1 del rival", "tactic": "Estrategia de ataque 1"}},
                {{"weakness": "Debilidad 2 del rival", "tactic": "Estrategia de ataque 2"}}
            ],
            "content_plan": {{
                "monday": "Gancho y tema corto para Lunes",
                "wednesday": "Tema de valor profundo para Miércoles",
                "friday": "Oferta o CTA de venta para Viernes"
            }},
            "recommendations": ["Recomendación 1", "Recomendación 2", "Recomendación 3"],
            "hashtags": "#hashtag1 #hashtag2 #hashtag3"
        }}
        """
        contents = pil_images + [prompt] if pil_images else [prompt]

        # TIEMPO 1: Generación y Emisión de Estrategia JSON
        try:
            response = await model.generate_content_async(contents)
            raw_text = response.text.strip()
            # Limpiar posible formato markdown residual
            if raw_text.startswith("```json"): raw_text = raw_text[7:-3]
            strategy_data = json.loads(raw_text)
            yield f"data: {json.dumps({'type': 'strategy', 'data': strategy_data})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'log', 'text': f'Error de IA: {str(e)}'})}\n\n"
            return

        # PAYWALL CHECK SERVIDOR
        if not is_admin and credits <= 0:
            yield f"data: {json.dumps({'type': 'log', 'text': 'Requiere créditos para Sección PRO...'})}\n\n"
            yield f"data: {json.dumps({'type': 'paywall'})}\n\n"
            return 

        # Descontar crédito si es usuario regular
        if not is_admin:
            database.update_credits(email, credits - 1)
            yield f"data: {json.dumps({'type': 'credit_update', 'credits': credits - 1})}\n\n"

        yield f"data: {json.dumps({'type': 'log', 'text': 'Renderizando Carrusel HD vía GPU...'})}\n\n"

        # TIEMPO 2: Carrusel Publicitario HD (4 Placas)
        clean_name = my_context[:50].replace('\n', ' ')
        p_base = f"hyperrealistic cinematic product advertising photography for {clean_name}, modern neon lighting, highly detailed, 8k"
        
        urls = [
            f"[https://pollinations.ai/p/](https://pollinations.ai/p/){urllib.parse.quote(p_base + ' vibrant hook slide')}?width=1080&height=1080&nologo=true&seed={random.randint(1,9999)}",
            f"[https://pollinations.ai/p/](https://pollinations.ai/p/){urllib.parse.quote(p_base + ' showing value proposition, clean background')}?width=1080&height=1080&nologo=true&seed={random.randint(1,9999)}",
            f"[https://pollinations.ai/p/](https://pollinations.ai/p/){urllib.parse.quote(p_base + ' comparing against competitor, split contrast')}?width=1080&height=1080&nologo=true&seed={random.randint(1,9999)}",
            f"[https://pollinations.ai/p/](https://pollinations.ai/p/){urllib.parse.quote(p_base + ' final call to action, premium dark mode UI')}?width=1080&height=1080&nologo=true&seed={random.randint(1,9999)}"
        ]
        
        yield f"data: {json.dumps({'type': 'carousel', 'urls': urls})}\n\n"
        yield f"data: {json.dumps({'type': 'log', 'text': 'Proceso completado.'})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    # Anti-Buffering Headers requeridos por Render
    return StreamingResponse(sse_generator(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no"
    })

@app.post("/api/checkout")
async def process_checkout(
    request: Request, operation_code: str = Form(None), voucher_file: UploadFile = File(None)
):
    user = get_current_user(request)
    if not user: raise HTTPException(status_code=401)
    voucher_b64 = None
    if voucher_file and voucher_file.filename:
        import base64
        contents = await voucher_file.read()
        voucher_b64 = base64.b64encode(contents).decode('utf-8')
    database.create_transaction(email=user['email'], amount=15.00, operation_code=operation_code, voucher_b64=voucher_b64)
    return {"status": "success"}

