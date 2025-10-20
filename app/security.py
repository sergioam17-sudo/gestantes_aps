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
    claims = user.get("claims", {})
    role = user.get("role") or claims.get("role")
    municipios = user.get("municipios") or claims.get("municipios") or []
    # normalizamos a MAYÚSCULAS para comparar
    municipios = [str(m).upper().strip() for m in municipios]
    return role, municipios, user
