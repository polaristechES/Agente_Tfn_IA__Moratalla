"""
SERVIDOR MIDDLEWARE — Clínica Ginecológica Moratalla
====================================================
Puente entre ElevenLabs (tool calling) y la API de la clínica.
Cada endpoint recibe los parámetros que el agente envía, consulta
la API de la clínica y devuelve la respuesta formateada.

Arrancar en local (puerto 8001 para no colisionar con la API de prueba):
    uvicorn main:app --reload --port 8001

Variables de entorno:
    API_KEY_MIDDLEWARE          Clave secreta que deben incluir las tools de ElevenLabs (header X-API-Key)
    API_URL_CLINICA             URL de la API de la clínica (default: http://localhost:8000)
    API_KEY_CLINICA             Clave de autenticación de la API de la clínica (header X-API-Key)
    SUPABASE_URL                URL del proyecto Supabase
    SUPABASE_KEY                Clave anon public de Supabase
    ELEVENLABS_WEBHOOK_SECRET   Secreto HMAC proporcionado por ElevenLabs
"""

from fastapi import FastAPI, APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from supabase import create_client
from dotenv import load_dotenv
import httpx
import os
import logging
import json
import hmac
import hashlib

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

API_KEY_MIDDLEWARE = os.getenv("API_KEY_MIDDLEWARE", "")
API_URL_CLINICA     = os.getenv("API_URL_CLINICA", "http://localhost:8000")
API_KEY_CLINICA    = os.getenv("API_KEY_CLINICA", "")
SUPABASE_URL       = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY       = os.environ.get("SUPABASE_KEY", "")
WEBHOOK_SECRET     = os.environ.get("ELEVENLABS_WEBHOOK_SECRET", "")

# Header de autenticación para la API de la clínica.
# Vacío mientras usamos la API de prueba; se rellena cuando la clínica facilite la clave real.
CLINIC_HEADERS = {"X-API-Key": API_KEY_CLINICA} if API_KEY_CLINICA else {}

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI(
    title="Middleware Clínica Moratalla",
    description="Puente entre ElevenLabs y la API de la clínica.",
    version="1.0.0"
)


# ─── Autenticación ────────────────────────────────────────────────────────────

async def check_api_key(request: Request):
    """Verifica que la petición incluye la API Key correcta en el header X-API-Key."""
    if request.headers.get("X-API-Key", "") != API_KEY_MIDDLEWARE:
        raise HTTPException(status_code=403, detail="API Key inválida")

# Todas las tools del agente van en este router; el webhook tiene su propia auth (HMAC).
router = APIRouter(dependencies=[Depends(check_api_key)])


# ─── Modelos de entrada ───────────────────────────────────────────────────────
# Cada modelo define los parámetros que ElevenLabs enviará al invocar la tool.

class PeticionDNI(BaseModel):
    dni: str

class PeticionCitaID(BaseModel):
    cita_id: int

class PeticionModificarCita(BaseModel):
    cita_id: int
    fecha: Optional[str] = None   # formato YYYY-MM-DD
    hora: Optional[str] = None    # formato HH:MM

class PeticionCrearCita(BaseModel):
    dni_paciente: str
    fecha: str                    # formato YYYY-MM-DD
    hora: str                     # formato HH:MM
    tipo: Optional[str] = "Consulta"

class PeticionCrearPaciente(BaseModel):
    dni: str
    nombre: str
    apellidos: str
    fecha_nacimiento: str         # formato YYYY-MM-DD
    telefono: str
    email: str
    direccion: str


# ─── Helper ───────────────────────────────────────────────────────────────────

def error_response(codigo: int, mensaje: str) -> dict:
    """Respuesta de error homogénea. El agente leerá el campo 'mensaje'."""
    logger.warning("Error API clínica — código %s", codigo)
    return {"error": True, "codigo": codigo, "mensaje": mensaje}


async def get(url: str, **kwargs) -> httpx.Response:
    """GET con timeout y manejo de conexión fallida."""
    kwargs.setdefault("headers", CLINIC_HEADERS)
    async with httpx.AsyncClient(timeout=10) as client:
        return await client.get(url, **kwargs)


async def post(url: str, **kwargs) -> httpx.Response:
    kwargs.setdefault("headers", CLINIC_HEADERS)
    async with httpx.AsyncClient(timeout=10) as client:
        return await client.post(url, **kwargs)


async def patch(url: str, **kwargs) -> httpx.Response:
    kwargs.setdefault("headers", CLINIC_HEADERS)
    async with httpx.AsyncClient(timeout=10) as client:
        return await client.patch(url, **kwargs)


# ─── Health check ─────────────────────────────────────────────────────────────

@app.get("/")
def health():
    return {"estado": "ok", "servicio": "Middleware Clínica Moratalla"}


# ─── CONSULTA DE DATOS ────────────────────────────────────────────────

@router.post("/consultar-paciente")
async def consultar_paciente(datos: PeticionDNI):
    """Devuelve la ficha de un paciente por su DNI."""
    logger.info("Tool: consultar-paciente")
    try:
        r = await get(f"{API_URL_CLINICA}/pacientes/{datos.dni.upper()}")
    except httpx.ConnectError:
        return error_response(503, "No puedo conectar con el sistema de la clínica. Llame directamente a recepción.")
    if r.status_code == 404:
        return error_response(404, "No encontré ningún paciente con ese DNI en el sistema.")
    if r.status_code != 200:
        return error_response(r.status_code, "Error al consultar los datos del paciente.")
    return r.json()


@router.post("/consultar-citas")
async def consultar_citas(datos: PeticionDNI):
    """Devuelve las citas próximas de un paciente por su DNI."""
    logger.info("Tool: consultar-citas")
    try:
        r = await get(
            f"{API_URL_CLINICA}/pacientes/{datos.dni.upper()}/citas",
            params={"solo_futuras": True}
        )
    except httpx.ConnectError:
        return error_response(503, "No puedo conectar con el sistema de la clínica. Llame directamente a recepción.")
    if r.status_code == 404:
        return error_response(404, "No encontré ningún paciente con ese DNI en el sistema.")
    if r.status_code != 200:
        return error_response(r.status_code, "Error al consultar las citas.")
    data = r.json()
    if data["total_citas"] == 0:
        return {"total_citas": 0, "mensaje": "No hay citas próximas registradas para este paciente."}
    return data


# ─── GESTIÓN DE CITAS ─────────────────────────────────────────────────

@router.get("/consultar-disponibilidad")
async def consultar_disponibilidad():
    """Devuelve las franjas horarias libres para pedir o cambiar cita."""
    logger.info("Tool: consultar-disponibilidad")
    try:
        r = await get(f"{API_URL_CLINICA}/disponibilidad")
    except httpx.ConnectError:
        return error_response(503, "No puedo conectar con el sistema de la clínica.")
    if r.status_code != 200:
        return error_response(r.status_code, "Error al consultar la disponibilidad.")
    return r.json()


@router.post("/modificar-cita")
async def modificar_cita(datos: PeticionModificarCita):
    """Cambia la fecha y/o hora de una cita existente."""
    logger.info("Tool: modificar-cita — ID: %s", datos.cita_id)
    cambios = {k: v for k, v in {"fecha": datos.fecha, "hora": datos.hora}.items() if v}
    if not cambios:
        return error_response(400, "No se indicó ningún campo a modificar.")
    try:
        r = await patch(f"{API_URL_CLINICA}/citas/{datos.cita_id}", json=cambios)
    except httpx.ConnectError:
        return error_response(503, "No puedo conectar con el sistema de la clínica.")
    if r.status_code == 404:
        return error_response(404, "No encontré la cita indicada.")
    if r.status_code != 200:
        return error_response(r.status_code, "Error al modificar la cita.")
    return r.json()


@router.post("/cancelar-cita")
async def cancelar_cita(datos: PeticionCitaID):
    """Cancela una cita existente."""
    logger.info("Tool: cancelar-cita — ID: %s", datos.cita_id)
    try:
        r = await patch(f"{API_URL_CLINICA}/citas/{datos.cita_id}", json={"estado": "cancelada"})
    except httpx.ConnectError:
        return error_response(503, "No puedo conectar con el sistema de la clínica.")
    if r.status_code == 404:
        return error_response(404, "No encontré la cita indicada.")
    if r.status_code != 200:
        return error_response(r.status_code, "Error al cancelar la cita.")
    return r.json()


@router.post("/crear-cita")
async def crear_cita(datos: PeticionCrearCita):
    """Crea una cita nueva para un paciente existente."""
    logger.info("Tool: crear-cita")
    payload = {
        "dni_paciente": datos.dni_paciente.upper(),
        "fecha": datos.fecha,
        "hora": datos.hora,
        "tipo": datos.tipo or "Consulta"
    }
    try:
        r = await post(f"{API_URL_CLINICA}/citas", json=payload)
    except httpx.ConnectError:
        return error_response(503, "No puedo conectar con el sistema de la clínica.")
    if r.status_code == 404:
        return error_response(404, "No encontré al paciente en el sistema.")
    if r.status_code != 201:
        return error_response(r.status_code, "Error al crear la cita.")
    return r.json()


# ─── NUEVOS PACIENTES ─────────────────────────────────────────────────

@router.post("/crear-paciente")
async def crear_paciente(datos: PeticionCrearPaciente):
    """Crea una ficha nueva para un paciente que no está en el sistema."""
    logger.info("Tool: crear-paciente")
    payload = {
        "dni": datos.dni.upper(),
        "nombre": datos.nombre,
        "apellidos": datos.apellidos,
        "fecha_nacimiento": datos.fecha_nacimiento,
        "telefono": datos.telefono,
        "email": datos.email,
        "direccion": datos.direccion
    }
    try:
        r = await post(f"{API_URL_CLINICA}/pacientes", json=payload)
    except httpx.ConnectError:
        return error_response(503, "No puedo conectar con el sistema de la clínica.")
    if r.status_code == 409:
        return error_response(409, "Ya existe un paciente con ese DNI en el sistema.")
    if r.status_code != 201:
        return error_response(r.status_code, "Error al crear la ficha del paciente.")
    return r.json()


app.include_router(router)


# ─── WEBHOOK POST-LLAMADA ─────────────────────────────────────────────

@app.post("/webhook-post-llamada")
async def webhook_post_llamada(request: Request):

    # a) Verificación HMAC
    # ElevenLabs envía: "t=<timestamp>,v0=<hmac_hex>"
    # El payload firmado es: "<timestamp>.<body>"
    body       = await request.body()
    sig_header = request.headers.get("ElevenLabs-Signature", "")
    try:
        partes    = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
        timestamp = partes.get("t", "")
        firma_cab = partes.get("v0", "")
    except Exception:
        raise HTTPException(status_code=401, detail="Firma inválida")
    signed_payload = f"{timestamp}.".encode() + body
    firma_esp = hmac.new(
        WEBHOOK_SECRET.encode(),
        signed_payload,
        hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(firma_esp, firma_cab):
        logger.warning("Webhook rechazado: firma HMAC inválida")
        raise HTTPException(status_code=401, detail="Firma inválida")

    # b) Parsear JSON
    datos = json.loads(body)

    # c) Extraer campos — ElevenLabs anida todo bajo "data"
    data          = datos.get("data", {})
    metadata      = data.get("metadata", {})
    duracion      = metadata.get("call_duration_secs", 0)
    transcripcion = data.get("transcript", [])

    # Log temporal para ver la estructura de tool_calls
    for turno in transcripcion:
        for tc in turno.get("tool_calls") or []:
            logger.info("TOOL_CALL: %s", tc)

    # Herramientas: cada turno del agente puede tener "tool_calls"
    nombres_tools = []
    for turno in transcripcion:
        for tc in turno.get("tool_calls") or []:
            nombre = tc.get("tool_name") or tc.get("name", "")
            if nombre:
                nombres_tools.append(nombre)

    # d) Calcular métricas
    transferida   = "transfer_to_number" in nombres_tools
    error_tecnico = any(
        tc.get("is_error", False)
        for turno in transcripcion
        for tc in turno.get("tool_calls") or []
    )
    herramientas  = ", ".join(nombres_tools)

    # e) Guardar en Supabase (sin datos personales — RGPD)
    supabase.table("llamadas").insert({
        "duracion":      int(duracion),
        "transferida":   transferida,
        "error_tecnico": error_tecnico,
        "num_turnos":    len([t for t in transcripcion if t.get("role") in ("agent", "user") and t.get("message")]),
        "herramientas":  herramientas,
    }).execute()

    # f) Log de resumen
    print("=" * 50)
    print("LLAMADA REGISTRADA")
    print(f"  Duración:      {duracion} segundos")
    print(f"  Transferida:   {transferida}")
    print(f"  Error técnico: {error_tecnico}")
    print(f"  Turnos:        {len(transcripcion)}")
    print(f"  Herramientas:  {herramientas or '(ninguna)'}")
    print()
    print("TRANSCRIPCIÓN:")
    for turno in transcripcion:
        rol     = turno.get("role", "")
        mensaje = turno.get("message", "")
        print(f"  [{rol.upper()}] {mensaje}")
    print("=" * 50)

    # g) Respuesta
    return {"ok": True}
