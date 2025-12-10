#!/usr/bin/env python
# coding: utf-8

from pathlib import Path
import os
from datetime import datetime
from typing import Optional, List

from googleapiclient.errors import HttpError

from app.alerts import (
    ensure_headers_alertas,
    upsert_alerts_for_gestante,
    count_open_alerts_by_gestante_ids,
    resumen_alertas,
    risk_color,
    read_alerts,
)




from fastapi import FastAPI, Request, Depends, HTTPException, Body
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware import Middleware



# Rutas propias
from app.admin_routes import router as admin_router  # asegúrate de tener app/admin_routes.py
from app.admin_ui_routes import router as admin_ui_router
from app.security import get_scope                   # verifica que exista app/security.py
from app.sheets import read_all, append_row, HEADERS, update_row_by_id # y app/sheets.py

from app.sheets import append_history  # + lo que ya importas


# === Utilidades ===

BASE_DIR = Path(__file__).resolve().parent.parent  # .../gestantes_cloudrun_app
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

def clean_env(name: str, default: str = "") -> str:
    """Lee una variable de entorno y elimina comillas simples/dobles o espacios accidentales."""
    val = os.getenv(name, default)
    if val is None:
        return default
    return val.strip().strip('"').strip("'")

def get_allowed_origins() -> List[str]:
    """
    Obtiene orígenes CORS permitidos desde ALLOWED_ORIGINS.
    Por defecto, habilita localhost para desarrollo.
    """
    raw = os.getenv("ALLOWED_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000")
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    return origins

# === App FastAPI ===

app = FastAPI(title="Gestantes APS")

# CORS: si usas credenciales (cookies/Authorization), no puede ser "*"
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# Montar carpeta de estáticos si existe (evita error en dev)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Motor de plantillas
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Incluir rutas de administración
app.include_router(admin_router)
app.include_router(admin_ui_router)


# === Eventos ===

@app.on_event("startup")
def _startup_info():
    # Avisos útiles en logs
    missing = []
    for key in ["SHEETS_SPREADSHEET_ID", "GOOGLE_APPLICATION_CREDENTIALS"]:
        if not clean_env(key):
            missing.append(key)
    if missing:
        # No detenemos la app, pero lo dejamos en log para diagnóstico
        print(f"[WARN] Faltan variables de entorno: {', '.join(missing)}")

    try:
        ensure_headers_alertas()
    except Exception as e:
        print(f"[WARN] No se pudo preparar hoja de alertas: {e}")


# === Rutas públicas ===

@app.get("/health", response_class=PlainTextResponse)
def health():
    return "ok"

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    ctx = {
        "request": request,
        "api_key": clean_env("FIREBASE_API_KEY"),
        "auth_domain": clean_env("FIREBASE_AUTH_DOMAIN"),
        "project_id": clean_env("FIREBASE_PROJECT_ID"),
        "storage_bucket": clean_env("FIREBASE_STORAGE_BUCKET", "seguimientomaternas.appspot.com"),
    }
    return templates.TemplateResponse("home.html", ctx)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    ctx = {
        "request": request,
        # Importante: limpiamos posibles comillas puestas en CMD/Powershell
        "api_key": clean_env("FIREBASE_API_KEY"),
        "auth_domain": clean_env("FIREBASE_AUTH_DOMAIN"),
        "project_id": clean_env("FIREBASE_PROJECT_ID"),
        "storage_bucket": clean_env("FIREBASE_STORAGE_BUCKET", "seguimientomaternas.appspot.com"),
    }
    return templates.TemplateResponse("login.html", ctx)

# === Rutas API (modo ligero con Google Sheets) ===

MUNI_COL = "Municipio"  # columna para filtrar

# --- Catálogos para selects (item 2) ---
CATALOGOS = {
    "Perfil profesional": ["Médico", "Enfermero(a)", "Auxiliar", "Otro"],
    "Zona": ["Rural", "Urbana"],
    "Lugar de captación": ["Hogar", "Jornada", "Escuela", "Otro"],
    "Regimen de salud": ["Sudsidiado", "Contributivo", "Especial", "Otro"],
    "EPS": ["Nueva EPS", "Fundación SaludMía EPS", "EPS Sura", "EPS Sanitas S.A.", "EPS Famisanar", "Magisterio", "Fiduprevisora", "Comparta", "Otro"],
    "Enfoque diferencial": ["Adolescente", "Migrante", "Víctima VBG", "Discapacidad", "Etnia", "Ninguno"],
    "Embarazo múltiple": ["Sí", "No"],
    "Atención por EBS": ["Sí", "No"],
    "Atención por IPS/ESE": ["Sí", "No"],
    "Estado vacunación materna": ["Tdap Sí", "Tdap No", "Tdap NA", "Influenza Sí", "Influenza No", "Influenza NA", "Covid-19 Sí", "Covid-19 No", "Covid-19 NA","VSR Sí", "VSR No", "VSR NA"],
    "Consejería recibida": ["Signos de alarma", "Planificación posparto", "Lactancia", "Nutrición"],
    "Tamizajes reportados": ["VDRL - Sifilis", "VIH", "HBsAg", "Orina – Tomado", "Orina – No tomado", "Cuadro hematico o hemoglobina"],
    "Antecedentes familiares": ["Hipertensión arterial", "Diabetes mellitus", "Malformaciones congénitas", "Ninguno"],
    "Antecedentes obstétricos": ["Aborto previo", "Parto pretérmino", "Cesárea previa", "Ninguno"],
    "Antecedentes médicos": ["Hipertensión", "Diabetes", "Cardiopatía", "Epilepsia", "Ninguno"],
    "Preeclampsia o eclampsia": ["Sí", "No"],
    "Enfermedades crónicas actuales": ["Hipertensión", "Diabetes", "Asma", "Otra", "Ninguna"],
    "Consumo de SPA": ["Sí", "No"],
    "Signos de alarma": ["Cefalea", "Fiebre", "Sangrado", "Dolor abdominal","Edema miembros superiores o inferiores","Prurito en manos y pies","Perdida de liquido","Visión borrosa", "Ausencia de movimiento de bebe", "Ardor o sangrado al orinar", "Vomito severo", "Ninguno"],
    "Factores psicosociales": ["VBG - Violencia Basada en genero", "Consumo SPA", "Apoyo familiar ausente", "Inseguridad alimentaria"],
    "Barreras de acceso": ["Transporte", "Horarios", "Distancia", "Cuidado de hijos", "Afiliación"],
    "Tipo de canalización": ["CPN", "Vacunación", "Salud oral", "Lab", "Trabajo social", "Otro"],
    "Tipo de canalización realizada": ["CPN", "Vacunación", "Salud oral", "Psicosocial", "Planificación Familiar", "Otro"],
    "Resultado canalización": ["Atendida", "No asistió", "Reprogramada", "Pendiente"],
    "Gestación finalizada": ["Sí", "No"],
    "Tipo de desenlace": [
        "Parto vaginal",
        "Cesárea",
        "Aborto espontáneo",
        "Aborto inducido",
        "Muerte fetal in útero",
        "Embarazo ectópico",
        "Otro"
    ],
    "Resultado del embarazo": [
        "Recién nacido vivo",
        "Recién nacido muerto",
        "Embarazo múltiple todos vivos",
        "Embarazo múltiple al menos un fallecido"
    ],

}

# Rango/restricciones simples para validación en el server
REGLAS = {
    "Edad_min": 10, "Edad_max": 55,                 # 10–55
    "EG_min": 4, "EG_max": 42,                      # 4–42
}

@app.get("/api/catalogos")
def catalogos():
    """Devuelve listas para poblar selects y reglas básicas de validación."""
    return {"catalogos": CATALOGOS, "reglas": REGLAS}


def _parse_iso_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        # Si el usuario pasa dd/mm/yyyy
        try:
            d, m, y = s.split("/")
            return datetime(int(y), int(m), int(d))
        except Exception:
            raise HTTPException(status_code=400, detail=f"Fecha inválida: {s}. Usa YYYY-MM-DD o dd/mm/yyyy.")


@app.get("/api/usuario/municipio")
def usuario_municipio(scope=Depends(get_scope)):
    """Devuelve el municipio asignado al usuario actual."""
    role, muni_list, user = scope
    return {
        "role": role,
        "municipios": muni_list or [],
        "email": user.get("email", "")
    }




@app.get("/api/gestantes")
def listar_gestantes(
    scope=Depends(get_scope),
    desde: str | None = None,
    hasta: str | None = None,
    q: str | None = None,            # búsqueda por documento o nombre (contiene)
    page: int = 1,                   # página 1-based
    page_size: int = 20              # tamaño de página
):
    role, muni_list, user = scope
    data = read_all()

    # filtro por municipio si no es admin
    if role != "admin":
        if not muni_list:
            return {"items": [], "total": 0, "page": page, "page_size": page_size}
        muni_set = set([m.upper().strip() for m in muni_list])
        data = [x for x in data if str(x.get(MUNI_COL, "")).upper().strip() in muni_set]

    # filtro por fechas
    def _parse_iso_date(s: str | None):
        if not s: return None
        try: return datetime.fromisoformat(s)
        except Exception:
            try:
                d,m,y = s.split("/")
                return datetime(int(y),int(m),int(d))
            except Exception:
                return None

    if desde or hasta:
        d_from = _parse_iso_date(desde)
        d_to = _parse_iso_date(hasta)
        def in_range(fs: str):
            fs = (fs or "").strip()
            if not fs: return False
            try:
                dt = datetime.fromisoformat(fs)
            except Exception:
                try:
                    d,m,y = fs.split("/")
                    dt = datetime(int(y),int(m),int(d))
                except Exception:
                    return False
            if d_from and dt < d_from: return False
            if d_to and dt > d_to: return False
            return True
        data = [x for x in data if in_range(str(x.get("Fecha de captación","")))]

    # filtro por búsqueda q (documento o nombre contiene)
    if q:
        qn = q.lower().strip()
        def match(x):
            doc = str(x.get("Tipo y N° de identificación","")).lower()
            nom = str(x.get("Nombres y apellidos","")).lower()
            return (qn in doc) or (qn in nom)
        data = [x for x in data if match(x)]

    total = len(data)
    # paginación
    if page < 1: page = 1
    if page_size < 1: page_size = 20
    start = (page - 1) * page_size
    end = start + page_size
    items = data[start:end]

    # Enriquecer con 'riesgo' y '# alertas abiertas' para la página actual
    # 1) riesgo
    for x in items:
        try:
            x["riesgo"] = risk_color(x)
        except:
            x["riesgo"] = ""

    # 2) # alertas abiertas (para IDs de la página)
    ids = [str(x.get("id","")).strip() for x in items]
    try:
        counts = count_open_alerts_by_gestante_ids(ids)
    except:
        counts = {i:0 for i in ids}
    for x in items:
        gid = str(x.get("id","")).strip()
        x["alertas_abiertas"] = counts.get(gid, 0)



    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/api/gestantes")
def crear_gestante(item: dict = Body(...), scope=Depends(get_scope)):
    """
    Crea un registro en la hoja. Restringe por municipio del usuario si no es admin.
    Requiere mínimo: Municipio / Territorio EBS, Nombres y apellidos, Tipo y N° de identificación.
    """
    role, muni_list, user = scope
    email = user.get("email", "desconocido")

    muni = str(item.get(MUNI_COL, "")).upper().strip()

    # --- Validación de municipio (no admin) ---
    if role != "admin":
        def normalize(s: str) -> str:
            return (
                str(s)
                .strip()
                .replace("Á", "A")
                .replace("É", "E")
                .replace("Í", "I")
                .replace("Ó", "O")
                .replace("Ú", "U")
                .upper()
            )

        user_muni = set([normalize(m) for m in (muni_list or [])])
        muni_norm = normalize(muni)

        if not user_muni or muni_norm not in user_muni:
            raise HTTPException(status_code=403, detail="No puedes crear en otro municipio")

    # --- Validación mínima obligatoria ---
    for f in [MUNI_COL, "Nombres y apellidos", "Tipo y N° de identificación"]:
        if not str(item.get(f, "")).strip():
            raise HTTPException(status_code=400, detail=f"Falta el campo obligatorio: {f}")

    # --- Rango de edad ---
    if "Edad" in item and str(item["Edad"]).strip():
        try:
            edad = int(item["Edad"])
            if not (REGLAS["Edad_min"] <= edad <= REGLAS["Edad_max"]):
                raise HTTPException(status_code=400, detail=f"Edad fuera de rango ({REGLAS['Edad_min']}-{REGLAS['Edad_max']})")
        except ValueError:
            raise HTTPException(status_code=400, detail="Edad debe ser numérica")

    # --- Rango semanas EG ---
    if "Semanas de gestación (EG)" in item and str(item["Semanas de gestación (EG)"]).strip():
        try:
            eg = int(item["Semanas de gestación (EG)"])
            if not (REGLAS["EG_min"] <= eg <= REGLAS["EG_max"]):
                raise HTTPException(status_code=400, detail=f"EG fuera de rango ({REGLAS['EG_min']}-{REGLAS['EG_max']})")
        except ValueError:
            raise HTTPException(status_code=400, detail="EG debe ser numérica")

    # --- Auditoría y defaults ---
    now = datetime.now().isoformat(timespec="seconds")
    item.setdefault("id", str(int(datetime.now().timestamp())))
    item.setdefault("usuario_registra", email)
    item.setdefault("timestamp", now)

    # --- Normaliza fechas ---
    for k in [
        "Fecha de captación",
        "Fecha última menstruación (FUM) o eco",
        "Fecha último CPN",
        "Fecha canalización",
        "Fecha atención efectiva",
    ]:
        if k in item and isinstance(item[k], str):
            item[k] = item[k].strip()

    # --- Validaciones de consistencia de fechas ---
    def to_dt(s):
        if not s:
            return None
        try:
            return datetime.fromisoformat(s)
        except Exception:
            try:
                d, m, y = s.split("/")
                return datetime(int(y), int(m), int(d))
            except Exception:
                return None

    f_cap = to_dt(item.get("Fecha de captación", ""))
    f_cpn = to_dt(item.get("Fecha último CPN", ""))
    f_can = to_dt(item.get("Fecha canalización", ""))
    f_ate = to_dt(item.get("Fecha atención efectiva", ""))

    if f_cpn and f_cap and f_cpn > f_cap:
        raise HTTPException(status_code=400, detail="Fecha último CPN no puede ser > Fecha de captación")
    if f_can and f_cap and f_can < f_cap:
        raise HTTPException(status_code=400, detail="Fecha canalización debe ser ≥ Fecha de captación")
    if f_ate and f_can and f_ate < f_can:
        raise HTTPException(status_code=400, detail="Fecha atención efectiva debe ser ≥ Fecha canalización")

    # === VALIDACIÓN DE DUPLICADO POR DOCUMENTO + MUNICIPIO ===
    documento_new = str(item.get("Tipo y N° de identificación", "")).strip()
    municipio_new = str(item.get("Municipio", "")).strip().upper()

    if documento_new:
        registros_exist = read_all()
        for r in registros_exist:
            doc_exist = str(r.get("Tipo y N° de identificación", "")).strip()
            muni_exist = str(r.get("Municipio", "")).strip().upper()

            if doc_exist == documento_new and muni_exist == municipio_new:
                raise HTTPException(
                    status_code=409,
                    detail="La gestante ya está registrada en este municipio."
                )


    # --- Asegura columnas ---
    safe = {h: item.get(h, "") for h in HEADERS}

    # --- Evita duplicados ---
    data_existente = read_all()
    documento = str(item.get("Tipo y N° de identificación", "")).strip()
    if documento and any(str(x.get("Tipo y N° de identificación", "")).strip() == documento for x in data_existente):
        raise HTTPException(status_code=400, detail="Ya existe una gestante registrada con este documento.")

    # --- Inserta fila ---
    try:
        append_row(safe)
    except HttpError as e:
        raise HTTPException(status_code=502, detail=f"Sheets API error: {e}") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error interno al escribir en Sheets: {e}") from e

    # --- HISTORIAL: CREAR ---
    try:
        diff = {k: safe.get(k, "") for k in HEADERS}
        append_history(
            gestante_id=safe["id"],
            accion="CREAR",
            cambiado_por=email,
            diff=diff,
            snapshot=safe,
            fecha_cambio_iso=now,
        )
    except Exception as e:
        print(f"[WARN] No se pudo registrar historial (crear): {e}")

    # --- ALERTAS ---
    try:
        upsert_alerts_for_gestante(safe, email)
    except Exception as e:
        print(f"[WARN] No se pudo actualizar alertas (crear): {e}")

    return {"ok": True, "id": safe["id"]}


# Obtener 1 registro por id
@app.get("/api/gestantes/{rec_id}")
def obtener_gestante(rec_id: str, scope=Depends(get_scope)):
    role, muni_list, user = scope
    data = read_all()
    item = next((x for x in data if str(x.get("id")) == str(rec_id)), None)
    if not item:
        raise HTTPException(status_code=404, detail="No encontrado")
    if role != "admin":
        muni_set = set([m.upper().strip() for m in muni_list])
        if str(item.get(MUNI_COL, "")).upper().strip() not in muni_set:
            raise HTTPException(status_code=403, detail="Sin permiso para ver este registro")
    return item


from fastapi import Query



@app.get("/api/gestantes/{rec_id}/historial")
def historial_gestante(rec_id: str, scope=Depends(get_scope)):
    from app.sheets import _service, SPREADSHEET_ID, HISTORY_TAB, HISTORY_HEADERS, _col_idx_to_a1, read_all
    svc = _service()
    role, muni_list, user = scope

    # Validación de permisos
    data = read_all()
    registro = next((x for x in data if str(x.get("id")) == str(rec_id)), None)
    if not registro:
        raise HTTPException(status_code=404, detail="No encontrado")
    if role != "admin":
        muni_set = set([m.upper().strip() for m in muni_list])
        if str(registro.get("Municipio", "")).upper().strip() not in muni_set:
            raise HTTPException(status_code=403, detail="Sin permiso para ver el historial de este municipio")

    # Lectura del historial
    end_col = _col_idx_to_a1(len(HISTORY_HEADERS))
    rng = f"{HISTORY_TAB}!A1:{end_col}100000"
    resp = svc.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=rng).execute()
    values = resp.get("values", []) or []
    if len(values) <= 1:
        return {"items": [], "total": 0}

    header = values[0]
    gid_idx = next((i for i, h in enumerate(header) if h.strip().lower() == "gestante_id"), None)
    if gid_idx is None:
        raise HTTPException(status_code=500, detail="Columna 'gestante_id' no encontrada en hoja de historial")

    items = []
    for r in values[1:]:
        if gid_idx < len(r):
            val = str(r[gid_idx]).strip()
            rec_cmp = str(rec_id).strip()
            if val.endswith(".0"):
                val = val[:-2]
            if rec_cmp.endswith(".0"):
                rec_cmp = rec_cmp[:-2]
            if val == rec_cmp:
                fila = {header[i]: (r[i] if i < len(r) else "") for i in range(len(header))}
                items.append(fila)

    # Ordena por versión
    def _key(x):
        try:
            return int(x.get("version", 0))
        except:
            return 0
    items.sort(key=_key)

    return {"items": items, "total": len(items)}




# Actualizar (complementar) por id
@app.put("/api/gestantes/{rec_id}")
def actualizar_gestante(
    rec_id: str,
    payload: dict = Body(...),
    scope=Depends(get_scope),
):
    role, muni_list, user = scope
    email = user.get("email", "desconocido")

    # Leemos el actual para validar permisos
    data = read_all()
    current = next((x for x in data if str(x.get("id")) == str(rec_id)), None)
    if not current:
        raise HTTPException(status_code=404, detail="No encontrado")
    if role != "admin":
        muni_set = set([m.upper().strip() for m in muni_list])
        if str(current.get(MUNI_COL, "")).upper().strip() not in muni_set:
            raise HTTPException(status_code=403, detail="No puedes editar registros de otros municipios")

    # Merge (solo HEADERS)
    merged = {h: current.get(h, "") for h in HEADERS}
    for k, v in payload.items():
        if k in merged:
            merged[k] = v

    # Auditoría
    merged["usuario_registra"] = email
    merged["timestamp"] = datetime.now().isoformat(timespec="seconds")

    # --- DIFF para historial ---
    def _diff(before: dict, after: dict) -> dict:
        d = {}
        for k in HEADERS:
            b = before.get(k, "")
            a = after.get(k, "")
            if str(b) != str(a):
                # guarda solo lo que cambió; si prefieres, guarda [antes, despues]
                d[k] = {"antes": b, "despues": a}
        return d

    cambios = _diff(current, merged)
    fecha_cambio = merged["timestamp"]



    try:
        update_row_by_id(rec_id, merged)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"No se pudo actualizar: {e}") from e


    # --- HISTORIAL: ACTUALIZAR ---
    try:
        append_history(
            gestante_id=rec_id,
            accion="ACTUALIZAR",
            cambiado_por=email,
            diff=cambios,
            snapshot=merged,
            fecha_cambio_iso=fecha_cambio
        )
    except Exception as e:
        print(f"[WARN] No se pudo registrar historial (actualizar): {e}")




    try:
        upsert_alerts_for_gestante(merged, email)
    except Exception as e:
        print(f"[WARN] No se pudo actualizar alertas (editar): {e}")



    return {"ok": True, "id": rec_id}


@app.get("/api/alertas/resumen")
def api_resumen_alertas(desde: str | None = None, hasta: str | None = None, scope=Depends(get_scope)):
    role, muni_list, user = scope
    # (Opcional) podrías filtrar por municipio si tu hoja de alertas incluyera municipio.
    return resumen_alertas(desde, hasta)

from typing import Optional

@app.get("/api/alertas")
def api_list_alertas(
    gestante_id: Optional[str] = None,
    tipo: Optional[str] = None,
    estado: Optional[str] = None,
    scope=Depends(get_scope),
):
    """
    Lista alertas. Si pasas gestante_id, devuelve solo las de esa persona.
    Filtros extra: tipo_alerta, estado (ABIERTA|CANALIZADA|ATENDIDA|CERRADA|EXPIRADA)
    """
    role, muni_list, user = scope
    data = read_alerts()

    def ok(a):
        if gestante_id and str(a.get("gestante_id","")) != str(gestante_id):
            return False
        if tipo and str(a.get("tipo_alerta","")).upper() != str(tipo).upper():
            return False
        if estado and str(a.get("estado","")).upper() != str(estado).upper():
            return False
        return True

    out = [a for a in data if ok(a)]

    # ordenar por fecha_generacion desc si existe
    def key_dt(a):
        iso = a.get("fecha_generacion", "") or ""
        try:
            from datetime import datetime
            return datetime.fromisoformat(iso)
        except:
            return None

    # 🔧 Evita mezclar tipos al ordenar (usa timestamp numérico)
    def safe_timestamp(a):
        dt = key_dt(a)
        return dt.timestamp() if dt else 0

    out.sort(key=safe_timestamp, reverse=True)


    return {"items": out, "total": len(out)}

