from fastapi import FastAPI, Request, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
import google.generativeai as genai
import fal_client
import os

app = FastAPI()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.post("/generar", response_class=HTMLResponse)
async def generar_anuncio(request: Request, producto: str = Form(...), descripcion: str = Form(...)):
    # 1. Análisis estratégico con Gemini
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt_analisis = f"Actúa como un experto en marketing SaaS. Producto: {producto}. Descripción: {descripcion}. Analiza puntos fuertes, propuesta de valor única y genera un concepto visual fotorrealista para una campaña de anuncios."
    response = model.generate_content(prompt_analisis)
    analisis_texto = response.text

    # 2. Generación de imagen fotorrealista con FLUX via Fal.ai
    prompt_imagen = f"Professional studio product photography of {producto}, high-end cosmetic styling, soft lighting, 8k resolution, detailed texture"
    result = fal_client.subscribe(
        "fal-ai/flux/schnell",
        arguments={"prompt": prompt_imagen, "image_size": "square_hd"}
    )
    image_url = result['images'][0]['url'] if result.get('images') else None

    return templates.TemplateResponse(
        request=request, 
        name="resultado.html", 
        context={"producto": producto, "analisis": analisis_texto, "image_url": image_url}
    )
