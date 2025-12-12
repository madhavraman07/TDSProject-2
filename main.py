# --- main.py (stable, simple, solves most quiz tasks) ---
import os
import requests
import json
import re
import traceback
import tempfile
import io
import csv
from urllib.parse import urljoin
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from bs4 import BeautifulSoup

try:
    import pdfplumber
    PDF_OK = True
except:
    PDF_OK = False

app = FastAPI()

# ENV VARS
SECRET = os.environ.get("QUIZ_SECRET")
EMAIL = os.environ.get("MY_EMAIL")
BROWSERLESS_TOKEN = os.environ.get("BROWSERLESS_TOKEN")
BROWSERLESS_URL = os.environ.get("BROWSERLESS_URL", "https://chrome.browserless.io")

def render_page(url):
    """ Fetch fully rendered HTML from Browserless """
    endpoint = f"{BROWSERLESS_URL}/content?token={BROWSERLESS_TOKEN}"
    payload = {
        "url": url,
        "options": {"waitUntil": "networkidle"}
    }
    r = requests.post(endpoint, json=payload, timeout=40)
    if r.status_code != 200:
        return None
    try:
        # browserless usually returns {"data": "<html>...</html>"}
        j = r.json()
        if "data" in j:
            return j["data"]
    except:
        pass
    return r.text

def extract_answer(html, base_url):
    soup = BeautifulSoup(html, "html.parser")

    # 1) HTML tables
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        nums = []
        for r in rows:
            cells = r.find_all(["td","th"])
            for c in cells:
                txt = c.get_text(strip=True).replace(",", "")
                if re.fullmatch(r"[-+]?\d*\.?\d+", txt):
                    nums.append(float(txt))
        if nums:
            return sum(nums)

    # 2) CSV/PDF links
    for a in soup.find_all("a", href=True):
        link = urljoin(base_url, a["href"])
        if link.endswith(".csv"):
            text = requests.get(link).text
            f = io.StringIO(text)
            reader = csv.reader(f)
            total = 0
            for row in reader:
                for cell in row:
                    cell = cell.replace(",", "")
                    if re.fullmatch(r"[-+]?\d*\.?\d+", cell):
                        total += float(cell)
            return total

        if link.endswith(".pdf") and PDF_OK:
            file = requests.get(link).content
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(file)
                tmp.flush()
                path = tmp.name
            total = 0
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    tables = page.extract_tables()
                    for t in tables:
                        for row in t:
                            for cell in row:
                                if cell:
                                    cell = cell.replace(",", "")
                                    if re.fullmatch(r"[-+]?\d*\.?\d+", cell):
                                        total += float(cell)
            return total

    # 3) fallback: first number on page
    nums = re.findall(r"[-+]?\d*\.?\d+", soup.get_text())
    if nums:
        return float(nums[0])

    return "demo_answer"

def find_submit_url(html, base_url):
    soup = BeautifulSoup(html, "html.parser")

    # common submit links
    for a in soup.find_all("a", href=True):
        if "submit" in a["href"]:
            return urljoin(base_url, a["href"])

    # fallback: default submit endpoint
    return f"{base_url.rsplit('/',1)[0]}/submit"

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/quiz_hook")
async def quiz_hook(req: Request):
    try:
        payload = await req.json()
    except:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if payload.get("secret") != SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    url = payload.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="Missing url")

    try:
        # STEP 1: Render page
        html = render_page(url)
        if not html:
            return {"status": "error", "reason": "renderer failed"}

        # STEP 2: Extract answer
        answer = extract_answer(html, url)

        # STEP 3: find submit URL
        submit_url = find_submit_url(html, url)

        # STEP 4: submit
        submit_payload = {
            "email": EMAIL,
            "secret": SECRET,
            "url": url,
            "answer": answer
        }

        r = requests.post(submit_url, json=submit_payload, timeout=30)
        try:
            return r.json()
        except:
            return {"status": "submitted", "raw": r.text}

    except Exception as e:
        return {"status": "error", "detail": str(e), "trace": traceback.format_exc()}
