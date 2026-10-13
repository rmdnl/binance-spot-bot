from passlib.context import CryptContext
from starlette.requests import Request

password_context = CryptContext(schemes=['bcrypt'], deprecated='auto')


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError('Password must be at least 12 characters long')
    return password_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return password_context.verify(password, password_hash)
    except (ValueError, TypeError):
        return False


def is_authenticated(request: Request) -> bool:
    return request.session.get('authenticated') is True
