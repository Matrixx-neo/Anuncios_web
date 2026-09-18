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
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
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

@app.post("/api/admin/assign")
async def admin_assign(request: Request, email: str = Form(...), credits: int = Form(...)):
    user = request.session.get('user')
    if user and user.get('role') == 'admin':
        database.assign_credits_manual(email.lower().strip(), credits)
        return {"success": True}
    return {"success": False}

async def pipeline_generator(user, biz: str, comp: str, angle: str, uploaded_files: list):
    yield f"data: {json.dumps({'type': 'log', 'msg': 'Extrayendo inteligencia de mercado y procesando contexto...'})}\n\n"
    await asyncio.sleep(0.5)
    
    biz_text = await scrape_url(biz) if re.match(r'^https?://', biz) else biz
    comp_text = await scrape_url(comp) if re.match(r'^https?://', comp) else comp

    yield f"data: {json.dumps({'type': 'log', 'msg': 'Procesando modelo estratégico avanzado con Gemini...'})}\n\n"
    
    prompt = f"""
    Eres un Consultor y Copywriter de Alto Nivel. Redacta un análisis premium extremadamente detallado, persuasivo y extenso.
    Negocio del cliente: '{biz_text}'. Competidor: '{comp_text}'. Enfoque: '{angle}'.
    
    IMPORTANTE PARA 'image_prompt': Escribe los prompts en INGLÉS detallado. Si el cliente subió fotos, o menciona productos físicos (ej. comida, ropa, jabones), el prompt debe describir EXACTAMENTE ese producto usando términos como "high-end commercial macro photography of [producto exacto], cinematic studio lighting, 8k resolution". No uses textos genéricos.
    
    Responde ÚNICAMENTE en este JSON estricto:
    {{
        "title": "Campaña de Dominio: [Nombre] vs El Mercado",
        "score": 89,
        "metrics": {{ "engagement": {{"tu": "8.5%", "rival": "4.2%"}}, "quality": {{"tu": "95/100", "rival": "70/100"}}, "freq": {{"tu": "5x/sem", "rival": "3x/sem"}} }},
        "rival_weaknesses": ["Debilidad extensa y detallada 1", "Debilidad extensa y detallada 2", "Debilidad extensa y detallada 3"],
        "attack_strategies": ["Estrategia táctica profunda 1", "Estrategia táctica profunda 2", "Estrategia táctica profunda 3"],
        "weekly_plan": {{
            "lunes": "Escribe 3-4 líneas detalladas con el enfoque del gancho para el lunes.",
            "miercoles": "Escribe 3-4 líneas detalladas sobre el contenido de valor educativo para el miércoles.",
            "viernes": "Escribe 3-4 líneas detalladas con el copy de venta agresiva y escasez para el viernes."
        }},
        "recommendations": "Redacta un párrafo extenso (5-6 líneas) con consejos expertos de marketing, neuromarketing y posicionamiento de marca.",
        "hashtags": "#Marketing #Estrategia #Escalabilidad #Premium",
        "carousel": [
            {{"tag": "GANCHO", "headline": "Titular de alto impacto", "copy": "Copy persuasivo de 2 líneas", "image_prompt": "english detailed prompt of the exact product"}},
            {{"tag": "VALOR", "headline": "Titular de beneficio", "copy": "Copy persuasivo de 2 líneas", "image_prompt": "english detailed prompt showing ingredients or texture of the product"}},
            {{"tag": "DIFERENCIADOR", "headline": "Titular de autoridad", "copy": "Copy persuasivo de 2 líneas", "image_prompt": "english detailed prompt showing the product in lifestyle use"}},
            {{"tag": "OFERTA", "headline": "Llamado a la acción", "copy": "Copy persuasivo de cierre", "image_prompt": "english detailed prompt showing a premium bundle packaging of the product"}}
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
            match = re.search(r'\{.*\}', resp.text, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
        except Exception as e:
            print(f"Error IA: {e}")
            pass

    # FALLBACK PREMIUM ULTRADETALLADO (Se usa si Gemini demora o falla)
    if not data:
        biz_name = biz_text[:20] if len(biz_text) > 5 else "Tu Producto"
        data = {
            "title": f"Plan de Expansión: {biz_name} vs La Competencia",
            "score": 92,
            "metrics": {"engagement": {"tu": "8.5%", "rival": "4.2%"}, "quality": {"tu": "95/100", "rival": "70/100"}, "freq": {"tu": "5x/sem", "rival": "3x/sem"}},
            "rival_weaknesses": [
                "Falta de storytelling emocional en sus descripciones de producto, centrándose exclusivamente en características técnicas que aburren al consumidor.",
                "Experiencia de usuario fragmentada y una atención al cliente automatizada que genera fricción y abandono de carritos.",
                "Identidad visual inconsistente que diluye su posicionamiento premium en redes sociales, compitiendo solo por precio."
            ],
            "attack_strategies": [
                "Implementar un embudo de ventas basado en la educación del cliente, demostrando autoridad y construyendo prueba social acelerada.",
                "Elevar la percepción de valor de tu marca mediante macro-fotografía y empaques estéticos que el rival no posee en su catálogo.",
                "Lanzar ofertas de 'Bundle' (Paquetes) para aumentar radicalmente el ticket promedio de compra y absorber el costo de adquisición de clientes."
            ],
            "weekly_plan": {
                "lunes": "[EL GANCHO] Rompe el mito principal de tu industria. Muestra en un reel corto de 7 segundos por qué el método o producto tradicional que usa tu competencia falla a largo plazo. Usa un audio en tendencia y texto grande.",
                "miercoles": "[EL VALOR] Detrás de escena: Muestra la extrema calidad de tus materiales o ingredientes. Crea un carrusel educativo que justifique el valor de tu oferta, respondiendo a la objeción de precio antes de que el cliente la piense.",
                "viernes": "[LA VENTA DIRECTA] Lanza una oferta irresistible por tiempo limitado con escasez real (solo 10 unidades o válido por 24 horas) y un llamado a la acción claro, dirigiéndolos hacia tu WhatsApp o tienda web."
            },
            "recommendations": "Tu principal ventaja competitiva en este momento es la agilidad y el servicio. Mientras tu competidor mantiene una comunicación fría e institucional, tú debes humanizar la marca. Muestra el proceso, cuenta tu historia de origen con transparencia y asegúrate de responder a todos los comentarios en los primeros 15 minutos de publicación para maximizar el empuje del algoritmo en Instagram y TikTok. El mercado valora a las marcas auténticas.",
            "hashtags": "#EstrategiaPremium #CrecimientoEscalable #DominioDeMercado #AltaConversion #MarcasConProposito",
            "carousel": [
                {"tag": "GANCHO", "headline": "El Secreto Revelado", "copy": "Lo que la industria tradicional no quiere que sepas sobre la verdadera calidad.", "image_prompt": f"macro commercial photography of {biz_name}, cinematic lighting, highly detailed, 8k resolution, elegant dark background"},
                {"tag": "VALOR", "headline": "Calidad Absoluta", "copy": "Formulado y diseñado con estándares de grado superior que otros ignoran.", "image_prompt": f"aesthetic lifestyle showcase of {biz_name}, natural lighting, premium soft shadows, 4k"},
                {"tag": "DIFERENCIADOR", "headline": "Experiencia Única", "copy": "Resultados tangibles que tus clientes notarán desde el primer día de uso.", "image_prompt": f"high end luxury details of {biz_name}, sharp focus, professional studio lighting, depth of field"},
                {"tag": "OFERTA", "headline": "Asegura el Tuyo", "copy": "Unidades estrictamente limitadas con envío express garantizado para hoy.", "image_prompt": f"premium product bundle packaging of {biz_name}, elegant setup, clean sophisticated aesthetic"}
            ]
        }

    # TIEMPO 1: ENVIAR TEXTO
    strategy_payload = {k: v for k, v in data.items() if k != "carousel"}
    strategy_payload["type"] = "strategy"
    yield f"data: {json.dumps(strategy_payload)}\n\n"

    # PAYWALL CHECK
    is_admin = user and user.get('role') == 'admin'
    credits = user.get('credits', 0) if user else 0
    if not is_admin and credits <= 0:
        yield f"data: {json.dumps({'type': 'paywall'})}\n\n"
        return

    # TIEMPO 2: IMÁGENES PRO
    yield f"data: {json.dumps({'type': 'log', 'msg': 'Renderizando placas fotográficas ultra HD basadas en tu negocio...'})}\n\n"
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
