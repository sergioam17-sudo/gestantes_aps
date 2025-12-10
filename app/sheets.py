import os
from typing import List, Dict
from google.oauth2 import service_account
from googleapiclient.discovery import build
from time import time

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SPREADSHEET_ID = os.getenv("SHEETS_SPREADSHEET_ID")
TAB = os.getenv("SHEETS_MAIN_TAB", "gestantes")

HEADERS = [
    "id",
    "Fecha de captación",
    "Perfil profesional",
    "Lugar de captación",
    "Tipo y N° de identificación",
    "Nombres y apellidos",
    "Edad",
    "Teléfono(s) de contacto",
    "Dirección / Ubicación",
    "Regimen de salud",
    "EPS",
    "Municipio",
    "Zona",
    "Territorio",
    "Microterritorio",  
    "Enfoque diferencial",
    "Semanas de gestación (EG)",
    "Embarazo múltiple",
    "Fecha última menstruación (FUM) o eco",
    "N° de controles prenatales (CPN)",
    "Fecha último CPN",
    "Antecedentes familiares",
    "Antecedentes obstétricos",
    "Antecedentes médicos",
    "Preeclampsia o eclampsia",
    "Enfermedades crónicas actuales",
    "Consumo de SPA",
    "Atención por EBS",
    "Atención por IPS/ESE",
    "N° atenciones por EBS",
    "Estado vacunación materna",
    "Consejería recibida",
    "Tamizajes reportados",
    "Signos de alarma",
    "Factores psicosociales",
    "Barreras de acceso",
    "Tipo de canalización",
    "Tipo de canalización realizada",
    "Fecha canalización",
    "Fecha atención efectiva",
    "Resultado canalización",
    "Observaciones",
    "Gestación finalizada",
    "Fecha de desenlace",
    "Tipo de desenlace",
    "Resultado del embarazo",

    # Campos de auditoría recomendados:
    "usuario_registra",
    "timestamp"
]

# === HISTORIAL ===
HISTORY_TAB = os.getenv("SHEETS_HISTORY_TAB", "gestantes_historial")

HISTORY_HEADERS = [
    "hist_id",          # str: timestamp o UUID
    "gestante_id",      # str: id de la hoja principal
    "accion",           # "CREAR" | "ACTUALIZAR"
    "cambiado_por",     # email del usuario
    "fecha_cambio",     # ISO 8601
    "version",          # int: 1,2,3...
    "diff_json",        # json solo con campos modificados
    "snapshot_json"     # json con el estado completo después del cambio
]




_service_client = None
_CACHE = {"ts": 0.0, "data": []}
TTL = int(os.getenv("SHEETS_CACHE_TTL", "30"))  # seg

def _service():
    global _service_client
    if _service_client is None:
        creds = service_account.Credentials.from_service_account_file(
            os.getenv("GOOGLE_APPLICATION_CREDENTIALS"), scopes=SCOPES
        )
        _service_client = build("sheets","v4",credentials=creds, cache_discovery=False)
    return _service_client


def read_all() -> List[Dict]:
    """Lee con caché en memoria y sólo hasta la última columna real."""
    now = time()
    if _CACHE["data"] and (now - _CACHE["ts"] < TTL):
        return _CACHE["data"]

    svc = _service()
    end_col = _col_idx_to_a1(len(HEADERS))
    rng = f"{TAB}!A1:{end_col}100000"
    resp = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=rng
    ).execute()
    values = resp.get("values", []) or []
    if not values:
        _CACHE.update(ts=now, data=[])
        return []

    headers = values[0]
    data = [{headers[i]: (r[i] if i < len(r) else "") for i in range(len(headers))}
            for r in values[1:]]
    _CACHE.update(ts=now, data=data)
    return data


def append_row(row: Dict):
    ensure_headers()
    svc = _service()
    values = [[row.get(h, "") for h in HEADERS]]
    svc.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID, range=TAB,
        valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
        body={"values": values},
    ).execute()
    _CACHE.update(ts=0, data=[])        # <-- invalida cache

def update_row_by_id(rec_id: str, data: dict):
    ensure_headers()
    row_index = find_row_index_by_id(rec_id)
    if not row_index:
        raise ValueError("Registro no encontrado")
    svc = _service()
    start_col = _col_idx_to_a1(1)
    end_col   = _col_idx_to_a1(len(HEADERS))
    rng = _a1(TAB, f"{start_col}{row_index}:{end_col}{row_index}")
    svc.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID, range=rng, valueInputOption="USER_ENTERED",
        body={"values": [[data.get(h, "") for h in HEADERS]]},
    ).execute()
    _CACHE.update(ts=0, data=[])        # <-- invalida cache

# --- utilidades auxiliares requeridas ---

def ensure_headers():
    """Garantiza que la hoja principal tenga los encabezados definidos."""
    svc = _service()
    res = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"{TAB}!1:1"
    ).execute()
    first = res.get("values", [])
    if not first or not first[0]:
        svc.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{TAB}!A1",
            valueInputOption="RAW",
            body={"values": [HEADERS]},
        ).execute()

def _a1(tab: str, rng: str) -> str:
    """Arma rango A1 escapando el nombre de hoja."""
    safe_tab = (tab or "").replace("'", "''")
    return f"'{safe_tab}'!{rng}"

def _col_idx_to_a1(n: int) -> str:
    """Convierte índice 1-based a nombre de columna A1 (1->A, 27->AA)."""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s

def find_row_index_by_id(rec_id: str) -> int | None:
    """Busca la fila (1-based) de la gestante por su ID."""
    svc = _service()
    end_col = _col_idx_to_a1(len(HEADERS))
    rng = _a1(TAB, f"A1:{end_col}100000")
    res = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=rng
    ).execute()
    values = res.get("values", [])
    if not values:
        return None
    headers = values[0]
    try:
        id_col = headers.index("id")
    except ValueError:
        return None
    for i, row in enumerate(values[1:], start=2):
        if id_col < len(row) and str(row[id_col]) == str(rec_id):
            return i
    return None

import json, uuid

def append_history(gestante_id: str, accion: str, cambiado_por: str, diff: dict, snapshot: dict, fecha_cambio_iso: str):
    """Guarda la versión histórica del registro en la hoja gestantes_historial."""
    from googleapiclient.errors import HttpError

    try:
        svc = _service()

        # Garantiza encabezados en la hoja de historial
        res = svc.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range=f"{HISTORY_TAB}!1:1"
        ).execute()
        first = res.get("values", [])
        if not first or not first[0]:
            svc.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{HISTORY_TAB}!A1",
                valueInputOption="RAW",
                body={"values": [HISTORY_HEADERS]},
            ).execute()

        # Calcula la versión siguiente para esa gestante
        end_col = _col_idx_to_a1(len(HISTORY_HEADERS))
        rng = f"{HISTORY_TAB}!A1:{end_col}100000"
        data = svc.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range=rng
        ).execute().get("values", [])
        version = 1
        if len(data) > 1:
            try:
                gid_idx = data[0].index("gestante_id")
                count = sum(1 for r in data[1:] if gid_idx < len(r) and str(r[gid_idx]) == str(gestante_id))
                version = count + 1
            except Exception:
                version = 1

        # Inserta una fila en la hoja de historial
        row = [
            str(uuid.uuid4()),                     # hist_id
            str(gestante_id).split(".")[0],                      # gestante_id
            accion,                                # CREAR / ACTUALIZAR
            cambiado_por,                          # usuario
            fecha_cambio_iso,                      # fecha
            version,                               # versión
            json.dumps(diff, ensure_ascii=False),  # diferencias
            json.dumps(snapshot, ensure_ascii=False)  # snapshot completo
        ]

        svc.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=HISTORY_TAB,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()

    except HttpError as e:
        print(f"[ERROR] Falló la escritura del historial en Sheets: {e}")
    except Exception as e:
        print(f"[WARN] Error general en append_history: {type(e).__name__} - {e}")

