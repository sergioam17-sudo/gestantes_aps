#!/usr/bin/env python
# coding: utf-8

# In[ ]:


# -*- coding: utf-8 -*-
"""
Asignar custom claims (role, municipios) a un usuario de Firebase por correo.
Uso (CMD/PowerShell):
  python set_claims.py --email soporte@dominio.com --role muni --municipios GIRON,BETULIA
  python set_claims.py --email admin@dominio.com --role admin

Requisitos:
  - Variable de entorno GOOGLE_APPLICATION_CREDENTIALS apuntando al JSON del Service Account
  - Paquete firebase-admin instalado
"""

import os
import argparse
import sys
import firebase_admin
from firebase_admin import credentials, auth

def init_firebase():
    cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not cred_path or not os.path.exists(cred_path):
        print("ERROR: Debes definir GOOGLE_APPLICATION_CREDENTIALS con la ruta al JSON del Service Account.")
        sys.exit(1)
    if not firebase_admin._apps:
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)

def normalize_role(role: str) -> str:
    role = (role or "").strip().lower()
    valid = {"admin", "dept", "muni", "viewer"}
    if role not in valid:
        raise ValueError(f"Rol inválido '{role}'. Válidos: {', '.join(sorted(valid))}")
    return role

def normalize_municipios(munis_str: str | None):
    if not munis_str:
        return []
    # separa por coma o punto y coma, quita espacios y sube a mayúsculas
    parts = []
    for token in munis_str.replace(";", ",").split(","):
        t = token.strip()
        if t:
            parts.append(t.upper())
    # dedup manteniendo orden
    seen = set()
    result = []
    for m in parts:
        if m not in seen:
            seen.add(m)
            result.append(m)
    return result

def set_custom_claims_by_email(email: str, role: str, municipios_str: str | None, revoke_sessions: bool):
    init_firebase()
    user = auth.get_user_by_email(email)
    role = normalize_role(role)
    municipios = normalize_municipios(municipios_str)

    claims = {"role": role}
    if municipios:
        claims["municipios"] = municipios

    # Si ya existían claims, puedes fusionar. Aquí se sobreescriben explícitamente:
    auth.set_custom_user_claims(user.uid, claims)

    if revoke_sessions:
        # Revoca tokens de actualización para forzar que el usuario obtenga un ID token nuevo con los claims actualizados
        auth.revoke_refresh_tokens(user.uid)

    print("=== RESULTADO ===")
    print(f"email: {email}")
    print(f"uid:   {user.uid}")
    print(f"role:  {claims['role']}")
    print(f"munis: {claims.get('municipios', [])}")
    print(f"revoke_refresh_tokens: {revoke_sessions}")
    print("Listo. El usuario debe volver a iniciar sesión (o renovar su ID token) para ver los nuevos claims.")

def get_custom_claims(email: str):
    init_firebase()
    user = auth.get_user_by_email(email)
    print("=== CLAIMS ACTUALES ===")
    print(f"email: {email}")
    print(f"uid:   {user.uid}")
    print(f"claims:{user.custom_claims or {}}")

def main():
    parser = argparse.ArgumentParser(description="Asignar/consultar custom claims en Firebase por correo.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # set
    p_set = sub.add_parser("set", help="Asignar claims")
    p_set.add_argument("--email", required=True, help="Correo del usuario en Firebase")
    p_set.add_argument("--role", required=True, help="Rol: admin | dept | muni | viewer")
    p_set.add_argument("--municipios", default="", help="Lista de municipios separados por coma o punto y coma (opcional)")
    p_set.add_argument("--revoke", action="store_true", help="Revocar sesiones para forzar que el usuario renueve token")

    # get
    p_get = sub.add_parser("get", help="Consultar claims actuales")
    p_get.add_argument("--email", required=True, help="Correo del usuario en Firebase")

    args = parser.parse_args()
    if args.cmd == "set":
        set_custom_claims_by_email(args.email, args.role, args.municipios, args.revoke)
    elif args.cmd == "get":
        get_custom_claims(args.email)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()

