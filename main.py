import os
import json
import asyncio
import urllib.parse
from typing import List, Optional
from io import BytesIO
from PIL import Image

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

# Setup GenAI
genai.configure(api_key=os.getenv("GEMINI_API_KEYS"))
model = genai.GenerativeModel('gemini-1.5-flash')

app = FastAPI()

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
    # FIX CRÍTICO: Redirección OAuth exacta requerida
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
async def login_redirect():
    return RedirectResponse(url='/auth/login')

@app.get('/logout')
async def logout_redirect():
    return RedirectResponse(url='/auth/logout')

# --- ENDPOINTS CORE ---

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
        # FASE 1: Análisis FODA Comparativo (Inmediato - Gratis)
        prompt_foda = f"Analiza este negocio: '{input_text}' comparado frente a su competidor: '{competitor_text}'. Genera un diagnóstico estratégico rápido y un análisis FODA enfocado. Formato Markdown limpio y directo."
        contents_foda = pil_images + [prompt_foda] if pil_images else [prompt_foda]
        
        response_foda = model.generate_content(contents_foda, stream=True)
        for chunk in response_foda:
            yield f"data: {json.dumps({'type': 'foda', 'text': chunk.text})}\n\n"
            await asyncio.sleep(0.01)

        # CONTROL DE PAYWALL SERVIDOR
        if not is_admin and credits <= 0:
            yield f"data: {json.dumps({'type': 'paywall_active'})}\n\n"
            return 

        # Descontar crédito a usuarios normales
        if not is_admin:
            database.update_credits(email, credits - 1)
            yield f"data: {json.dumps({'type': 'credit_update', 'credits': credits - 1})}\n\n"

        # FASE 2: Contenido Premium (Copys)
        prompt_premium = f"Actúa como experto en marketing de alto rendimiento. Para el negocio '{input_text}', genera 3 Copys publicitarios con método AIDA diseñados para arrebatarle clientes a '{competitor_text}'. Incluye estimación de métricas referenciales (CTR, CPC). Formato Markdown atractivo."
        contents_premium = pil_images + [prompt_premium] if pil_images else [prompt_premium]
        
        response_premium = model.generate_content(contents_premium, stream=True)
        for chunk in response_premium:
            yield f"data: {json.dumps({'type': 'premium_text', 'text': chunk.text})}\n\n"
            await asyncio.sleep(0.01)
            
        # FASE 3: Generación de Placas HD en paralelo (Pollinations)
        # Se generan prompts optimizados para IA de imagen basados en el texto original
        base_prompt = urllib.parse.quote(f"Professional hyperrealistic advertising photography for {input_text[:100]}, clean background, 8k resolution, cinematic lighting")
        creative_prompt = urllib.parse.quote(f"Modern neon aesthetic banner background for {input_text[:100]}, dark mode UI, glowing accents, 4k")
        
        images = [
            f"https://pollinations.ai/p/{base_prompt}?width=1024&height=576&nologo=true",
            f"https://pollinations.ai/p/{creative_prompt}?width=1024&height=576&nologo=true"
        ]
        
        yield f"data: {json.dumps({'type': 'images', 'urls': images})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    # Anti-Buffering Headers obligatorios en Render para streaming real-time
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
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")

    voucher_b64 = None
    if voucher_file and voucher_file.filename:
        import base64
        contents = await voucher_file.read()
        voucher_b64 = base64.b64encode(contents).decode('utf-8')

    database.create_transaction(
        email=user['email'],
        amount=15.00,
        operation_code=operation_code,
        voucher_b64=voucher_b64
    )
    return {"status": "success", "message": "Validación en proceso"}
