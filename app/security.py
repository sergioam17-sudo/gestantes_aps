import os, firebase_admin
from firebase_admin import credentials, auth
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

if not firebase_admin._apps:
    cred = credentials.Certificate(os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
    firebase_admin.initialize_app(cred)

bearer = HTTPBearer()

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    try:
        decoded = auth.verify_id_token(credentials.credentials)
        return decoded
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")

def get_scope(user=Depends(verify_token)):
    # claims = todo lo que viene del token de Firebase
    claims = user.get("claims", {}) or {}

    # 1. Rol: intenta 'role' y, si no, toma el primero de 'roles'
    role = (
        user.get("role")
        or claims.get("role")
        or (claims.get("roles")[0] if claims.get("roles") else None)
    )

    # 2. Municipio: trabajamos SIEMPRE con lista interna, aunque el claim sea simple
    municipios = user.get("municipios") or claims.get("municipios") or []

    # Si no hay lista pero sí hay 'municipio' (singular), creamos la lista con uno
    if (not municipios) and claims.get("municipio"):
        municipios = [claims.get("municipio")]

    # Normalizamos a MAYÚSCULAS y sin espacios
    municipios = [str(m).upper().strip() for m in municipios]

    return role, municipios, user
