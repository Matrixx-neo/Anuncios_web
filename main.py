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
        "chart": {"tu": {"eng": 88, "qual": 95, "freq": 70, "auth": 85, "conv": 92}, "rival": {"eng": 55, "qual": 65, "freq": 85, "auth": 70, "conv": 60}},
        "metrics": {"engagement": {"tu": "8.5%", "rival": "4.2%"}, "quality": {"tu": "95/100", "rival": "70/100"}, "freq": {"tu": "5x/sem", "rival": "3x/sem"}},
        "rival_weaknesses": [
            "Carencia absoluta de storytelling emocional: sus descripciones parecen fichas técnicas aburridas que no conectan con el deseo real del consumidor.",
            "Experiencia de usuario fragmentada: demoran horas en responder y derivan al cliente a flujos de bots que generan alta tasa de abandono de carritos.",
            "Identidad visual plana y genérica: sus redes sociales parecen un catálogo de supermercado, destruyendo cualquier percepción de marca premium o exclusiva."
        ],
        "attack_strategies": [
            "Diseñar un embudo de ventas fundamentado en autoridad y educación: muestra el 'Detrás de escena' para elevar el valor percibido brutalmente.",
            "Desplegar un arsenal de prueba social: casos de éxito documentados en video (UGC) para destruir la objeción principal de falta de confianza.",
            "Implementar ofertas 'Bundle' (Paquetes o Kits): no vendas productos sueltos, vende soluciones integrales para triplicar el ticket promedio de compra."
        ],
        "weekly_plan": {
            "lunes": "GANCHO (3s): ¿Cansado de que [Problema Principal] siga igual? Rompamos el mito. | VALOR: Explica cómo el 90% de la industria te miente, y por qué tu solución ataca la raíz del problema, no solo el síntoma. | CTA: Guarda este post.",
            "miercoles": "GANCHO (3s): Así luce la calidad de verdad bajo el microscopio. | VALOR: Haz un paneo lento ASMR a tu producto destacando texturas/materiales, explicando el beneficio oculto que la competencia ahorra en costos. | CTA: Comenta 'INFO'.",
            "viernes": "GANCHO (3s): Si estás viendo esto, eres uno de los afortunados. | VENTA: Abrimos 15 cupos exclusivos (o unidades limitadas) con envío express bonificado por las próximas 24 horas. La escasez debe ser 100% real. | CTA: Link en la biografía ya."
        },
        "recommendations": "El mercado actual sufre de 'ceguera publicitaria'. Tu ventaja táctica es la hiper-humanización. Muestra tu rostro o la producción manual, utiliza sesgos cognitivos de autoridad y urgencia real. Responde todo comentario en los primeros 15 minutos para enviar señales positivas al algoritmo y dispara campañas de retargeting a quienes reprodujeron al menos el 50% de tus videos educativos.",
        "hashtags": "#MarketingDisruptivo #AutoridadDeMarca #CrecimientoEscalable #EstrategiaDeVentas #PosicionamientoPremium #DominioDigital #AltaConversion #Innovacion",
        "carousel": [
            {"tag": "GANCHO", "headline": "El Secreto Revelado", "copy": "Lo que la industria corporativa no quiere que sepas sobre la verdadera calidad y los resultados tangibles.", "image_prompt": f"macro commercial photography of {biz_name} product, cinematic moody lighting, highly detailed texture, professional studio shot, 8k resolution"},
            {"tag": "VALOR", "headline": "Calidad Absoluta", "copy": "Formulado, diseñado y ensamblado con estándares de grado superior que la competencia ignora por completo.", "image_prompt": f"aesthetic lifestyle photography of {biz_name}, beautiful natural lighting, premium soft shadows, depth of field, 4k quality"},
            {"tag": "DIFERENCIADOR", "headline": "Experiencia Única", "copy": "Experimenta la diferencia real. Resultados garantizados que tus clientes y tú notarán desde el primer día.", "image_prompt": f"high end luxury details of {biz_name}, sharp focus, modern sophisticated setting, professional lighting setup"},
            {"tag": "OFERTA", "headline": "Asegura el Tuyo Hoy", "copy": "Disponibilidad estrictamente limitada. Asegura tu paquete hoy mismo con beneficios exclusivos y envío prioritario.", "image_prompt": f"premium product bundle packaging of {biz_name}, elegant setup, clean sophisticated aesthetic, commercial advertising"}
        ]
    }

async def pipeline_generator(user, biz: str, comp: str, angle: str, uploaded_files: list):
    yield f"data: {json.dumps({'type': 'log', 'msg': 'Mapeando huella digital y comprimiendo contexto visual...'})}\n\n"
    await asyncio.sleep(0.5)
    
    clean_biz = extract_clean_name(biz)
    clean_comp = extract_clean_name(comp)
    
    biz_text = await scrape_url(biz) if re.match(r'^https?://', biz) else biz
    comp_text = await scrape_url(comp) if re.match(r'^https?://', comp) else comp

    yield f"data: {json.dumps({'type': 'log', 'msg': 'Procesando Neuromarketing y FODA con Inteligencia Artificial Multimodal...'})}\n\n"
    
    prompt = f"""
    Eres un Consultor de Negocios y Copywriter Premium de Silicon Valley, experto en neuromarketing.
    Negocio: '{biz_text}' ({clean_biz}). Competidor: '{comp_text}' ({clean_comp}). Enfoque: '{angle}'.
    
    INSTRUCCIONES ESTRATÉGICAS:
    1. Desarrolla debilidades y ataques EXTENSOS, detallados y técnicos (mínimo 25 palabras por viñeta).
    2. En el 'weekly_plan', redacta párrafos largos y persuasivos usando el framework AIDA.
    3. 'recommendations' debe ser un párrafo masivo y profundo sobre posicionamiento y sesgos cognitivos.
    
    INSTRUCCIONES VISUALES ('image_prompt'):
    1. El usuario puede haber subido fotos. Identifica EXACTAMENTE qué es el producto físico o servicio.
    2. Escribe en INGLÉS descripciones hiper-detalladas para un motor de renderizado realista (Ej: 'close up of artisanal organic soap bar...').
    3. PROHIBIDO incluir rostros humanos a menos que sea marca personal explícita.
    4. Cierra CADA prompt en inglés con esto obligatoriamente: ", high-end commercial macro photography, cinematic studio lighting, highly detailed, Unreal Engine 5 render, 8k resolution".
    
    Responde ÚNICAMENTE en JSON estricto:
    {{
        "title": "Estrategia de Dominio: {clean_biz} vs {clean_comp}",
        "score": 94,
        "chart": {{"tu": {{"eng": 85, "qual": 95, "freq": 60, "auth": 80, "conv": 90}}, "rival": {{"eng": 50, "qual": 70, "freq": 80, "auth": 60, "conv": 65}}}},
        "metrics": {{"engagement": {{"tu": "8.5%", "rival": "4.2%"}}, "quality": {{"tu": "95/100", "rival": "70/100"}}, "freq": {{"tu": "5x/sem", "rival": "3x/sem"}}}},
        "rival_weaknesses": ["Debilidad detallada, técnica y extensa 1", "Debilidad detallada, técnica y extensa 2", "Debilidad detallada, técnica y extensa 3"],
        "attack_strategies": ["Táctica profunda e instructiva 1", "Táctica profunda e instructiva 2", "Táctica profunda e instructiva 3"],
        "weekly_plan": {{
            "lunes": "GANCHO: [Largo]. VALOR: [Largo]. CTA: [Largo].",
            "miercoles": "GANCHO: [Largo]. VALOR: [Largo]. CTA: [Largo].",
            "viernes": "GANCHO: [Largo]. VALOR: [Largo]. CTA: [Largo]."
        }},
        "recommendations": "Redacta un párrafo extenso y altamente profesional (min 50 palabras).",
        "hashtags": "#Keyword1 #Keyword2 #Audiencia1 #Audiencia2 #Dolor1 #Conversion #Premium #Estrategia",
        "carousel": [
            {{"tag": "GANCHO", "headline": "Titular de Impacto", "copy": "Copy persuasivo y denso", "image_prompt": "english specific prompt of product, high-end commercial macro photography..."}},
            {{"tag": "VALOR", "headline": "Beneficio Premium", "copy": "Copy persuasivo y denso", "image_prompt": "english specific prompt of product, high-end commercial macro photography..."}},
            {{"tag": "DIFERENCIADOR", "headline": "Autoridad Máxima", "copy": "Copy persuasivo y denso", "image_prompt": "english specific prompt of product, high-end commercial macro photography..."}},
            {{"tag": "OFERTA", "headline": "Oferta Irresistible", "copy": "Copy persuasivo y denso", "image_prompt": "english specific prompt of product, high-end commercial macro photography..."}}
        ]
    }}
    """
    
    contents = [prompt]
    # Si el usuario subió fotos comprimidas, se añaden al prompt
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
                if "chart" in parsed and "metrics" in parsed:
                    data = parsed
        except Exception:
            pass

    if not data:
        data = get_fallback_data(clean_biz, clean_comp)

    # TIEMPO 1: ENVIAR ESTRATEGIA TEXTO Y FODA (GRATIS)
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
    yield f"data: {json.dumps({'type': 'log', 'msg': 'Generando Placas HD con motor Flux.1 (evitando saturación de red)...'})}\n\n"
    
    slides = []
    for item in data["carousel"]:
        base_prompt = item["image_prompt"].replace("\n", " ").strip()
        safe_prompt = urllib.parse.quote(base_prompt)
        seed = random.randint(1, 999999)
        # Inyectamos &model=flux para activar la IA de máxima calidad
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
    # La descompresión/compresión se hizo en el frontend. Aquí solo leemos.
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
