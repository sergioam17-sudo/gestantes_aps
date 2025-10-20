#!/usr/bin/env python
# coding: utf-8

# In[ ]:


import os
from fastapi import Request, HTTPException

# MODO DESARROLLO:
# Mientras pruebas localmente, devolvemos siempre un usuario "admin".
# Cuando pases a producción con Firebase, cambia DEV_AUTH_ALWAYS_ADMIN a "false"
# y completa la verificación real del token (bloque TODO más abajo).

DEV_AUTH_ALWAYS_ADMIN = os.getenv("DEV_AUTH_ALWAYS_ADMIN", "true").lower() == "true"

class UserContext:
    def __init__(self, uid: str = "dev-admin", email: str | None = None, roles: list[str] | None = None, municipio: str | None = None):
        self.uid = uid
        self.email = email
        self._roles = [r.lower() for r in (roles or ["admin"])]
        self.municipio = municipio

    def is_admin(self) -> bool:
        return "admin" in self._roles or "coordinador" in self._roles or "analista_departamental" in self._roles

async def get_user(request: Request) -> UserContext | None:
    # DEV: siempre admin para poder crear usuarios y probar
    if DEV_AUTH_ALWAYS_ADMIN:
        return UserContext()

    # -------- PRODUCCIÓN (con Firebase) --------
    # TODO: habilita este bloque cuando quieras validar el ID Token real.
    # from google.oauth2 import id_token
    # from google.auth.transport import requests
    # audience = os.getenv("FIREBASE_AUTH_AUDIENCE", "")
    # auth_hdr = request.headers.get("Authorization", "")
    # if not auth_hdr.startswith("Bearer "):
    #     raise HTTPException(status_code=401, detail="No autenticado")
    # token = auth_hdr.split(" ", 1)[1]
    # try:
    #     decoded = id_token.verify_token(token, requests.Request(), audience=audience)
    #     uid = decoded.get("user_id") or decoded.get("sub")
    #     email = decoded.get("email")
    #     roles = decoded.get("roles") or decoded.get("role") or []
    #     if isinstance(roles, str):
    #         roles = [roles]
    #     municipio = decoded.get("municipio")
    #     return UserContext(uid=uid, email=email, roles=roles, municipio=municipio)
    # except Exception as e:
    #     raise HTTPException(status_code=401, detail=f"Token inválido: {e}")

