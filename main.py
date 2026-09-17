import os
import json
import asyncio
import urllib.parse
from typing import List, Optional
from fastapi import FastAPI, Request, File, UploadFile, Form, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth
import httpx
from bs4 import BeautifulSoup
import database

# Inicializar Base de Datos
database.init_db()

app = FastAPI(title="AdVance AI Studio")

# Middleware para asegurar HTTPS en Render y manejo de cookies seguras
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
app.add_middleware(
    SessionMiddleware, 
    secret_key=os.getenv("SESSION_SECRET", "super-secret-key-fallback"),
    https_only=True,
    same_site="lax"
)

# Configuración Directorios
os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
templates = Jinja2Templates(directory="templates")

# Configuración Google OAuth
oauth = OAuth()
oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

ADMIN_EMAILS = [e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",")]
BASE_URL = os.getenv("BASE_URL", "").replace("http://", "https://").rstrip("/")

def send_evt(event: str, data: dict):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"

# ===== RUTAS OAUTH =====
@app.get("/auth/login")
async def login(request: Request):
    if not BASE_URL:
        return HTMLResponse("Error: Variable BASE_URL no configurada en Render.", 500)
    redirect_uri = f"{BASE_URL}/auth/callback"
    return await oauth.google.authorize_redirect(request, redirect_uri)

@app.get("/auth/callback")
async def auth_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
        user_info = token.get('userinfo')
        if not user_info:
            raise HTTPException(400, "No se pudo obtener información del usuario.")
        
        email = user_info.get('email').lower()
        name = user_info.get('name')
        role = "admin" if email in ADMIN_EMAILS else "user"
        
        db_user = database.create_or_update_user(email, name, role)
        request.session['user'] = db_user
        return RedirectResponse(url="/")
    except Exception as e:
        return HTMLResponse(f"Error en autenticación OAuth: {str(e)}", 400)

@app.get("/auth/logout")
async def logout(request: Request):
    request.session.pop('user', None)
    return RedirectResponse(url="/")

# ===== RUTAS FRONTEND =====
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = request.session.get('user')
    if user:
        # Refrescar datos desde DB
        user = database.get_user(user['email'])
        request.session['user'] = user
    return templates.TemplateResponse(request=request, name="index.html", context={"request": request, "user": user})

@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request):
    user = request.session.get('user')
    if not user or user.get('role') != 'admin':
        return RedirectResponse(url="/")
    txs = database.get_pending_transactions()
    return templates.TemplateResponse(request=request, name="admin.html", context={"request": request, "txs": txs, "user": user})

# ===== API DE PAGOS =====
@app.post("/api/recharge")
async def recharge(
    request: Request,
    amount: float = Form(...),
    method: str = Form(...),
    receipt: UploadFile = File(...)
):
    user = request.session.get('user')
    if not user:
        return {"success": False, "error": "No autenticado"}
    
    file_path = f"uploads/{receipt.filename}"
    with open(file_path, "wb") as f:
        f.write(await receipt.read())
        
    database.add_transaction(user['email'], amount, method, f"/{file_path}")
    return {"success": True}

@app.post("/api/approve_tx/{tx_id}")
async def approve_tx(request: Request, tx_id: int):
    user = request.session.get('user')
    if not user or user.get('role') != 'admin':
        return {"success": False, "error": "Acceso denegado"}
    success = database.approve_transaction(tx_id)
    return {"success": success}

# ===== MOTOR IA Y PAYWALL SERVER-SIDE =====
async def stream_generator(user_data, input_text: str, competitor: str):
    yield send_evt("progress", {"message": "Analizando negocio..."})
    await asyncio.sleep(0.5)

    is_admin = user_data and user_data.get('role') == 'admin'
    has_credits = user_data and user_data.get('credits', 0) > 0

    # 1. PARTE GRATUITA (FODA y Resumen)
    foda_data = {
        "fortaleza": "Propuesta de valor clara adaptada al mercado local.",
        "oportunidad": "Alta demanda en canales digitales sin explotar.",
        "debilidad": "Falta de optimización en llamados a la acción (CTA).",
        "amenaza": "Saturación de ofertas similares en competencia directa."
    }
    yield send_evt("foda", foda_data)
    await asyncio.sleep(0.5)

    # 2. PAYWALL SERVER-SIDE
    if not is_admin and not has_credits:
        yield send_evt("paywall", {"message": "Créditos insuficientes. Recarga para desbloquear carruseles HD y métricas."})
        return
    
    # 3. PARTE PREMIUM (Carruseles, Copy AIDA, Métricas)
    yield send_evt("progress", {"message": "Generando Carrusel y Copy AIDA..."})
    await asyncio.sleep(1.0)
    
    encoded_query = urllib.parse.quote(input_text[:30] if input_text else "product")
    premium_data = {
        "aida_copy": "🔥 ¡Atención! Descubre la solución definitiva.\n💡 El método probado por expertos.\n✨ Empieza hoy mismo.\n👉 Haz clic en el enlace.",
        "metrics": {"ctr": "4.5%", "cpc": "S/ 0.85", "roi": "210%"},
        "images": [
            f"https://image.pollinations.ai/prompt/commercial%20{encoded_query}%20hook%20shot?width=1080&height=1080&nologo=true",
            f"https://image.pollinations.ai/prompt/commercial%20{encoded_query}%20details?width=1080&height=1080&nologo=true",
            f"https://image.pollinations.ai/prompt/commercial%20{encoded_query}%20premium?width=1080&height=1080&nologo=true"
        ]
    }
    
    yield send_evt("premium_content", premium_data)
    
    # Consumir crédito si no es admin
    if not is_admin:
        database.deduct_credit(user_data['email'])
        
    yield send_evt("complete", {"message": "Análisis finalizado"})

@app.post("/api/generate")
async def generate_campaign(
    request: Request,
    business_input: str = Form(...),
    competitor: Optional[str] = Form(""),
    product_photos: List[UploadFile] = File(None)
):
    user = request.session.get('user')
    return StreamingResponse(
        stream_generator(user, business_input, competitor),
        media_type="text/event-stream"
    )
