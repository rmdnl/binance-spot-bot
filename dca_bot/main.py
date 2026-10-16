from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from passlib.context import CryptContext

from .config import Settings

settings = Settings()
app = FastAPI(title="Binance Spot Terminal")
templates = Jinja2Templates(directory="dca_bot/templates")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def require_password_hash() -> str:
    value = settings.dashboard_password_hash.strip()
    if not value:
        raise RuntimeError("DASHBOARD_PASSWORD_HASH is required")
    return value


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, password: str = Form(...)):
    if len(password) < settings.dashboard_min_password_length:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Password minimal 8 karakter"}, status_code=400)
    if not pwd.verify(password, require_password_hash()):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Password salah"}, status_code=401)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie("dashboard_session", "authenticated", httponly=True, secure=True, samesite="strict", max_age=3600)
    return response


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    if request.cookies.get("dashboard_session") != "authenticated":
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("dashboard_session")
    return response
