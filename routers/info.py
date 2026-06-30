from pathlib import Path

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

_DATA_FILE = Path(__file__).parent.parent / "data" / "etf_ucits.yaml"
_KATEGORIE_ORDER = ["Benchmark", "US-Sektor", "Region", "Faktor", "Makro/Rohstoff/FX", "Gold-Miners/Crypto"]


@router.get("/info", response_class=HTMLResponse)
async def info_page(request: Request):
    rows = yaml.safe_load(_DATA_FILE.read_text()) or []
    by_kategorie = {k: [] for k in _KATEGORIE_ORDER}
    for row in rows:
        by_kategorie.setdefault(row["kategorie"], []).append(row)
    return templates.TemplateResponse(
        request, "info.html",
        {"by_kategorie": [(k, by_kategorie[k]) for k in _KATEGORIE_ORDER if by_kategorie[k]]},
    )
