"""Gera APP_PASSWORD_HASH e um SESSION_SECRET para o arquivo .env do servidor.

Uso:  python -m app.hash_password
"""
import getpass
import secrets
import sys

from app.auth import hash_password


def main() -> None:
    pw = getpass.getpass("Nova senha de acesso (mín. 12 caracteres): ")
    if len(pw) < 12:
        sys.exit("Senha muito curta. Use pelo menos 12 caracteres.")
    if getpass.getpass("Repita a senha: ") != pw:
        sys.exit("As senhas não conferem.")
    print("\nCopie para o .env do servidor (nunca para o GitHub):\n")
    print(f"APP_PASSWORD_HASH='{hash_password(pw)}'")
    print(f"SESSION_SECRET='{secrets.token_urlsafe(48)}'")
    print("\nNo .env use aspas simples: o hash contém '$'.")


if __name__ == "__main__":
    main()
