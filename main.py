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

def extract_clean_name(url: str) -> str:
    if "instagram.com" in url or "facebook.com" in url or "tiktok.com" in url:
        match = re.search(r'([a-zA-Z0-9_.-]+)/?(?:\?.*)?$', url.strip('/'))
        if match: return match.group(1).replace('_', ' ').replace('-', ' ').title()
    if "http" in url:
        match = re.search(r'://(?:www\.)?([^/]+)', url)
        if match: return match.group(1).split('.')[0].title()
    return url[:25].title()

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

@app.post("/api/admin/assign")
async def admin_assign(request: Request, email: str = Form(...), credits: int = Form(...)):
    user = request.session.get('user')
    if user and user.get('role') == 'admin':
        database.assign_credits_manual(email.lower().strip(), credits)
        return {"success": True}
    return {"success": False}

def get_fallback_data(biz_name, comp_name):
    return {
        "title": f"Plan de Expansión: {biz_name} vs {comp_name}",
        "score": 92,
        "chart": {
             "tu": {"eng": 88, "qual": 95, "freq": 70, "auth": 85, "conv": 92},
             "rival": {"eng": 55, "qual": 65, "freq": 85, "auth": 70, "conv": 60}
        },
        "metrics": {"engagement": {"tu": "8.5%", "rival": "4.2%"}, "quality": {"tu": "95/100", "rival": "70/100"}, "freq": {"tu": "5x/sem", "rival": "3x/sem"}},
        "rival_weaknesses": [
            "Falta de storytelling emocional en sus descripciones de producto.",
            "Experiencia de usuario fragmentada y atención al cliente automatizada.",
            "Identidad visual inconsistente que diluye su posicionamiento premium."
        ],
        "attack_strategies": [
            "Implementar un embudo de ventas basado en la educación del cliente y prueba social.",
            "Elevar la percepción de valor mediante macro-fotografía estética.",
            "Lanzar ofertas de paquetes (Bundles) para aumentar el ticket promedio."
        ],
        "weekly_plan": {
            "lunes": "[GANCHO] Rompe el mito de tu industria. Muestra por qué el método de la competencia falla.",
            "miercoles": "[VALOR] Muestra la extrema calidad de tus materiales. Crea un carrusel educativo.",
            "viernes": "[VENTA] Lanza oferta irresistible por tiempo limitado con escasez real."
        },
        "recommendations": "Tu ventaja es la agilidad. Humaniza la marca, muestra el proceso y responde comentarios en los primeros 15 minutos para hackear el algoritmo.",
        "hashtags": "#EstrategiaPremium #CrecimientoEscalable #AltaConversion",
        "carousel": [
            {"tag": "GANCHO", "headline": "El Secreto Revelado", "copy": "Lo que la industria no quiere que sepas.", "image_prompt": f"macro commercial photography of {biz_name}, cinematic lighting, highly detailed"},
            {"tag": "VALOR", "headline": "Calidad Absoluta", "copy": "Estándares que otros ignoran.", "image_prompt": f"aesthetic lifestyle showcase of {biz_name}, natural lighting"},
            {"tag": "DIFERENCIADOR", "headline": "Experiencia Única", "copy": "Resultados que se notan.", "image_prompt": f"high end luxury details of {biz_name}, professional studio lighting"},
            {"tag": "OFERTA", "headline": "Asegura el Tuyo", "copy": "Unidades limitadas con envío express.", "image_prompt": f"premium product bundle packaging of {biz_name}, elegant setup"}
        ]
    }

async def pipeline_generator(user, biz: str, comp: str, angle: str, uploaded_files: list):
    yield f"data: {json.dumps({'type': 'log', 'msg': 'Extrayendo inteligencia de mercado...'})}\n\n"
    await asyncio.sleep(0.5)
    
    clean_biz = extract_clean_name(biz)
    clean_comp = extract_clean_name(comp)
    
    biz_text = await scrape_url(biz) if re.match(r'^https?://', biz) else biz
    comp_text = await scrape_url(comp) if re.match(r'^https?://', comp) else comp

    yield f"data: {json.dumps({'type': 'log', 'msg': 'Procesando modelo estratégico con IA Multimodal...'})}\n\n"
    
    prompt = f"""
    Eres Consultor de Marketing Premium. 
    Negocio: '{biz_text}' ({clean_biz}). Competidor: '{comp_text}' ({clean_comp}). Enfoque: '{angle}'.
    
    INSTRUCCIONES PARA 'image_prompt': 
    Describe el PRODUCTO EXACTO (ej. 'artisanal organic soap', 'juicy burger'). 
    Debes escribir el prompt en INGLÉS EXTREMADAMENTE DETALLADO.
    
    Responde ÚNICAMENTE en JSON estricto:
    {{
        "title": "Dominio: {clean_biz} vs {clean_comp}",
        "score": 89,
        "chart": {{"tu": {{"eng": 85, "qual": 95, "freq": 60, "auth": 80, "conv": 90}}, "rival": {{"eng": 50, "qual": 70, "freq": 80, "auth": 60, "conv": 65}}}},
        "metrics": {{"engagement": {{"tu": "8.5%", "rival": "4.2%"}}, "quality": {{"tu": "95/100", "rival": "70/100"}}, "freq": {{"tu": "5x/sem", "rival": "3x/sem"}}}},
        "rival_weaknesses": ["Debilidad 1", "Debilidad 2", "Debilidad 3"],
        "attack_strategies": ["Estrategia 1", "Estrategia 2", "Estrategia 3"],
        "weekly_plan": {{"lunes": "Copy Gancho", "miercoles": "Copy Valor", "viernes": "Copy Venta"}},
        "recommendations": "Recomendación experta detallada.",
        "hashtags": "#Estrategia #Premium",
        "carousel": [
            {{"tag": "GANCHO", "headline": "Titular 1", "copy": "Copy 1", "image_prompt": "english detailed prompt"}},
            {{"tag": "VALOR", "headline": "Titular 2", "copy": "Copy 2", "image_prompt": "english detailed prompt"}},
            {{"tag": "DIFERENCIADOR", "headline": "Titular 3", "copy": "Copy 3", "image_prompt": "english detailed prompt"}},
            {{"tag": "OFERTA", "headline": "Titular 4", "copy": "Copy 4", "image_prompt": "english detailed prompt"}}
        ]
    }}
    """
    
    contents = [prompt]
    for file_data in uploaded_files:
        contents.append({"mime_type": file_data["mime"], "data": file_data["bytes"]})
    
    model = get_gemini_model()
    data = None
    if model:
        try:
            resp = await asyncio.to_thread(model.generate_content, contents)
            match = re.search(r'\{[\s\S]*\}', resp.text)
            if match:
                parsed = json.loads(match.group(0))
                # Asegurar estructura básica para evitar crashes en JS
                if "chart" in parsed and "metrics" in parsed:
                    data = parsed
        except Exception:
            pass

    if not data:
        data = get_fallback_data(clean_biz, clean_comp)

    # TIEMPO 1: ENVIAR ESTRATEGIA TEXTO
    strategy_payload = {k: v for k, v in data.items() if k != "carousel"}
    strategy_payload["type"] = "strategy"
    yield f"data: {json.dumps(strategy_payload)}\n\n"

    # PAYWALL CHECK
    is_admin = user and user.get('role') == 'admin'
    credits = user.get('credits', 0) if user else 0
    if not is_admin and credits <= 0:
        yield f"data: {json.dumps({'type': 'paywall'})}\n\n"
        return

    # TIEMPO 2: IMÁGENES PRO FOTORREALISTAS HD
    yield f"data: {json.dumps({'type': 'log', 'msg': 'Renderizando placas en calidad Ultra HD (8K)...'})}\n\n"
    await asyncio.sleep(1.0)

    slides = []
    for item in data["carousel"]:
        base_prompt = item["image_prompt"].replace("\n", " ").strip()
        enhanced_prompt = f"{base_prompt}, commercial product photography, 8k resolution, highly detailed, Unreal Engine 5 render, sharp focus"
        safe_prompt = urllib.parse.quote(enhanced_prompt)
        # Se añade hd=true y tamaño 1080x1080
        url = f"https://image.pollinations.ai/prompt/{safe_prompt}?width=1080&height=1080&nologo=true&hd=true"
        slides.append({
            "tag": item["tag"],
            "headline": item["headline"],
            "copy": item["copy"],
            "image_url": url
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
