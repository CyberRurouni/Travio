import asyncio
from core import fetch_agency_id_by_email


required = asyncio.run(fetch_agency_id_by_email(email="ahk3155263@gmail.com"))

print(required)