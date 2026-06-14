from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import config

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("/disco", response_class=HTMLResponse)
async def disco_page(request: Request):
    html_exists = config.DISCO_LATEST_HTML.exists()
    return templates.TemplateResponse(request, "disco.html", {"html_available": html_exists})


@router.get("/disco-chart")
async def disco_chart():
    if not config.DISCO_LATEST_HTML.exists():
        return HTMLResponse("<p>Kein disco-Chart verfügbar. Bitte disco-Run ausführen.</p>", status_code=404)
    return FileResponse(config.DISCO_LATEST_HTML, media_type="text/html")
