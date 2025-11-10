# app/alerts.py
import os
from datetime import datetime
from typing import List, Dict, Optional
from google.oauth2 import service_account
from googleapiclient.discovery import build

from time import time

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SPREADSHEET_ID = os.getenv("SHEETS_SPREADSHEET_ID")
TAB_ALERTAS = os.getenv("SHEETS_ALERTS_TAB", "alertas")

ALERT_HEADERS = [
    "alerta_id",
    "gestante_id",
    "Municipio / Territorio EBS",   # <--- nueva columna
    "tipo_alerta", "prioridad",
    "fecha_generacion", "regla_disparadora",
    "estado",
    "fecha_estado", "responsable",
    "canalizacion_tipo", "fecha_canalizacion",
    "fecha_atencion_efectiva", "evidencia_resolucion",
    "intentos_contacto", "observaciones",
    "resuelta",
]

def _svc():
    creds = service_account.Credentials.from_service_account_file(
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS"), scopes=SCOPES
    )
    return build("sheets", "v4", credentials=creds)

def _ensure_tab_exists():
    svc = _svc()
    ss = svc.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    titles = [s["properties"]["title"] for s in ss.get("sheets", [])]
    if TAB_ALERTAS not in titles:
        svc.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests":[{"addSheet":{"properties":{"title": TAB_ALERTAS}}}]}
        ).execute()

def ensure_headers_alertas():
    _ensure_tab_exists()
    svc = _svc()
    rng = f"'{TAB_ALERTAS}'!1:1"
    res = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=rng
    ).execute()
    first = res.get("values", [])
    if not first or not first[0]:
        svc.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{TAB_ALERTAS}'!A1",
            valueInputOption="RAW",
            body={"values":[ALERT_HEADERS]}
        ).execute()

def _col_idx_to_a1(n:int)->str:
    s=""
    while n>0:
        n,r=divmod(n-1,26)
        s=chr(65+r)+s
    return s

def append_alert(row: Dict):
    ensure_headers_alertas()
    svc = _svc()
    values = [[row.get(h, "") for h in ALERT_HEADERS]]
    svc.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=TAB_ALERTAS,
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": values}
    ).execute()

def _find_row_index_by_alerta_id(alerta_id: str) -> Optional[int]:
    svc = _svc()
    end_col = _col_idx_to_a1(len(ALERT_HEADERS))
    rng = f"'{TAB_ALERTAS}'!A1:{end_col}100000"
    res = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=rng
    ).execute()
    vals = res.get("values", [])
    if not vals:
        return None
    hdr = vals[0]
    try:
        idx = hdr.index("alerta_id")
    except ValueError:
        return None
    for i, row in enumerate(vals[1:], start=2):
        if idx < len(row) and str(row[idx]) == str(alerta_id):
            return i
    return None


def update_alert_by_id(alerta_id: str, data: Dict):
    ensure_headers_alertas()
    row_index = _find_row_index_by_alerta_id(alerta_id)
    if not row_index:
        raise ValueError("Alerta no encontrada")
    row_values = [data.get(h, "") for h in ALERT_HEADERS]
    svc = _svc()
    start_col = _col_idx_to_a1(1)
    end_col = _col_idx_to_a1(len(ALERT_HEADERS))
    rng = f"'{TAB_ALERTAS}'!{start_col}{row_index}:{end_col}{row_index}"
    svc.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=rng,
        valueInputOption="USER_ENTERED",
        body={"values":[row_values]}
    ).execute()

# -------- Reglas de negocio --------

def _parse_multi(s: str) -> List[str]:
    return [x.strip() for x in (s or "").split(";") if x.strip()]

def _is_adolescente(edad_str: str) -> bool:
    try:
        return int(edad_str) < 18
    except: return False

def _to_int(s) -> int:
    try: return int(str(s).strip())
    except: return 0

def risk_color(gest: Dict) -> str:
    # Semáforo propuesto
    edad = _to_int(gest.get("Edad"))
    eg   = _to_int(gest.get("Semanas de gestación (EG)"))
    cpn  = _to_int(gest.get("N° de controles prenatales (CPN)"))
    mult = (str(gest.get("Embarazo múltiple","")).strip().lower() == "sí")
    signos = _parse_multi(gest.get("Signos de alarma",""))
    psico  = _parse_multi(gest.get("Factores psicosociales",""))
    vacunas = _parse_multi(gest.get("Estado vacunación materna",""))
    barreras = _parse_multi(gest.get("Barreras de acceso",""))
    antecedentes_med = _parse_multi(gest.get("Antecedentes médicos", ""))
    preeclampsia = str(gest.get("Preeclampsia o eclampsia", "")).strip().lower()
    spa = str(gest.get("Consumo de SPA", "")).strip().lower()


    rojo = (
        _is_adolescente(edad) or
        mult or
        (any(x for x in signos if x.lower() != "ninguno")) or
        (eg >= 12 and cpn == 0) or
        any(k for k in psico if k) or
        any(x in antecedentes_med for x in ["Hipertensión", "Diabetes", "Cardiopatía"]) or
        preeclampsia == "sí"
    )
    if rojo: return "Rojo"

    amarillo = (
        (cpn < 4 and eg >= 20) or
        (len(barreras) >= 1) or
        any("No" in v for v in vacunas) or
        spa == "sí"
    )

    if amarillo: return "Amarillo"
    return "Verde"

def generate_alert_types(gest: Dict) -> List[Dict]:
    """Devuelve las alertas que DEBEN estar abiertas para esta gestante, con prioridad."""
    eg   = _to_int(gest.get("Semanas de gestación (EG)"))
    cpn  = _to_int(gest.get("N° de controles prenatales (CPN)"))
    signos = _parse_multi(gest.get("Signos de alarma",""))
    vacunas = _parse_multi(gest.get("Estado vacunación materna",""))
    barreras = _parse_multi(gest.get("Barreras de acceso",""))

    out = []
    # ======== NUEVAS ALERTAS BASADAS EN ANTECEDENTES ========
    antecedentes_med = _parse_multi(gest.get("Antecedentes médicos", ""))
    preeclampsia = str(gest.get("Preeclampsia o eclampsia", "")).strip().lower()
    spa = str(gest.get("Consumo de SPA", "")).strip().lower()

    # Riesgo alto por antecedentes clínicos relevantes
    if any(x in antecedentes_med for x in ["Hipertensión", "Diabetes", "Cardiopatía"]):
        out.append({
            "tipo_alerta": "ANTECEDENTES_MEDICOS",
            "prioridad": "Rojo",
            "regla": "Antecedentes clínicos relevantes"
        })

    # Antecedente de preeclampsia o eclampsia
    if preeclampsia == "sí":
        out.append({
            "tipo_alerta": "PREECLAMPSIA_ANTECEDENTE",
            "prioridad": "Rojo",
            "regla": "Antecedente de preeclampsia/eclampsia"
        })

    # Consumo de sustancias psicoactivas
    if spa == "sí":
        out.append({
            "tipo_alerta": "CONSUMO_SPA",
            "prioridad": "Amarillo",
            "regla": "Consumo de SPA reportado"
        })

    if eg >= 12 and cpn == 0:
        out.append({"tipo_alerta":"SIN_CPN","prioridad":"Rojo","regla":"EG>=12 AND CPN=0"})
    if any(x for x in signos if x.lower()!="ninguno"):
        out.append({"tipo_alerta":"SIGNOS_ALARMA","prioridad":"Rojo","regla":"Signos != Ninguno"})
    if any("No" in v for v in vacunas):
        out.append({"tipo_alerta":"NO_VACUNADA","prioridad":"Amarillo","regla":"Tdap/Influenza = No"})
    if len([b for b in barreras if b]) >= 2:
        out.append({"tipo_alerta":"BARRERAS_ACCESO","prioridad":"Amarillo","regla":">=2 barreras"})


    # ======== ALERTAS DE CANALIZACIÓN ========
    canalizacion = str(gest.get("Tipo de canalización", "")).upper()
    realizada = str(gest.get("Tipo de canalización realizada", "")).upper()
    fecha_realizada = gest.get("Fecha atención efectiva", "")
    resultado = str(gest.get("Resultado canalización", "")).upper()

    if canalizacion and not realizada:
        out.append({
            "tipo_alerta": "CANALIZACION_PENDIENTE",
            "prioridad": "Rojo",
            "regla": f"Canalizada a {canalizacion}, sin atención registrada"
        })

    elif canalizacion and realizada and any(r in canalizacion for r in realizada.split(",")):
        if resultado != "ATENDIDA":
            out.append({
                "tipo_alerta": "CANALIZACION_PARCIAL",
                "prioridad": "Amarillo",
                "regla": f"Atendida parcialmente en {realizada}, pendiente otros servicios"
            })
        else:
            out.append({
                "tipo_alerta": "CANALIZACION_ATENDIDA",
                "prioridad": "Verde",
                "regla": f"Atendida completamente en {realizada} el {fecha_realizada}"
            })

    elif canalizacion and realizada and not any(r in canalizacion for r in realizada.split(",")):
        out.append({
            "tipo_alerta": "CANALIZACION_NO_COINCIDE",
            "prioridad": "Amarillo",
            "regla": f"Atención ({realizada}) no coincide con canalización previa ({canalizacion})"
        })





    return out

def _now_iso():
    return datetime.now().isoformat(timespec="seconds")

def upsert_alerts_for_gestante(gest: Dict, responsable: str = ""):
    """
    - Crea alertas ABIERTAS que falten.
    - Cierra alertas que ya no aplican (resueltas).
    """
    ensure_headers_alertas()
    all_alerts = read_alerts()
    gid = str(gest.get("id", "")).strip()
    current = [a for a in all_alerts if str(a.get("gestante_id")) == gid]

    # Alertas que deberían existir según el dato actual
    should = generate_alert_types(gest)
    should_types = {s["tipo_alerta"]: s for s in should}

    # 1) Crear las que no existan (o estén cerradas)
    existing_open_types = {
        a["tipo_alerta"]
        for a in current
        if a.get("estado") in ("ABIERTA", "CANALIZADA", "ATENDIDA")
    }

    for t, meta in should_types.items():
        if t not in existing_open_types:
            alerta = {
                "alerta_id": str(int(datetime.now().timestamp() * 1000)) + "-" + t,
                "gestante_id": gid,
                "Municipio / Territorio EBS": gest.get("Municipio / Territorio EBS", ""),
                "tipo_alerta": t,
                "prioridad": meta["prioridad"],
                "fecha_generacion": _now_iso(),
                "regla_disparadora": meta["regla"],
                "estado": "ABIERTA",
                "fecha_estado": "",
                "responsable": responsable or "",
                "canalizacion_tipo": "",
                "fecha_canalizacion": "",
                "fecha_atencion_efectiva": "",
                "evidencia_resolucion": "",
                "intentos_contacto": "",
                "observaciones": "",
                "resuelta": "",
            }

            # 🔹 Guarda la nueva alerta en la hoja de cálculo
            append_alert(alerta)

    # 2) Cerrar las que ya no aplican (resueltas)
    for a in current:
        st = a.get("estado", "")
        t = a.get("tipo_alerta", "")
        if st in ("CERRADA", "EXPIRADA"):
            continue

        resolved = False
        if t == "SIN_CPN":
            resolved = _to_int(gest.get("N° de controles prenatales (CPN)")) >= 1
        elif t == "NO_VACUNADA":
            vacunas = _parse_multi(gest.get("Estado vacunación materna", ""))
            resolved = not any("No" in v for v in vacunas)
        elif t == "SIGNOS_ALARMA":
            signos = _parse_multi(gest.get("Signos de alarma", ""))
            resolved = not any(x for x in signos if x.lower() != "ninguno")
        elif t == "BARRERAS_ACCESO":
            barreras = _parse_multi(gest.get("Barreras de acceso", ""))
            resolved = len([b for b in barreras if b]) < 2


        elif t == "CANALIZACION_PENDIENTE":
            # Cierra la alerta si lo realizado coincide con lo canalizado y el resultado es ATENDIDA
            canalizacion = str(gest.get("Tipo de canalización", "")).upper()
            realizadas = [x.strip().upper() for x in str(gest.get("Tipo de canalización realizada", "")).split(";") if         x.strip()]
            resultado = str(gest.get("Resultado canalización", "")).upper()
            coincide = any(r in canalizacion for r in realizadas)

            if coincide and resultado == "ATENDIDA":
                # Cerramos la pendiente y registramos trazabilidad básica
                a_upd = {h: a.get(h, "") for h in ALERT_HEADERS}
                a_upd["estado"] = "CERRADA"
                a_upd["fecha_estado"] = _now_iso()
                a_upd["resuelta"] = "TRUE"
                # opcional: dejamos evidencia mínima en la misma alerta cerrada
                a_upd["canalizacion_tipo"] = "; ".join(realizadas)
                a_upd["fecha_canalizacion"] = str(gest.get("Fecha canalización", ""))
                a_upd["fecha_atencion_efectiva"] = str(gest.get("Fecha atención efectiva", ""))
                a_upd["evidencia_resolucion"] = (
                    a.get("evidencia_resolucion", "") or "Cierre automático por atención efectiva"
                )
                update_alert_by_id(a["alerta_id"], a_upd)
                continue
        
        elif t == "CANALIZACION_ATENDIDA":
            # Cierra automáticamente las alertas verdes de canalización atendida
            canalizacion = str(gest.get("Tipo de canalización", "")).upper()
            realizadas = [
                x.strip().upper()
                for x in str(gest.get("Tipo de canalización realizada", "")).split(";")
                if x.strip()
            ]
            resultado = str(gest.get("Resultado canalización", "")).upper()
            coincide = any(r in canalizacion for r in realizadas)
            fecha_atencion = str(gest.get("Fecha atención efectiva", "")).strip()

            if coincide and resultado == "ATENDIDA" and fecha_atencion:
                a_upd = {h: a.get(h, "") for h in ALERT_HEADERS}
                a_upd["estado"] = "CERRADA"
                a_upd["fecha_estado"] = _now_iso()
                a_upd["resuelta"] = "TRUE"
                a_upd["canalizacion_tipo"] = "; ".join(realizadas)
                a_upd["fecha_canalizacion"] = str(gest.get("Fecha canalización", ""))
                a_upd["fecha_atencion_efectiva"] = fecha_atencion
                a_upd["evidencia_resolucion"] = (
                    a.get("evidencia_resolucion", "")
                    or "Cierre automático de alerta verde (atención efectiva registrada)"
                )
                update_alert_by_id(a["alerta_id"], a_upd)
                continue



        # Si no debería existir (condición ya no aplica) o está resuelta → cerrar
        if (t not in should_types) or resolved:
            a_upd = {h: a.get(h, "") for h in ALERT_HEADERS}
            a_upd["estado"] = "CERRADA"
            a_upd["fecha_estado"] = _now_iso()
            a_upd["resuelta"] = "TRUE"
            update_alert_by_id(a["alerta_id"], a_upd)

def count_open_alerts_by_gestante_ids(ids: List[str]) -> Dict[str, int]:
    """Devuelve {gestante_id: #abiertas} para los ids dados."""
    alerts = read_alerts()
    open_states = {"ABIERTA","CANALIZADA","ATENDIDA"}
    counts = {gid:0 for gid in ids}
    for a in alerts:
        gid = str(a.get("gestante_id","")).strip()
        if gid in counts and a.get("estado") in open_states:
            counts[gid]+=1
    return counts

def resumen_alertas(desde:str|None=None, hasta:str|None=None) -> Dict[str, Dict[str,int]]:
    """ {tipo_alerta: {detectadas, resueltas, pendientes}} """
    alerts = read_alerts()
    def in_range(iso):
        if not desde and not hasta: return True
        try:
            dt = datetime.fromisoformat(iso)
        except: return False
        if desde: 
            try:
                if dt < datetime.fromisoformat(desde): return False
            except: ...
        if hasta:
            try:
                if dt > datetime.fromisoformat(hasta): return False
            except: ...
        return True

    out={}
    for a in alerts:
        if not in_range(a.get("fecha_generacion","")): 
            continue
        t = a.get("tipo_alerta","")
        out.setdefault(t, {"detectadas":0,"resueltas":0,"pendientes":0})
        out[t]["detectadas"] += 1
        if a.get("estado")=="CERRADA" and a.get("resuelta","").upper()=="TRUE":
            out[t]["resueltas"] += 1
        elif a.get("estado") in ("ABIERTA","CANALIZADA","ATENDIDA"):
            out[t]["pendientes"] += 1
    return out



_svc_client = None
_ALERTS_CACHE = {"ts": 0.0, "data": []}
TTL = int(os.getenv("SHEETS_CACHE_TTL", "30"))

def _svc():
    global _svc_client
    if _svc_client is None:
        creds = service_account.Credentials.from_service_account_file(
            os.getenv("GOOGLE_APPLICATION_CREDENTIALS"), scopes=SCOPES
        )
        _svc_client = build("sheets","v4",credentials=creds, cache_discovery=False)
    return _svc_client

def _read_alerts_raw() -> List[Dict]:
    ensure_headers_alertas()
    svc = _svc()
    end_col = _col_idx_to_a1(len(ALERT_HEADERS))
    rng = f"'{TAB_ALERTAS}'!A1:{end_col}100000"
    res = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=rng
    ).execute()
    vals = res.get("values", []) or []
    if not vals: return []
    hdr = vals[0]
    return [{hdr[i]: (r[i] if i<len(r) else "") for i in range(len(hdr))} for r in vals[1:]]

def read_alerts() -> List[Dict]:
    now = time()
    if _ALERTS_CACHE["data"] and (now - _ALERTS_CACHE["ts"] < TTL):
        return _ALERTS_CACHE["data"]
    data = _read_alerts_raw()
    _ALERTS_CACHE.update(ts=now, data=data)
    return data

def append_alert(row: Dict):
    ensure_headers_alertas()
    svc = _svc()
    svc.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID, range=TAB_ALERTAS,
        valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
        body={"values": [[row.get(h, "") for h in ALERT_HEADERS]]}
    ).execute()
    _ALERTS_CACHE.update(ts=0, data=[])

def update_alert_by_id(alerta_id: str, data: Dict):
    ensure_headers_alertas()
    row_index = _find_row_index_by_alerta_id(alerta_id)
    if not row_index:
        raise ValueError("Alerta no encontrada")
    svc = _svc()
    start_col = _col_idx_to_a1(1)
    end_col   = _col_idx_to_a1(len(ALERT_HEADERS))
    rng = f"'{TAB_ALERTAS}'!{start_col}{row_index}:{end_col}{row_index}"
    svc.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID, range=rng, valueInputOption="USER_ENTERED",
        body={"values":[[data.get(h,"") for h in ALERT_HEADERS]]}
    ).execute()
    _ALERTS_CACHE.update(ts=0, data=[])
