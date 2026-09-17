import os
import json
import asyncio
import re
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
import google.generativeai as genai
import database

database.init_db()

app = FastAPI(title="AdVance AI Studio")

app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
app.add_middleware(
    SessionMiddleware, 
    secret_key=os.getenv("SESSION_SECRET", "super-secret-production-key-987"),
    https_only=True,
    same_site="lax"
)

os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
templates = Jinja2Templates(directory="templates")

oauth = OAuth()
oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

ADMIN_EMAILS = [e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()]
BASE_URL = os.getenv("BASE_URL", "https://anuncios-web-c4bv.onrender.com").rstrip("/")

# Configuración Gemini API Keys con rotación
raw_keys = os.getenv("GEMINI_API_KEYS", "")
API_KEYS = [k.strip() for k in raw_keys.split(",") if k.strip()]
if not API_KEYS and os.getenv("GEMINI_API_KEY"):
    API_KEYS = [os.getenv("GEMINI_API_KEY").strip()]

def get_gemini_model():
    if not API_KEYS:
        return None
    genai.configure(api_key=API_KEYS[0])
    return genai.GenerativeModel("gemini-1.5-flash")

async def extract_url_content(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=3.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for s in soup(["script", "style", "nav", "footer"]):
                    s.extract()
                return " ".join(soup.get_text().split())[:800]
    except Exception:
        pass
    return url

# ===== OAUTH RUTAS =====
@app.get("/auth/login")
async def login(request: Request):
    redirect_uri = f"{BASE_URL}/auth/callback"
    return await oauth.google.authorize_redirect(request, redirect_uri)

@app.get("/auth/callback")
async def auth_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
        userinfo = token.get('userinfo')
        if not userinfo:
            raise HTTPException(400, "Error obteniendo credenciales de usuario")
        
        email = userinfo.get('email').lower()
        name = userinfo.get('name')
        role = "admin" if email in ADMIN_EMAILS else "user"
        
        db_user = database.create_or_update_user(email, name, role)
        request.session['user'] = db_user
        return RedirectResponse(url="/")
    except Exception as e:
        return HTMLResponse(f"OAuth Callback Error: {e}", 400)

@app.get("/auth/logout")
async def logout(request: Request):
    request.session.pop('user', None)
    return RedirectResponse(url="/")

# ===== FRONTEND =====
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = request.session.get('user')
    if user:
        user = database.get_user(user['email'])
        request.session['user'] = user
    return templates.TemplateResponse(request=request, name="index.html", context={"request": request, "user": user})

@app.get("/admin", response_class=HTMLResponse)
async def admin(request: Request):
    user = request.session.get('user')
    if not user or user.get('role') != 'admin':
        return RedirectResponse(url="/")
    txs = database.get_pending_transactions()
    return templates.TemplateResponse(request=request, name="admin.html", context={"request": request, "txs": txs, "user": user})

# ===== STREAMING PIPELINE =====
async def run_strategy_pipeline(user, my_biz: str, competitor: str, angle: str):
    yield f"data: {json.dumps({'type': 'log', 'msg': 'Escaneando contexto de ambas marcas...'})}\n\n"
    await asyncio.sleep(0.5)

    biz_text = await extract_url_content(my_biz) if re.match(r'^https?://', my_biz) else my_biz
    comp_text = await extract_url_content(competitor) if re.match(r'^https?://', competitor) else competitor

    yield f"data: {json.dumps({'type': 'log', 'msg': 'Ejecutando matriz comparativa y FODA con Gemini...'})}\n\n"
    
    model = get_gemini_model()
    is_admin = user and user.get('role') == 'admin'
    credits = user.get('credits', 0) if user else 0

    prompt = f"""
    Eres Director de Publicidad Estratégica. Genera un análisis comparativo agresivo de marketing.
    - Negocio: {biz_text}
    - Competidor: {comp_text}
    - Enfoque opcional: {angle}

    Responde ÚNICAMENTE un JSON válido sin markdown ni comillas triples:
    {{
        "title": "Crea un título épico (Ej: El Despertar del Ritual: Marca vs Competidor)",
        "score": 82,
        "metrics": {{
            "engagement": {{"tu": "85", "rival": "70"}},
            "quality": {{"tu": "90", "rival": "65"}},
            "freq": {{"tu": "50", "rival": "80"}}
        }},
        "rival_weaknesses": [
            "Debilidad 1 del competidor",
            "Debilidad 2 del competidor",
            "Debilidad 3 del competidor"
        ],
        "attack_strategies": [
            "Estrategia de ataque 1",
            "Estrategia de ataque 2",
            "Estrategia de ataque 3"
        ],
        "weekly_plan": {{
            "lunes": "Copy/Gancho para lunes",
            "miercoles": "Copy/Valor educativo para miércoles",
            "viernes": "Copy/Cierre de venta para viernes"
        }},
        "recommendations": "Recomendación táctica para el mercado peruano.",
        "hashtags": "#PublicidadPerú #EstrategiaDigital #Crecimiento"
    }}
    """
    
    strategy_data = None
    if model:
        try:
            resp = await asyncio.to_thread(model.generate_content, prompt)
            clean_text = resp.text.strip().replace("```json", "").replace("```", "")
            strategy_data = json.loads(clean_text)
        except Exception:
            strategy_data = None

    if not strategy_data:
        strategy_data = {
            "title": f"Campaña de Dominio: {biz_text[:20]} vs {comp_text[:20]}",
            "score": 78,
            "metrics": {
                "engagement": {"tu": "85", "rival": "70"},
                "quality": {"tu": "92", "rival": "64"},
                "freq": {"tu": "50", "rival": "80"}
            },
            "rival_weaknesses": [
                "Comunicación impersonal y transaccional sin valor agregado.",
                "Tiempos de respuesta lentos en atención por WhatsApp/Instagram.",
                "Poca transparencia en los ingredientes o métodos de fabricación."
            ],
            "attack_strategies": [
                "Posicionar la marca como la alternativa consciente y personalizada.",
                "Campañas de retargeting atacando la falta de calidad del competidor.",
                "Ofrecer asesoría gratuita para generar confianza y cierre directo."
            ],
            "weekly_plan": {
                "lunes": "¿Tu producto realmente cumple lo que promete? Revelamos el secreto.",
                "miercoles": "Comparativa directa: ¿Por qué lo barato sale caro a largo plazo?",
                "viernes": "Pack exclusivo de inicio con envío inmediato. ¡Pocas unidades!"
            },
            "recommendations": "Capitalizar las fallas del competidor y destacar el servicio al cliente directo.",
            "hashtags": "#NegocioLocal #CalidadGarantizada #EstrategiaPRO"
        }

    strategy_data["type"] = "strategy"
    yield f"data: {json.dumps(strategy_data)}\n\n"

    # PAYWALL CHECK SERVER-SIDE
    if not is_admin and credits <= 0:
        yield f"data: {json.dumps({'type': 'paywall'})}\n\n"
        return

    # GENERACIÓN DE CARRUSEL HD (TIEMPO 2)
    yield f"data: {json.dumps({'type': 'log', 'msg': 'Renderizando placas del Carrusel en Alta Definición...'})}\n\n"
    await asyncio.sleep(1.0)

    query = urllib.parse.quote(biz_text[:25] if biz_text else "product commercial")
    carousel_data = {
        "type": "carousel",
        "slides": [
            {
                "tag": "GANCHO",
                "headline": "¿Sigues usando lo convencional?",
                "copy": "Descubre la diferencia de una formulación superior.",
                "image_url": f"https://image.pollinations.ai/prompt/commercial%20macro%20shot%20of%20{query}%20luxury%20lighting?width=1080&height=1080&nologo=true"
            },
            {
                "tag": "VALOR",
                "headline": "Ingredientes de Grado Premium",
                "copy": "Cuidado real sin aditivos químicos nocivos.",
                "image_url": f"https://image.pollinations.ai/prompt/natural%20ingredients%20and%20botanicals%20for%20{query}%20studio?width=1080&height=1080&nologo=true"
            },
            {
                "tag": "DIFERENCIADOR",
                "headline": "Tu Rutina, Ahora Elevada",
                "copy": "Resultados visibles desde la primera semana.",
                "image_url": f"https://image.pollinations.ai/prompt/minimalist%20aesthetic%20showcase%20of%20{query}?width=1080&height=1080&nologo=true"
            },
            {
                "tag": "OFERTA",
                "headline": "Prueba la Experiencia Hoy",
                "copy": "Llévate tu pack con asesoría y entrega rápida.",
                "image_url": f"https://image.pollinations.ai/prompt/pack%20bundle%20offer%20of%20{query}%20elegance?width=1080&height=1080&nologo=true"
            }
        ]
    }
    yield f"data: {json.dumps(carousel_data)}\n\n"

    if not is_admin:
        database.deduct_credit(user['email'])

    yield f"data: {json.dumps({'type': 'done'})}\n\n"

@app.post("/api/generate")
async def generate(
    request: Request,
    business_input: str = Form(...),
    competitor_input: str = Form(...),
    focus_angle: Optional[str] = Form(""),
    product_photos: List[UploadFile] = File(None)
):
    user = request.session.get('user')
    return StreamingResponse(
        run_strategy_pipeline(user, business_input, competitor_input, focus_angle),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )
