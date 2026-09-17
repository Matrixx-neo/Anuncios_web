import os
import json
import asyncio
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

# Variables de Entorno y Configuración
BASE_URL = os.getenv("BASE_URL", "https://anuncios-web-c4bv.onrender.com").rstrip("/")
ADMIN_EMAILS = [e.strip() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()]
SESSION_SECRET = os.getenv("SESSION_SECRET", "super-secret-session-key")

# Setup GenAI
genai.configure(api_key=os.getenv("GEMINI_API_KEYS"))
model = genai.GenerativeModel('gemini-1.5-flash')

app = FastAPI()

# Middlewares críticos para OAuth detrás de un Reverse Proxy en Render
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
    # Bug Fix: Usar request=request explícitamente y context como dict
    return templates.TemplateResponse(
        request=request, 
        name="index.html", 
        context={"user": user, "is_admin": is_admin}
    )

@app.route('/login')
async def login(request: Request):
    redirect_uri = f"{BASE_URL}/auth/callback"
    return await oauth.google.authorize_redirect(request, redirect_uri)

@app.route('/auth/callback')
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

@app.route('/logout')
async def logout(request: Request):
    request.session.pop('user', None)
    return RedirectResponse(url='/')

# --- ENDPOINTS CORE ---

@app.post("/api/generate")
async def generate_content(
    request: Request,
    input_text: str = Form(...),
    files: List[UploadFile] = File(None)
):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado")

    email = user['email']
    is_admin = email in ADMIN_EMAILS
    credits = user['credits']

    # Procesar imágenes si existen
    pil_images = []
    if files:
        for file in files:
            if file.filename and file.content_type.startswith("image/"):
                contents = await file.read()
                img = Image.open(BytesIO(contents))
                pil_images.append(img)

    async def sse_generator():
        # FASE 1: Análisis FODA (Gratis para todos)
        prompt_foda = f"Realiza un análisis FODA rápido e incisivo para este negocio basado en: {input_text}. Formato Markdown limpio y conciso."
        contents_foda = pil_images + [prompt_foda] if pil_images else [prompt_foda]
        
        response_foda = model.generate_content(contents_foda, stream=True)
        for chunk in response_foda:
            yield f"data: {json.dumps({'type': 'foda', 'text': chunk.text})}\n\n"
            await asyncio.sleep(0.02)

        # CONTROL DE PAYWALL SERVIDOR
        if not is_admin and credits <= 0:
            # Emite señal para bloquear UI y detener generación real
            yield f"data: {json.dumps({'type': 'paywall_active'})}\n\n"
            return 

        # Descontar crédito
        if not is_admin:
            database.update_credits(email, credits - 1)
            yield f"data: {json.dumps({'type': 'credit_update', 'credits': credits - 1})}\n\n"

        # FASE 2: Contenido Premium (Copys, Métricas)
        prompt_premium = f"Genera: 1) 3 Copys publicitarios con método AIDA. 2) Estimación de métricas referenciales (CTR esperado, CPC, ROI). Basado en: {input_text}. Formato Markdown atractivo."
        contents_premium = pil_images + [prompt_premium] if pil_images else [prompt_premium]
        
        response_premium = model.generate_content(contents_premium, stream=True)
        for chunk in response_premium:
            yield f"data: {json.dumps({'type': 'premium', 'text': chunk.text})}\n\n"
            await asyncio.sleep(0.02)
            
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(sse_generator(), media_type="text/event-stream")

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
        amount=15.00, # Monto fijo demo
        operation_code=operation_code,
        voucher_b64=voucher_b64
    )
    return {"status": "success", "message": "Validación en proceso"}

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    user = get_current_user(request)
    if not user or user['email'] not in ADMIN_EMAILS:
        return RedirectResponse(url='/')
    
    transactions = database.get_pending_transactions()
    return templates.TemplateResponse(
        request=request, 
        name="admin.html", 
        context={"user": user, "transactions": transactions}
    )

@app.post("/admin/tx/{tx_id}/{action}")
async def resolve_transaction(request: Request, tx_id: int, action: str):
    user = get_current_user(request)
    if not user or user['email'] not in ADMIN_EMAILS:
        raise HTTPException(status_code=403)
    
    if action in ['approved', 'rejected']:
        database.update_transaction_status(tx_id, action)
    return {"status": "success"}

