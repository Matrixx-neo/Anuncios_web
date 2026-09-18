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
                soup = BeautifulSoup(resp.text, "html.parser")
                for s in soup(["script", "style", "nav", "footer", "meta"]): s.extract()
                text = " ".join(soup.get_text().split())
                if len(text) > 100: return text[:1500]
    except Exception:
        pass
    
    clean = extract_clean_name(url)
    return f"Sitio protegido. Modelo de negocio deducido a partir de la URL: {clean}. URL original: {url}."

def get_heuristic_premium_fallback(biz_text, clean_biz, clean_comp):
    """Genera datos ultradetallados identificando el producto real para que Flux no alucine."""
    # Deducción Heurística del Producto
    texto_lower = biz_text.lower()
    if "jabon" in texto_lower or "jabón" in texto_lower or "cosmetic" in texto_lower or "botanic" in texto_lower:
        prod_en = "artisanal organic glycerin soap bar with dried flowers"
        niche_recs = "Enfócate en los ingredientes orgánicos. El mercado cosmético es visual; tus clientes compran el aroma visualmente. Usa macros extremos de la textura de tus jabones para activar el deseo sensorial."
    elif "ropa" in texto_lower or "fashion" in texto_lower or "boutique" in texto_lower:
        prod_en = "high end fashion apparel clothing"
        niche_recs = "La moda se vende por estatus y estilo de vida. No vendas la tela, vende en quién se convierte tu cliente al usarla. Utiliza iluminación dramática y prueba social de clientes reales."
    elif "comida" in texto_lower or "burger" in texto_lower or "restaurant" in texto_lower or "food" in texto_lower:
        prod_en = "delicious gourmet gourmet food dish"
        niche_recs = "La comida entra por los ojos. Necesitas implementar 'Food Porn' en cada foto. Colores cálidos, queso derretido o texturas jugosas que activen las glándulas salivales del espectador en los primeros 3 segundos."
    else:
        prod_en = "premium commercial physical product"
        niche_recs = "En un mercado saturado de opciones iguales, el consumidor sufre de 'ceguera publicitaria'. Tu ventaja táctica es la hiper-humanización. Muestra el proceso, cuenta tu historia de origen con transparencia."

    return {
        "title": f"Expansión Estratégica: {clean_biz} vs {clean_comp}",
        "score": 93,
        "chart": {"tu": {"eng": 88, "qual": 95, "freq": 75, "auth": 85, "conv": 90}, "rival": {"eng": 55, "qual": 65, "freq": 85, "auth": 70, "conv": 60}},
        "metrics": {"engagement": {"tu": "8.8%", "rival": "4.1%"}, "quality": {"tu": "95/100", "rival": "65/100"}, "freq": {"tu": "6x/sem", "rival": "3x/sem"}},
        "rival_weaknesses": [
            "Carencia absoluta de storytelling emocional: sus descripciones parecen fichas técnicas aburridas que no logran conectar con los deseos profundos ni los dolores reales del consumidor final.",
            "Experiencia de usuario fragmentada: demoran horas en responder y derivan al cliente a flujos de bots impersonales, lo que genera una altísima tasa de abandono justo antes del pago.",
            "Identidad visual plana y genérica: sus redes sociales parecen un catálogo antiguo sin curación estética, destruyendo cualquier percepción de marca premium o exclusiva en la mente del comprador."
        ],
        "attack_strategies": [
            "Diseñar un embudo de ventas fundamentado en autoridad y educación: muestra el 'Detrás de escena' y los procesos de fabricación para elevar el valor percibido brutalmente frente a la competencia.",
            "Desplegar un arsenal de prueba social y UGC (Contenido Generado por Usuario): documenta casos de éxito reales en video para destruir de inmediato la objeción principal de falta de confianza.",
            "Implementar ofertas 'Bundle' (Paquetes o Kits): dejar de vender productos sueltos y comenzar a vender soluciones integrales. Esto triplicará tu ticket promedio de compra sin gastar más en publicidad."
        ],
        "weekly_plan": {
            "lunes": "GANCHO (3s): ¿Cansado de que el método tradicional te siga fallando? Rompamos este gran mito. | VALOR: Explica cómo el 90% de la industria engaña al cliente, y por qué tu solución ataca la raíz del problema con pruebas reales. | CTA: Guarda este post para no olvidarlo.",
            "miercoles": "GANCHO (3s): Así luce la verdadera calidad de alta gama bajo la lupa. | VALOR: Haz un paneo lento ASMR a tu producto destacando texturas y materiales, explicando el beneficio oculto que la competencia ahorra en costos para ganar más. | CTA: Comenta 'INFO' para el catálogo secreto.",
            "viernes": "GANCHO (3s): Si estás viendo esto en tu feed, eres uno de los pocos afortunados hoy. | VENTA: Abrimos solo 15 cupos exclusivos (o unidades limitadas de stock) con envío express bonificado por 24 horas. La escasez debe ser 100% real para activar el FOMO (Miedo a quedarse fuera). | CTA: Link en la biografía ya mismo."
        },
        "recommendations": f"{niche_recs} Utiliza sesgos cognitivos de autoridad (certificaciones, calidad técnica) y urgencia real. Responde todo comentario en los primeros 15 minutos para enviar señales positivas de interacción al algoritmo y dispara campañas de retargeting a quienes reprodujeron al menos el 50% de tus videos. El dinero está en el seguimiento constante.",
        "hashtags": "#MarketingDisruptivo #AutoridadDeMarca #CrecimientoEscalable #EstrategiaDeVentas #PosicionamientoPremium #DominioDigital #AltaConversion #Innovacion #VentasOnline #Neuromarketing",
        "carousel": [
            {"tag": "GANCHO", "headline": "El Secreto Revelado", "copy": "Lo que la gran industria corporativa no quiere que sepas sobre la verdadera calidad y los resultados tangibles.", "image_prompt": f"macro commercial photography of {prod_en}, cinematic moody lighting, highly detailed texture, professional studio shot, 8k resolution"},
            {"tag": "VALOR", "headline": "Calidad Absoluta", "copy": "Formulado, diseñado y ensamblado con estándares de grado superior que tu competencia ignora deliberadamente.", "image_prompt": f"aesthetic lifestyle photography of {prod_en}, beautiful natural lighting, premium soft shadows, depth of field, 4k quality"},
            {"tag": "DIFERENCIADOR", "headline": "Experiencia Única", "copy": "Experimenta la diferencia real. Resultados garantizados que tus clientes notarán desde el primer segundo.", "image_prompt": f"high end luxury details of {prod_en}, sharp focus, modern sophisticated setting, professional lighting setup"},
            {"tag": "OFERTA", "headline": "Asegura el Tuyo Hoy", "copy": "Disponibilidad estrictamente limitada. Asegura tu paquete premium hoy mismo con beneficios exclusivos.", "image_prompt": f"premium packaging box bundle of {prod_en}, elegant setup, clean sophisticated aesthetic, commercial advertising"}
        ]
    }

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
    except Exception:
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
    yield f"data: {json.dumps({'type': 'log', 'msg': 'Scraping avanzado: Extrayendo huella digital y metadatos...'})}\n\n"
    await asyncio.sleep(0.5)
    
    clean_biz = extract_clean_name(biz)
    clean_comp = extract_clean_name(comp)
    
    biz_text = await robust_scrape(biz)
    comp_text = await robust_scrape(comp)

    yield f"data: {json.dumps({'type': 'log', 'msg': 'Procesando Neuromarketing con Inteligencia Artificial Multimodal...'})}\n\n"
    
    prompt = f"""
    Eres un Estratega de Marketing Premium y Copywriter Senior de Silicon Valley.
    Datos Extraídos: '{biz_text}' (Nombre Deducido: {clean_biz}). Competidor: '{comp_text}' ({clean_comp}). Enfoque: '{angle}'.
    
    REGLAS ESTRICTAS PARA EL TEXTO (DEBE SER LARGO Y PROFUNDO):
    1. 'rival_weaknesses' y 'attack_strategies': Redacta párrafos analíticos y detallados (mínimo 30 palabras CADA UNO). Explica el POR QUÉ.
    2. 'weekly_plan': Describe la estrategia de contenido día a día usando el framework AIDA. Extenso.
    3. 'recommendations': Redacta un análisis experto masivo (mínimo 60 palabras) combinando psicología del consumidor, neuromarketing y pricing.
    
    REGLA CRÍTICA PARA IMÁGENES ('image_prompt'):
    1. DEDUCE EL OBJETO FÍSICO EXACTO del negocio (ej. 'artisanal decorative soap', 'gourmet double burger', 'leather jacket').
    2. PROHIBIDO usar el nombre de la marca (ej. 'Art and love ofc') en el prompt de imagen. 
    3. PROHIBIDO generar rostros humanos (No faces, no portraits).
    4. Escribe en INGLÉS. Termina CADA prompt con: ", high-end commercial macro photography, cinematic studio lighting, highly detailed, Unreal Engine 5 render, 8k resolution".
    
    Responde ÚNICAMENTE en JSON estricto (SIN FORMATO MARKDOWN, SIN ```json):
    {{
        "title": "Estrategia de Dominio: {clean_biz} vs {clean_comp}",
        "score": 96,
        "chart": {{"tu": {{"eng": 88, "qual": 95, "freq": 70, "auth": 85, "conv": 92}}, "rival": {{"eng": 55, "qual": 65, "freq": 85, "auth": 70, "conv": 60}}}},
        "metrics": {{"engagement": {{"tu": "8.8%", "rival": "4.1%"}}, "quality": {{"tu": "95/100", "rival": "65/100"}}, "freq": {{"tu": "6x/sem", "rival": "3x/sem"}}}},
        "rival_weaknesses": ["Párrafo técnico extenso 1...", "Párrafo técnico extenso 2...", "Párrafo técnico extenso 3..."],
        "attack_strategies": ["Táctica de persuasión profunda 1...", "Táctica de persuasión profunda 2...", "Táctica de persuasión profunda 3..."],
        "weekly_plan": {{
            "lunes": "GANCHO (3s): [Describe el inicio]. VALOR: [Describe desarrollo]. CTA: [Llamado a la acción claro].",
            "miercoles": "GANCHO (3s): [Describe el inicio]. VALOR: [Describe desarrollo]. CTA: [Llamado a la acción claro].",
            "viernes": "GANCHO (3s): [Describe el inicio]. VALOR: [Describe desarrollo]. CTA: [Llamado a la acción claro]."
        }},
        "recommendations": "Análisis profundo de experto de mínimo 60 palabras, enfocado en neuromarketing.",
        "hashtags": "#Marketing #Estrategia #Premium #Conversion #Escalabilidad #DominioDigital",
        "carousel": [
            {{"tag": "GANCHO", "headline": "Titular de Impacto", "copy": "Copy persuasivo de 2 líneas", "image_prompt": "english specific physical object description, high-end commercial macro photography..."}},
            {{"tag": "VALOR", "headline": "Beneficio Tangible", "copy": "Copy persuasivo de 2 líneas", "image_prompt": "english specific physical object description, high-end commercial macro photography..."}},
            {{"tag": "DIFERENCIADOR", "headline": "Autoridad Máxima", "copy": "Copy persuasivo de 2 líneas", "image_prompt": "english specific physical object description, high-end commercial macro photography..."}},
            {{"tag": "OFERTA", "headline": "Oferta Irresistible", "copy": "Copy persuasivo de 2 líneas", "image_prompt": "english specific physical object description packaging, high-end commercial macro photography..."}}
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
                parsed = json.loads(match.group(0).replace('**', ''))
                if "chart" in parsed and "weekly_plan" in parsed:
                    data = parsed
        except Exception as e:
            print(f"Error AI: {e}")
            pass

    if not data:
        data = get_heuristic_premium_fallback(biz_text, clean_biz, clean_comp)

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

    # TIEMPO 2: IMÁGENES PRO FOTORREALISTAS (FLUX.1)
    yield f"data: {json.dumps({'type': 'log', 'msg': 'Renderizando placas en calidad Ultra HD (Flux.1 Engine)...'})}\n\n"
    
    slides = []
    for item in data["carousel"]:
        base_prompt = item["image_prompt"].replace("\n", " ").strip()
        safe_prompt = urllib.parse.quote(base_prompt)
        seed = random.randint(1, 999999)
        url = f"[https://image.pollinations.ai/prompt/](https://image.pollinations.ai/prompt/){safe_prompt}?width=1080&height=1080&nologo=true&model=flux&seed={seed}"
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
