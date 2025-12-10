# app/admin_ui_routes.py

import os
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from firebase_admin import auth as fb_auth
from app.security import get_scope
from fastapi.templating import Jinja2Templates
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(prefix="/admin/ui", tags=["admin-ui"])

def ensure_admin(role: str):
    if role != "admin":
        raise HTTPException(status_code=403, detail="Solo administradores")

# === 1. PANEL HTML ===
@router.get("/usuarios", response_class=HTMLResponse)
async def panel_usuarios(request: Request):
    # El control de administrador se hace en las llamadas AJAX dentro de la página.
    # Solo los usuarios con role="admin" ven el enlace en home.html.
    return templates.TemplateResponse("admin_users.html", {"request": request})


# === 2. LISTAR USUARIOS ===
@router.get("/usuarios/listar")
async def listar_usuarios(scope=Depends(get_scope)):
    role, muni, user = scope
    ensure_admin(role)

    out = []
    
    # 🔥 iterate_all() -> recorre TODOS los usuarios del proyecto
    for u in fb_auth.list_users().iterate_all():

        claims = u.custom_claims or {}
        municipio_u = claims.get("municipio", "")

        out.append({
            "uid": u.uid,
            "email": u.email,
            "municipio": municipio_u,
            "roles": claims.get("roles", [])
        })

    return {"items": out}

# === 3. CREAR USUARIO ===
@router.post("/usuarios/crear")
async def crear_usuario(data: dict, scope=Depends(get_scope)):
    role, muni, user = scope
    ensure_admin(role)

    email = data.get("email", "").strip()
    password = data.get("password", "").strip()
    municipio = data.get("municipio", "").upper().strip()
    rol = data.get("rol", "auxiliar").lower().strip()

    if not email or not password:
        raise HTTPException(400, "Email y contraseña requeridos")

    try:
        # Si ya existe, opcionalmente actualizamos la contraseña
        u = fb_auth.get_user_by_email(email)
        if password:
            u = fb_auth.update_user(u.uid, password=password)
    except fb_auth.UserNotFoundError:
        # Si NO existe, lo creamos
        u = fb_auth.create_user(email=email, password=password)

    # Claims
    claims = u.custom_claims or {}
    claims["municipio"] = municipio
    claims["municipios"] = [municipio]
    claims["roles"] = [rol]
    claims["role"] = rol

    fb_auth.set_custom_user_claims(u.uid, claims)

    return {"ok": True, "uid": u.uid, "email": u.email, "claims": claims}


# === 4. ACTUALIZAR CLAIMS ===

@router.patch("/usuarios/actualizar")
async def actualizar_usuario(data: dict, scope=Depends(get_scope)):
    role, muni, user = scope
    ensure_admin(role)

    uid = data.get("uid")
    municipio = data.get("municipio", "").upper().strip()
    roles = data.get("roles", [])

    if not uid:
        raise HTTPException(400, "uid requerido")

    try:
        u = fb_auth.get_user(uid)
        claims = u.custom_claims or {}

        if municipio:
            claims["municipio"] = municipio
            claims["municipios"] = [municipio]

        if roles:
            roles_norm = [str(r).lower().strip() for r in roles]
            claims["roles"] = roles_norm
            claims["role"] = roles_norm[0] if roles_norm else claims.get("role")

        fb_auth.set_custom_user_claims(uid, claims)
        return {"ok": True}

    except Exception as e:
        raise HTTPException(500, str(e))


# === 5. RESET PASSWORD ===
@router.post("/usuarios/reset")
async def reset_password(data: dict, scope=Depends(get_scope)):
    role, muni, user = scope
    ensure_admin(role)

    email = data.get("email")
    if not email:
        raise HTTPException(400, "email requerido")

    link = fb_auth.generate_password_reset_link(email)
    return {"ok": True, "link": link}

# === 6. ELIMINAR USUARIO ===
@router.delete("/usuarios/eliminar/{uid}")
async def eliminar_usuario(uid: str, scope=Depends(get_scope)):
    role, muni, user = scope
    ensure_admin(role)
    try:
        fb_auth.delete_user(uid)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))
