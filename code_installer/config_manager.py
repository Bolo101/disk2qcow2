"""
config_manager.py – Gestion du mot de passe administrateur.
Le mot de passe est stocké dans /etc/p2v_converter/admin.cred via SecureCredentialStore.
"""

import os
from typing import Tuple

from secure_credentials import SecureCredentialStore

CONFIG_DIR = "/etc/p2v_converter"
CONFIG_FILE = os.path.join(CONFIG_DIR, "admin.conf")
ADMIN_CRED_FILE = os.path.join(CONFIG_DIR, "admin.cred")

DEFAULT_PASSWORD = "0000"
MIN_PASSWORD_LENGTH = 8

_store = SecureCredentialStore(
    path=ADMIN_CRED_FILE,
    default_password=DEFAULT_PASSWORD,
)


def is_password_set() -> bool:
    """Vérifie qu'un mot de passe admin a déjà été configuré."""
    return os.path.isfile(ADMIN_CRED_FILE)


def is_default_password() -> bool:
    """Indique si le mot de passe administrateur est encore la valeur d'usine."""
    return _store.is_default_password(DEFAULT_PASSWORD)


def set_password(password: str) -> None:
    """
    Enregistre (ou remplace) le mot de passe admin sans vérifier l'ancien.
    Lève ValueError si le mot de passe est invalide.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Le mot de passe doit comporter au moins {MIN_PASSWORD_LENGTH} caractères."
        )

    ok, message = _store.force_set_password(password)
    if not ok:
        raise ValueError(message)


def verify_password(password: str) -> bool:
    """
    Vérifie si le mot de passe fourni correspond au mot de passe enregistré.
    Retourne uniquement True/False pour compatibilité.
    """
    ok, _wait = _store.verify(password)
    return ok


def verify_password_with_wait(password: str) -> Tuple[bool, int]:
    """
    Vérifie le mot de passe et retourne (ok, wait_seconds).
    - ok=True, wait=0 : mot de passe correct
    - ok=False, wait=0 : mot de passe incorrect
    - ok=False, wait>0 : verrouillage temporaire en cours
    """
    return _store.verify(password)


def change_password(old_password: str, new_password: str) -> None:
    """
    Change le mot de passe après vérification de l'ancien.
    Lève ValueError si l'ancien mot de passe est incorrect ou si le nouveau est invalide.
    """
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Le nouveau mot de passe doit comporter au moins {MIN_PASSWORD_LENGTH} caractères."
        )

    ok, message = _store.change_password(old_password, new_password)
    if not ok:
        raise ValueError(message)