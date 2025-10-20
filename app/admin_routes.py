#!/usr/bin/env python
# coding: utf-8

# In[ ]:


import os
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List

from app.auth_middleware import get_user, UserContext

import firebase_admin
from firebase_admin import auth as fb_auth, credentials

if not firebase_admin._apps:
    cred = credentials.ApplicationDefault()
    firebase_admin.initialize_app(cred, {
        'projectId': os.getenv('FIREBASE_PROJECT_ID')
    })

router = APIRouter(prefix="/admin", tags=["admin"])

class CreateUserIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    municipio: str
    role: str = Field(default="auxiliar")

class UpdateClaimsIn(BaseModel):
    email: Optional[EmailStr] = None
    uid: Optional[str] = None
    municipio: Optional[str] = None
    roles: Optional[List[str]] = None

def _ensure_admin(user: Optional[UserContext]):
    if user is None:
        raise HTTPException(status_code=401, detail="No autenticado")
    if not user.is_admin():
        raise HTTPException(status_code=403, detail="Solo administradores")

@router.post("/users")
async def create_user(body: CreateUserIn, user: Optional[UserContext] = Depends(get_user)):
    _ensure_admin(user)
    try:
        u = fb_auth.get_user_by_email(body.email)
    except fb_auth.UserNotFoundError:
        u = fb_auth.create_user(email=body.email, password=body.password)
    claims = {"municipio": body.municipio.upper().strip(), "roles": [body.role.lower().strip()]}
    fb_auth.set_custom_user_claims(u.uid, claims)
    return {"ok": True, "uid": u.uid, "email": u.email, "claims": claims}

@router.patch("/users/claims")
async def update_claims(body: UpdateClaimsIn, user: Optional[UserContext] = Depends(get_user)):
    _ensure_admin(user)
    target = None
    if body.uid:
        target = fb_auth.get_user(body.uid)
    elif body.email:
        target = fb_auth.get_user_by_email(body.email)
    else:
        raise HTTPException(status_code=400, detail="Proporcione uid o email")
    claims = target.custom_claims or {}
    if body.municipio:
        claims["municipio"] = body.municipio.upper().strip()
    if body.roles is not None:
        claims["roles"] = [r.lower().strip() for r in body.roles]
    fb_auth.set_custom_user_claims(target.uid, claims)
    return {"ok": True, "uid": target.uid, "email": target.email, "claims": claims}

