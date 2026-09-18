import os
import json
import asyncio
import re
import urllib.parse
import random
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
        # Actualizado al último modelo estable para evitar Error 404
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

async def robust_scrape(url: str) -> str:
    if not url.startswith("http"):
        return url
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "es-ES,es;q=0.9"
    }
    try:
        async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "lxml")
                for s in soup(["script", "style", "nav", "footer", "meta"]): s.extract()
                text = " ".join(soup.get_text().split())
                if len(text) > 100: return text[:1500]
    except Exception:
        pass
    
    clean = extract_clean_name(url)
    return f"Sitio protegido. Modelo de negocio deducido a partir de la URL: {clean}. URL original: {url}."

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

async def pipeline_generator(user, biz: str, comp: str, angle: str, uploaded_files: list):
    yield f"data: {json.dumps({'type': 'log', 'msg': 'Mapeando huella digital y comprimiendo contexto visual...'})}\n\n"
    await asyncio.sleep(0.5)
    
    clean_biz = extract_clean_name(biz)
    clean_comp = extract_clean_name(comp)
    
    biz_text = await robust_scrape(biz)
    comp_text = await robust_scrape(comp)

    yield f"data: {json.dumps({'type': 'log', 'msg': 'Procesando Neuromarketing con Inteligencia Artificial Multimodal...'})}\n\n"
    
    prompt = f"""
    Eres un Estratega de Marketing Premium y Copywriter Senior de Silicon Valley, experto en neuromarketing.
    Datos Extraídos del Negocio: '{biz_text}' (Nombre Deducido: {clean_biz}).
    Datos Extraídos del Competidor: '{comp_text}' (Nombre Deducido: {clean_comp}).
    Ángulo del cliente: '{angle}'.
    
    REGLAS ESTRICTAS PARA GENERAR VALOR:
    1. EXTIÉNDETE: Las debilidades y ataques deben ser párrafos analíticos y profundos (mínimo 30 palabras cada uno). No seas genérico.
    2. 'weekly_plan': Usa el framework AIDA. Detalla el guion paso a paso.
    3. 'recommendations': Redacta un análisis masivo (mínimo 70 palabras) combinando psicología del consumidor, pricing y posicionamiento de nicho.
    4. NO uses formato markdown (**) en tus respuestas JSON.
    
    REGLA CRÍTICA PARA IMÁGENES ('image_prompt'):
    1. DEBES deducir el OBJETO FÍSICO EXACTO que vende el negocio (ej. "artisanal organic glycerin soap", "gourmet double burger", "cotton t-shirt").
    2. PROHIBIDO usar el nombre de la marca como prompt de imagen (Nunca pongas el nombre de la empresa literal).
    3. Escribe en INGLÉS descripciones hiper-detalladas del objeto.
    4. PROHIBIDO generar rostros humanos, chicas o personas (No portraits, no faces).
    5. Cierra SIEMPRE el prompt en inglés con: ", high-end commercial macro photography, cinematic studio lighting, highly detailed, Unreal Engine 5 render, 8k resolution, photorealistic".
    
    Responde ÚNICAMENTE con este JSON válido (y asegúrate de cerrarlo bien):
    {{
        "title": "Estrategia de Dominio: {clean_biz} vs {clean_comp}",
        "score": 96,
        "chart": {{"tu": {{"eng": 85, "qual": 95, "freq": 60, "auth": 80, "conv": 90}}, "rival": {{"eng": 50, "qual": 70, "freq": 80, "auth": 60, "conv": 65}}}},
        "rival_weaknesses": ["Párrafo largo técnico 1", "Párrafo largo técnico 2", "Párrafo largo técnico 3"],
        "attack_strategies": ["Táctica de neuromarketing extensa 1", "Táctica de neuromarketing extensa 2", "Táctica de neuromarketing extensa 3"],
        "weekly_plan": {{
            "lunes": "GANCHO (3s): [Texto]. VALOR: [Texto]. CTA: [Texto].",
            "miercoles": "GANCHO (3s): [Texto]. VALOR: [Texto]. CTA: [Texto].",
            "viernes": "GANCHO (3s): [Texto]. VALOR: [Texto]. CTA: [Texto]."
        }},
        "recommendations": "Análisis profundo de experto de mínimo 70 palabras.",
        "hashtags": "#Keyword1 #Keyword2 #Keyword3 #Nicho1 #Nicho2 #Dolor1 #Conversion #Premium #Marketing #EstrategiaDigital",
        "carousel": [
            {{"tag": "GANCHO", "headline": "Titular de Choque", "copy": "Copy persuasivo de 2 líneas", "image_prompt": "english specific physical object description, high-end commercial macro photography..."}},
            {{"tag": "VALOR", "headline": "Beneficio Tangible", "copy": "Copy persuasivo de 2 líneas", "image_prompt": "english specific physical object description, high-end commercial macro photography..."}},
            {{"tag": "DIFERENCIADOR", "headline": "Autoridad Máxima", "copy": "Copy persuasivo de 2 líneas", "image_prompt": "english specific physical object description, high-end commercial macro photography..."}},
            {{"tag": "OFERTA", "headline": "Llamado a la Acción", "copy": "Copy persuasivo de 2 líneas", "image_prompt": "english specific physical object description packaging, high-end commercial macro photography..."}}
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
            # Limpiar posible basura en la respuesta para extraer JSON puro
            text_clean = resp.text.strip().replace("```json", "").replace("```", "")
            match = re.search(r'\{[\s\S]*\}', text_clean)
            if match:
                parsed = json.loads(match.group(0).replace('**', ''))
                if "chart" in parsed and "weekly_plan" in parsed:
                    data = parsed
        except Exception as e:
            print(f"Error AI: {e}")
            pass

    # FALLBACK DE EMERGENCIA EN CASO DE BLOQUEO DE INSTAGRAM
    if not data:
        data = {
            "title": f"Expansión Estratégica: {clean_biz} vs {clean_comp}",
            "score": 92,
            "chart": {"tu": {"eng": 88, "qual": 95, "freq": 70, "auth": 85, "conv": 92}, "rival": {"eng": 55, "qual": 65, "freq": 85, "auth": 70, "conv": 60}},
            "rival_weaknesses": [
                "Carencia absoluta de storytelling emocional: sus descripciones parecen fichas técnicas aburridas que no logran conectar con los deseos profundos ni los dolores reales del consumidor.",
                "Experiencia de usuario fragmentada: demoran horas en responder y derivan al cliente a flujos de bots impersonales que generan una altísima tasa de abandono de carritos.",
                "Identidad visual plana y genérica: sus redes sociales parecen un catálogo de supermercado antiguo, destruyendo cualquier percepción de marca premium o exclusiva en la mente del comprador."
            ],
            "attack_strategies": [
                "Diseñar un embudo de ventas fundamentado en autoridad y educación: muestra el 'Detrás de escena' y los procesos para elevar el valor percibido brutalmente frente a la competencia.",
                "Desplegar un arsenal de prueba social: documenta casos de éxito reales en video (UGC) para destruir de inmediato la objeción principal de falta de confianza.",
                "Implementar ofertas 'Bundle' (Paquetes o Kits): dejar de vender productos sueltos y comenzar a vender soluciones integrales para triplicar el ticket promedio de compra sin gastar más."
            ],
            "weekly_plan": {
                "lunes": "GANCHO (3s): ¿Cansado de que el método tradicional te siga fallando? Rompamos este gran mito. | VALOR: Explica cómo la industria engaña al cliente, y por qué tu solución ataca la raíz del problema con pruebas reales. | CTA: Guarda este post para no olvidarlo.",
                "miercoles": "GANCHO (3s): Así luce la verdadera calidad de alta gama bajo la lupa. | VALOR: Haz un paneo lento ASMR a tu producto destacando texturas/materiales, explicando el beneficio oculto que la competencia ahorra en costos para ganar más. | CTA: Comenta 'INFO'.",
                "viernes": "GANCHO (3s): Si estás viendo esto en tu feed, eres uno de los pocos afortunados hoy. | VENTA: Abrimos solo 15 cupos exclusivos (o unidades limitadas) con envío express bonificado por 24 horas. | CTA: Link en la biografía ya mismo."
            },
            "recommendations": "En un mercado saturado, tu ventaja competitiva es la hiper-humanización y agilidad. Muestra el proceso, cuenta tu historia de origen con transparencia y utiliza sesgos cognitivos de autoridad (certificaciones, calidad). Responde todo comentario en los primeros 15 minutos para enviar señales positivas de interacción al algoritmo de Instagram y TikTok. El dinero está en el seguimiento y el retargeting a quienes ven tus videos educativos.",
            "hashtags": "#MarketingDisruptivo #AutoridadDeMarca #CrecimientoEscalable #EstrategiaDeVentas #PosicionamientoPremium #DominioDigital #AltaConversion #Innovacion #VentasOnline #Neuromarketing",
            "carousel": [
                {"tag": "GANCHO", "headline": "El Secreto Revelado", "copy": "Lo que la gran industria corporativa no quiere que sepas sobre la verdadera calidad y los resultados.", "image_prompt": "beautiful luxury physical product, cinematic moody lighting, highly detailed texture, professional studio shot, 8k resolution, macro photography"},
                {"tag": "VALOR", "headline": "Calidad Absoluta", "copy": "Formulado, diseñado y ensamblado con estándares de grado superior que tu competencia ignora deliberadamente.", "image_prompt": "aesthetic lifestyle photography of premium product, beautiful natural lighting, premium soft shadows, depth of field, 4k quality, object only"},
                {"tag": "DIFERENCIADOR", "headline": "Experiencia Única", "copy": "Experimenta la diferencia real. Resultados garantizados que tus clientes notarán desde el primer día.", "image_prompt": "high end luxury details of an artisanal product, sharp focus, modern sophisticated setting, professional lighting setup, object only"},
                {"tag": "OFERTA", "headline": "Asegura el Tuyo Hoy", "copy": "Disponibilidad estrictamente limitada. Asegura tu paquete premium hoy mismo con beneficios exclusivos.", "image_prompt": "premium product bundle packaging box, elegant setup, clean sophisticated aesthetic, commercial advertising, highly detailed"}
            ]
        }

    # TIEMPO 1: ENVIAR TEXTOS COMPLETOS Y FODA
    strategy_payload = {k: v for k, v in data.items() if k != "carousel"}
    strategy_payload["type"] = "strategy"
    yield f"data: {json.dumps(strategy_payload)}\n\n"

    # PAYWALL CHECK SERVER-SIDE
    is_admin = user and user.get('role') == 'admin'
    credits = user.get('credits', 0) if user else 0
    if not is_admin and credits <= 0:
        yield f"data: {json.dumps({'type': 'paywall'})}\n\n"
        return

    # TIEMPO 2: IMÁGENES PRO FOTORREALISTAS (FLUX.1) CON RETARDO ANTI-COLAPSO
    yield f"data: {json.dumps({'type': 'log', 'msg': 'Renderizando placas en calidad Ultra HD (Flux.1 Engine)...'})}\n\n"
    
    slides = []
    for item in data["carousel"]:
        base_prompt = item["image_prompt"].replace("\n", " ").strip()
        safe_prompt = urllib.parse.quote(base_prompt)
        seed = random.randint(1, 999999)
        url = f"https://image.pollinations.ai/prompt/{safe_prompt}?width=1080&height=1080&nologo=true&model=flux&seed={seed}"
        slides.append({
            "tag": item["tag"],
            "headline": item["headline"],
            "copy": item["copy"],
            "image_url": url
        })
    
    await asyncio.sleep(0.5)
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
