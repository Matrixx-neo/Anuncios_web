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
    secret_key=os.getenv("SESSION_SECRET", "super-secret-key-advance-2026"),
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

def get_gemini_model():
    keys = [k.strip() for k in os.getenv("GEMINI_API_KEYS", "").split(",") if k.strip()]
    if not keys and os.getenv("GEMINI_API_KEY"):
        keys = [os.getenv("GEMINI_API_KEY").strip()]
    if keys:
        genai.configure(api_key=keys[0])
        return genai.GenerativeModel("gemini-1.5-flash")
    return None

async def scrape_url(url: str):
    try:
        async with httpx.AsyncClient(timeout=4.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for s in soup(["script", "style", "nav", "footer"]): s.extract()
                return " ".join(soup.get_text().split())[:600]
    except Exception:
        pass
    return url

@app.get("/auth/login")
async def login(request: Request):
    # SOLUCIÓN QUIRÚRGICA AL ERROR 400: URL HARDCODEADA
    redirect_uri = "https://anuncios-web-c4bv.onrender.com/auth/callback"
    return await oauth.google.authorize_redirect(request, redirect_uri)

@app.get("/auth/callback")
async def auth_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
        user = token.get('userinfo')
        email = user.get('email').lower()
        role = "admin" if email in ADMIN_EMAILS else "user"
        db_user = database.create_or_update_user(email, user.get('name'), role)
        request.session['user'] = db_user
        return RedirectResponse(url="/")
    except Exception as e:
        print(f"OAuth Error: {e}")
        return RedirectResponse(url="/")

@app.get("/auth/logout")
async def logout(request: Request):
    request.session.pop('user', None)
    return RedirectResponse(url="/")

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
    return templates.TemplateResponse(request=request, name="admin.html", context={"request": request, "txs": database.get_pending_transactions()})

@app.post("/api/recharge")
async def recharge(request: Request, receipt: UploadFile = File(...)):
    user = request.session.get('user')
    if not user:
        return {"success": False, "error": "No has iniciado sesión"}
    path = f"uploads/{receipt.filename}"
    with open(path, "wb") as f:
        f.write(await receipt.read())
    database.add_transaction(user['email'], f"/{path}")
    return {"success": True}

@app.post("/api/approve/{tx_id}")
async def approve(request: Request, tx_id: int, credits: int = Form(...)):
    user = request.session.get('user')
    if user and user.get('role') == 'admin':
        database.approve_transaction(tx_id, credits)
        return {"success": True}
    return {"success": False}

async def pipeline_generator(user, biz: str, comp: str, angle: str, photos_count: int):
    yield f"data: {json.dumps({'type': 'log', 'msg': 'Extrayendo inteligencia de mercado...'})}\n\n"
    await asyncio.sleep(0.5)
    
    biz_text = await scrape_url(biz) if re.match(r'^https?://', biz) else biz
    comp_text = await scrape_url(comp) if re.match(r'^https?://', comp) else comp

    yield f"data: {json.dumps({'type': 'log', 'msg': 'Generando Matriz Comparativa con Inteligencia Artificial...'})}\n\n"
    
    # Prompt de Ingeniería para Gemini (Garantiza imágenes contextuales)
    prompt = f"""
    Eres Director de Publicidad Estratégica. Genera un análisis agresivo.
    - Negocio: {biz_text}
    - Competidor: {comp_text}
    - Enfoque opcional: {angle}
    - El usuario subió {photos_count} fotos de referencia (toma esto en cuenta).

    Retorna SOLO un JSON válido con esta estructura estricta:
    {{
        "title": "Campaña: Nombre Épico",
        "score": 85,
        "metrics": {{ "engagement": {{"tu": "88", "rival": "65"}}, "quality": {{"tu": "92", "rival": "70"}}, "freq": {{"tu": "60", "rival": "75"}} }},
        "rival_weaknesses": ["Debilidad 1", "Debilidad 2", "Debilidad 3"],
        "attack_strategies": ["Ataque 1", "Ataque 2", "Ataque 3"],
        "weekly_plan": {{"lunes": "Copy Lunes", "miercoles": "Copy Miercoles", "viernes": "Copy Viernes"}},
        "recommendations": "Recomendación experta",
        "hashtags": "#Hashtag1 #Hashtag2",
        "carousel": [
            {{"tag": "GANCHO", "headline": "Titular corto 1", "copy": "Subtítulo 1", "image_prompt": "Prompt en INGLÉS para generar una foto fotorrealista comercial y profesional de alta calidad sobre este negocio exacto (ej. macro shot of artisanal soap / delicious juicy burger...)"}},
            {{"tag": "VALOR", "headline": "Titular corto 2", "copy": "Subtítulo 2", "image_prompt": "Prompt en INGLÉS detallando los beneficios visuales del producto del usuario"}},
            {{"tag": "DIFERENCIADOR", "headline": "Titular corto 3", "copy": "Subtítulo 3", "image_prompt": "Prompt en INGLÉS mostrando el producto del usuario en uso o su textura"}},
            {{"tag": "OFERTA", "headline": "Titular corto 4", "copy": "Subtítulo 4", "image_prompt": "Prompt en INGLÉS de un paquete o empaque premium del producto del usuario"}}
        ]
    }}
    """
    
    model = get_gemini_model()
    data = None
    if model:
        try:
            resp = await asyncio.to_thread(model.generate_content, prompt)
            clean = resp.text.strip().replace("```json", "").replace("```", "")
            data = json.loads(clean)
        except Exception as e:
            print(f"Gemini Error: {e}")
            pass

    # Fallback de seguridad por si falla la API
    if not data:
        data = {
            "title": f"Dominio de Mercado: {biz_text[:15]}",
            "score": 85,
            "metrics": {"engagement": {"tu": "88", "rival": "65"}, "quality": {"tu": "92", "rival": "70"}, "freq": {"tu": "60", "rival": "75"}},
            "rival_weaknesses": ["Atención lenta", "Calidad visual estándar", "Falta de valor agregado"],
            "attack_strategies": ["Posicionar como premium", "Educar al cliente", "Asesoría 1 a 1"],
            "weekly_plan": {"lunes": "Beneficio clave", "miercoles": "Testimonio", "viernes": "Cierre con escasez"},
            "recommendations": "Usa colores contrastantes y copies directos atacando la fricción de compra.",
            "hashtags": "#EstrategiaPremium #Crecimiento",
            "carousel": [
                {"tag": "GANCHO", "headline": "¿Buscas algo mejor?", "copy": "Descubre la diferencia.", "image_prompt": f"macro commercial photography of {biz_text[:20]} premium quality"},
                {"tag": "VALOR", "headline": "Máxima Calidad", "copy": "Hecho para destacar.", "image_prompt": f"aesthetic lifestyle showcase of {biz_text[:20]} natural lighting"},
                {"tag": "DIFERENCIADOR", "headline": "Único en su tipo", "copy": "Resultados garantizados.", "image_prompt": f"high end luxury details of {biz_text[:20]} 8k resolution"},
                {"tag": "OFERTA", "headline": "Llévalo Hoy", "copy": "Unidades limitadas.", "image_prompt": f"product bundle offer packaging of {biz_text[:20]} clean background"}
            ]
        }

    # TIEMPO 1: Enviar datos de texto GRATIS
    strategy_payload = {k: v for k, v in data.items() if k != "carousel"}
    strategy_payload["type"] = "strategy"
    yield f"data: {json.dumps(strategy_payload)}\n\n"

    # PAYWALL CHECK SERVER-SIDE
    is_admin = user and user.get('role') == 'admin'
    credits = user.get('credits', 0) if user else 0
    
    if not is_admin and credits <= 0:
        yield f"data: {json.dumps({'type': 'paywall'})}\n\n"
        return

    # TIEMPO 2: Generar Imágenes Contextuales PRO
    yield f"data: {json.dumps({'type': 'log', 'msg': 'Renderizando placas fotográficas basadas en tu negocio...'})}\n\n"
    await asyncio.sleep(1.0)

    slides = []
    for item in data["carousel"]:
        img_prompt = urllib.parse.quote(item["image_prompt"])
        slides.append({
            "tag": item["tag"],
            "headline": item["headline"],
            "copy": item["copy"],
            "image_url": f"https://image.pollinations.ai/prompt/{img_prompt}?width=1080&height=1080&nologo=true"
        })
    
    yield f"data: {json.dumps({'type': 'carousel', 'slides': slides})}\n\n"

    if not is_admin and user:
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
    # Contamos las fotos subidas para darle contexto a la IA
    photos_count = len([p for p in product_photos if p.filename]) if product_photos else 0
    return StreamingResponse(
        pipeline_generator(request.session.get('user'), business_input, competitor_input, focus_angle, photos_count),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
    )
