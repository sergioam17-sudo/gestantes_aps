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
    "Fecha canalización",
    "Fecha atención efectiva",
    "Resultado canalización",
    "Observaciones",
    # Campos de auditoría recomendados:
    "usuario_registra",
    "timestamp"
]

def _service():
    creds = service_account.Credentials.from_service_account_file(
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS"), scopes=SCOPES
    )
    return build("sheets", "v4", credentials=creds)

def ensure_headers():
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

def read_all() -> List[Dict]:
    svc = _service()
    rng = f"{TAB}!A1:Z100000"
    resp = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=rng
    ).execute()
    values = resp.get("values", [])
    if not values:
        return []
    headers = values[0]
    out = []
    for r in values[1:]:
        obj = {headers[i]: (r[i] if i < len(r) else "") for i in range(len(headers))}
        out.append(obj)
    return out

def append_row(row: Dict):
    """Inserta en el orden de HEADERS y crea encabezados si no existen."""
    ensure_headers()
    svc = _service()
    values = [[row.get(h, "") for h in HEADERS]]
    svc.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=TAB,
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": values},
    ).execute()

# --- utilidades A1 (si ya la tienes, NO la dupliques) ---
def _a1(tab: str, rng: str) -> str:
    safe_tab = (tab or "").replace("'", "''")
    return f"'{safe_tab}'!{rng}"

# Lee la fila de encabezados
def get_headers() -> list[str]:
    svc = _service()
    res = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=_a1(TAB, "1:1")
    ).execute()
    return res.get("values", [[]])[0] if res.get("values") else []

# Busca índice (1-based) de la fila con ese id (1 = encabezados)
def find_row_index_by_id(rec_id: str) -> int | None:
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


# Actualiza una fila completa (en orden HEADERS) por id
def update_row_by_id(rec_id: str, data: dict):
    ensure_headers()
    row_index = find_row_index_by_id(rec_id)
    if not row_index:
        raise ValueError("Registro no encontrado")

    svc = _service()
    row_values = [data.get(h, "") for h in HEADERS]

    # rango exacto de la fila a sobrescribir (A..)
    start_col = "A"
    end_col = chr(ord("A") + len(HEADERS) - 1)
    rng = _a1(TAB, f"{start_col}{row_index}:{end_col}{row_index}")

    # --- AQUÍ EL ARREGLO ---
    start_col = _col_idx_to_a1(1)                         # "A"
    end_col = _col_idx_to_a1(len(HEADERS))                # p.ej. "AE", "BA", etc.
    rng = _a1(TAB, f"{start_col}{row_index}:{end_col}{row_index}")  # "'Hoja1'!A3:AE3"

    svc.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=rng,
        valueInputOption="USER_ENTERED",
        body={"values": [row_values]},
    ).execute()

def _col_idx_to_a1(n: int) -> str:
    """Convierte índice 1-based a nombre de columna A1 (1->A, 27->AA)."""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s



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

