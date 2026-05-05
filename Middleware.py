"""
SERVIDOR MIDDLEWARE — Clínica Ginecológica Moratalla
====================================================
Puente entre ElevenLabs (tool calling) y la API de la clínica.
Cada endpoint recibe los parámetros que el agente envía, consulta
la API de la clínica y devuelve la respuesta formateada.

Arrancar en local (puerto 8001 para no colisionar con la API de prueba):
    uvicorn main:app --reload --port 8001

Variables de entorno:
    CLINIC_API_BASE_URL  URL de la API de la clínica (default: http://localhost:8000)
"""

from fastapi import FastAPI, Request
from pydantic import BaseModel
from typing import Optional
import httpx
import os
import logging
import json

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CLINIC_API_URL = os.getenv("CLINIC_API_BASE_URL", "http://localhost:8000")

app = FastAPI(
    title="Middleware Clínica Moratalla",
    description="Puente entre ElevenLabs y la API de la clínica.",
    version="1.0.0"
)


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
    async with httpx.AsyncClient(timeout=10) as client:
        return await client.get(url, **kwargs)


async def post(url: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(timeout=10) as client:
        return await client.post(url, **kwargs)


async def patch(url: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(timeout=10) as client:
        return await client.patch(url, **kwargs)


# ─── Health check ─────────────────────────────────────────────────────────────

@app.get("/")
def health():
    return {"estado": "ok", "servicio": "Middleware Clínica Moratalla"}


# ─── CONSULTA DE DATOS ────────────────────────────────────────────────

@app.post("/consultar-paciente")
async def consultar_paciente(datos: PeticionDNI):
    """Devuelve la ficha de un paciente por su DNI."""
    logger.info("Tool: consultar-paciente")
    try:
        r = await get(f"{CLINIC_API_URL}/pacientes/{datos.dni.upper()}")
    except httpx.ConnectError:
        return error_response(503, "No puedo conectar con el sistema de la clínica. Llame directamente a recepción.")
    if r.status_code == 404:
        return error_response(404, "No encontré ningún paciente con ese DNI en el sistema.")
    if r.status_code != 200:
        return error_response(r.status_code, "Error al consultar los datos del paciente.")
    return r.json()


@app.post("/consultar-citas")
async def consultar_citas(datos: PeticionDNI):
    """Devuelve las citas próximas de un paciente por su DNI."""
    logger.info("Tool: consultar-citas")
    try:
        r = await get(
            f"{CLINIC_API_URL}/pacientes/{datos.dni.upper()}/citas",
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

@app.post("/consultar-disponibilidad")
async def consultar_disponibilidad():
    """Devuelve las franjas horarias libres para pedir o cambiar cita."""
    logger.info("Tool: consultar-disponibilidad")
    try:
        r = await get(f"{CLINIC_API_URL}/disponibilidad")
    except httpx.ConnectError:
        return error_response(503, "No puedo conectar con el sistema de la clínica.")
    if r.status_code != 200:
        return error_response(r.status_code, "Error al consultar la disponibilidad.")
    return r.json()


@app.post("/modificar-cita")
async def modificar_cita(datos: PeticionModificarCita):
    """Cambia la fecha y/o hora de una cita existente."""
    logger.info("Tool: modificar-cita — ID: %s", datos.cita_id)
    cambios = {k: v for k, v in {"fecha": datos.fecha, "hora": datos.hora}.items() if v}
    if not cambios:
        return error_response(400, "No se indicó ningún campo a modificar.")
    try:
        r = await patch(f"{CLINIC_API_URL}/citas/{datos.cita_id}", json=cambios)
    except httpx.ConnectError:
        return error_response(503, "No puedo conectar con el sistema de la clínica.")
    if r.status_code == 404:
        return error_response(404, "No encontré la cita indicada.")
    if r.status_code != 200:
        return error_response(r.status_code, "Error al modificar la cita.")
    return r.json()


@app.post("/cancelar-cita")
async def cancelar_cita(datos: PeticionCitaID):
    """Cancela una cita existente."""
    logger.info("Tool: cancelar-cita — ID: %s", datos.cita_id)
    try:
        r = await patch(f"{CLINIC_API_URL}/citas/{datos.cita_id}", json={"estado": "cancelada"})
    except httpx.ConnectError:
        return error_response(503, "No puedo conectar con el sistema de la clínica.")
    if r.status_code == 404:
        return error_response(404, "No encontré la cita indicada.")
    if r.status_code != 200:
        return error_response(r.status_code, "Error al cancelar la cita.")
    return r.json()


@app.post("/crear-cita")
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
        r = await post(f"{CLINIC_API_URL}/citas", json=payload)
    except httpx.ConnectError:
        return error_response(503, "No puedo conectar con el sistema de la clínica.")
    if r.status_code == 404:
        return error_response(404, "No encontré al paciente en el sistema.")
    if r.status_code != 201:
        return error_response(r.status_code, "Error al crear la cita.")
    return r.json()


# ─── NUEVOS PACIENTES ─────────────────────────────────────────────────

@app.post("/crear-paciente")
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
        r = await post(f"{CLINIC_API_URL}/pacientes", json=payload)
    except httpx.ConnectError:
        return error_response(503, "No puedo conectar con el sistema de la clínica.")
    if r.status_code == 409:
        return error_response(409, "Ya existe un paciente con ese DNI en el sistema.")
    if r.status_code != 201:
        return error_response(r.status_code, "Error al crear la ficha del paciente.")
    return r.json()


# ─── WEBHOOK POST-LLAMADA ─────────────────────────────────────────────

@app.post("/webhook-post-llamada")
async def webhook_post_llamada(request: Request):

    body  = await request.body()
    datos = json.loads(body)

    duracion      = datos.get("duration_seconds", 0)
    herramientas  = datos.get("tools_called", [])
    transcripcion = datos.get("transcript", [])

    transfirio  = any(t["tool"] == "transfer_to_number" for t in herramientas)
    hubo_error  = any(not t.get("success", True) for t in herramientas)

    print("=" * 50)
    print("LLAMADA RECIBIDA")
    print(f"  Duración:      {duracion} segundos")
    print(f"  Transferida:   {transfirio}")
    print(f"  Error técnico: {hubo_error}")
    print(f"  Turnos:        {len(transcripcion)}")
    print()
    print("TRANSCRIPCIÓN:")
    for turno in transcripcion:
        rol     = turno.get("role", "")
        mensaje = turno.get("message", "")
        print(f"  [{rol.upper()}] {mensaje}")
    print()
    print("HERRAMIENTAS USADAS:")
    for t in herramientas:
        print(f"  {t['tool']} → {'OK' if t.get('success') else 'ERROR'}")
    print("=" * 50)

    return {"ok": True}
