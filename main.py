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
        # Usamos flash que es rápido y soporta visión (multimodal)
        return genai.GenerativeModel("gemini-1.5-flash")
    return None

async def scrape_url(url: str):
    try:
        async with httpx.AsyncClient(timeout=4.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for s in soup(["script", "style", "nav", "footer"]): s.extract()
                return " ".join(soup.get_text().split())[:800]
    except Exception:
        pass
    return url

@app.get("/auth/login")
async def login(request: Request):
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
        return {"success": False}
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

async def pipeline_generator(user, biz: str, comp: str, angle: str, uploaded_files: list):
    yield f"data: {json.dumps({'type': 'log', 'msg': 'Extrayendo inteligencia del mercado y analizando imágenes...'})}\n\n"
    await asyncio.sleep(0.5)
    
    biz_text = await scrape_url(biz) if re.match(r'^https?://', biz) else biz
    comp_text = await scrape_url(comp) if re.match(r'^https?://', comp) else comp

    yield f"data: {json.dumps({'type': 'log', 'msg': 'Procesando Matriz FODA y copies con Inteligencia Artificial Multimodal...'})}\n\n"
    
    prompt = f"""
    Eres un Director Creativo y Estratega de Marketing de clase mundial.
    Analiza este negocio: '{biz_text}' y su competidor '{comp_text}'. Ángulo de campaña: '{angle}'.
    IMPORTANTE SOBRE IMÁGENES: Si el usuario adjuntó imágenes (ej. hamburguesas, ropa, jabones), DEBES analizar qué son y adaptar los 'image_prompt' ESTRICTAMENTE a ese tipo de producto. No generes imágenes genéricas. Escribe los 'image_prompt' en INGLÉS como descripciones hiperrealistas para una IA de generación de imágenes.

    Devuelve ÚNICAMENTE un JSON con esta estructura exacta:
    {{
        "title": "El Despertar del Ritual: TuMarca vs Competidor",
        "score": 85,
        "metrics": {{ "engagement": {{"tu": "88", "rival": "65"}}, "quality": {{"tu": "92", "rival": "70"}}, "freq": {{"tu": "60", "rival": "75"}} }},
        "rival_weaknesses": ["Debilidad 1", "Debilidad 2", "Debilidad 3"],
        "attack_strategies": ["Estrategia 1", "Estrategia 2", "Estrategia 3"],
        "weekly_plan": {{"lunes": "Copy Gancho", "miercoles": "Copy Valor", "viernes": "Copy Venta"}},
        "recommendations": "Recomendación experta",
        "hashtags": "#Estrategia #Marketing",
        "carousel": [
            {{"tag": "GANCHO", "headline": "Titular 1", "copy": "Texto 1", "image_prompt": "english prompt describing the exact product from context, high quality commercial photography..."}},
            {{"tag": "VALOR", "headline": "Titular 2", "copy": "Texto 2", "image_prompt": "english prompt focusing on ingredients/details of the exact product..."}},
            {{"tag": "DIFERENCIADOR", "headline": "Titular 3", "copy": "Texto 3", "image_prompt": "english prompt showing the exact product in use or lifestyle..."}},
            {{"tag": "OFERTA", "headline": "Titular 4", "copy": "Texto 4", "image_prompt": "english prompt showing a premium bundle or package of the exact product..."}}
        ]
    }}
    """
    
    # Preparar el payload multimodal (Texto + Fotos en base64/bytes)
    contents = [prompt]
    for file_data in uploaded_files:
        contents.append({"mime_type": file_data["mime"], "data": file_data["bytes"]})
    
    model = get_gemini_model()
    data = None
    if model:
        try:
            resp = await asyncio.to_thread(model.generate_content, contents)
            match = re.search(r'\{.*\}', resp.text, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
        except Exception as e:
            print(f"Gemini Error: {e}")
            pass

    # Fallback ultra-seguro por si Gemini colapsa
    if not data:
        data = {
            "title": f"Campaña de Dominio: {biz_text[:15]}",
            "score": 75,
            "metrics": {"engagement": {"tu": "85", "rival": "70"}, "quality": {"tu": "90", "rival": "65"}, "freq": {"tu": "50", "rival": "60"}},
            "rival_weaknesses": ["Atención lenta al cliente", "Comunicación muy corporativa/fría", "Falta de valor educativo en sus redes"],
            "attack_strategies": ["Posicionamiento como alternativa premium y cercana", "Educar al cliente sobre los beneficios técnicos", "Asesoría gratuita 1a1 para cerrar ventas"],
            "weekly_plan": {"lunes": "¿Cansado de lo de siempre? Descubre la diferencia.", "miercoles": "Duelo de ingredientes: Por qué lo natural siempre gana.", "viernes": "Últimas unidades de nuestro pack de inicio."},
            "recommendations": "Mantén una estética oscura y minimalista para transmitir autoridad.",
            "hashtags": "#EstrategiaDigital #Crecimiento #Premium",
            "carousel": [
                {"tag": "GANCHO", "headline": "¿Buscas algo mejor?", "copy": "Descubre la diferencia.", "image_prompt": f"macro commercial photography of {biz_text[:15]} highly detailed"},
                {"tag": "VALOR", "headline": "Máxima Calidad", "copy": "Hecho para destacar.", "image_prompt": f"aesthetic lifestyle showcase of {biz_text[:15]} natural lighting"},
                {"tag": "DIFERENCIADOR", "headline": "Único en su tipo", "copy": "Resultados garantizados.", "image_prompt": f"high end luxury details of {biz_text[:15]} 8k resolution"},
                {"tag": "OFERTA", "headline": "Llévalo Hoy", "copy": "Unidades limitadas.", "image_prompt": f"product bundle offer packaging of {biz_text[:15]} clean background"}
            ]
        }

    # TIEMPO 1: Enviar datos de texto GRATIS al frontend
    strategy_payload = {k: v for k, v in data.items() if k != "carousel"}
    strategy_payload["type"] = "strategy"
    yield f"data: {json.dumps(strategy_payload)}\n\n"

    # PAYWALL CHECK SERVER-SIDE
    is_admin = user and user.get('role') == 'admin'
    credits = user.get('credits', 0) if user else 0
    
    if not is_admin and credits <= 0:
        yield f"data: {json.dumps({'type': 'paywall'})}\n\n"
        return

    # TIEMPO 2: Generar Imágenes Pro (Polinations)
    yield f"data: {json.dumps({'type': 'log', 'msg': 'Generando placas publicitarias en HD a partir del contexto...'})}\n\n"
    await asyncio.sleep(1.0)

    slides = []
    for item in data["carousel"]:
        img_prompt = urllib.parse.quote(item["image_prompt"])
        slides.append({
            "tag": item["tag"],
            "headline": item["headline"],
            "copy": item["copy"],
            "image_url": f"https://image.pollinations.ai/prompt/{img_prompt}?width=1080&height=1080&nologo=true&hd=true"
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
    # Procesar imágenes subidas para enviarlas al LLM Multimodal
    uploaded_files = []
    if product_photos:
        for p in product_photos:
            if p.filename:
                file_bytes = await p.read()
                if file_bytes:
                    uploaded_files.append({"mime": p.content_type, "bytes": file_bytes})
                    
    return StreamingResponse(
        pipeline_generator(request.session.get('user'), business_input, competitor_input, focus_angle, uploaded_files),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
    )
