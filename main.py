# main.py — Railway-friendly solver using a browser-as-a-service (renderer)
import os, time, json, re, tempfile, traceback
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import logging
import csv, io

# Optional PDF parsing
try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except Exception:
    PDFPLUMBER_AVAILABLE = False

app = FastAPI()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tdsproject2")

# Config — set these in Railway environment variables
EXPECTED_SECRET = os.environ.get("QUIZ_SECRET", "TDS2025_madhav_!37")
MY_EMAIL = os.environ.get("MY_EMAIL", "24f2002722@ds.study.iitm.ac.in")
# Browser-as-a-service config (Browserless is the recommended free-start service)
# Example:
#   BROWSERLESS_URL = "https://chrome.browserless.io"
#   BROWSERLESS_TOKEN = "<your-token>"
BROWSERLESS_URL = os.environ.get("BROWSERLESS_URL", "https://chrome.browserless.io")
BROWSERLESS_TOKEN = os.environ.get("BROWSERLESS_TOKEN", None)
RENDER_TIMEOUT = int(os.environ.get("RENDER_TIMEOUT_SECONDS", "30"))

def redact_text(text: str, secrets: list) -> str:
    if not text:
        return text
    out = text
    for s in (secrets or []):
        if s:
            out = out.replace(s, "[REDACTED]")
    return out

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/")
def root():
    return {"service": "TDSProject-2", "status": "running"}

@app.post("/quiz_hook")
async def quiz_hook(req: Request):
    try:
        payload = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Verify secret
    if payload.get("secret") != EXPECTED_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    # Log payload safely (redacted)
    logger.info("Received quiz_hook payload (redacted): %s",
                redact_text(json.dumps(payload), [EXPECTED_SECRET]))

    url = payload.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="Missing url")

    try:
        # orchestrate solving the quiz (with safe exceptions)
        result = solve_quiz_chain(url, payload)
    except Exception as e:
        logger.error("Solver top-level error: %s\n%s", e, traceback.format_exc())
        return JSONResponse(status_code=500, content={"status": "error", "reason": str(e)})

    # Do not include the secret in responses
    safe_result = result
    if isinstance(safe_result, dict):
        # ensure no secret key present
        if "secret" in safe_result:
            safe_result.pop("secret", None)
    return JSONResponse(status_code=200, content=safe_result)

# --- Core orchestration (synchronous helper to allow simple requests-based renderer) ---
def solve_quiz_chain(start_url: str, payload: dict) -> dict:
    """
    Visit start_url, attempt to solve the page, submit answer if submit_url found.
    If the grader returns a next URL, optionally follow it. For safety and speed we follow at most 3 steps.
    """
    max_steps = 3
    current_url = start_url
    last_submit_response = None
    steps = 0

    while current_url and steps < max_steps:
        steps += 1
        logger.info("Solving step %d: %s", steps, current_url)
        # 1) get rendered HTML
        rendered_html = render_page(current_url)
        if rendered_html is None:
            logger.warning("Renderer returned no content for %s", current_url)
            break

        # 2) extract answer from HTML or linked files
        answer = extract_answer_from_html(rendered_html, current_url)
        logger.info("Extracted answer candidate: %s", str(answer))

        # 3) find submit URL on page
        submit_url = find_submit_url(rendered_html, current_url)
        logger.info("Found submit_url: %s", submit_url)

        # 4) if submit_url is present, post the answer
        if submit_url:
            body = {
                "email": payload.get("email"),
                "secret": payload.get("secret"),
                "url": current_url,
                "answer": answer
            }
            try:
                r = requests.post(submit_url, json=body, timeout=30)
                last_submit_response = {"status_code": r.status_code, "body": r.text}
                logger.info("Submit response: %s", redact_text(str(last_submit_response), [EXPECTED_SECRET]))
                # If grader returns a next url in JSON, follow it
                try:
                    j = r.json()
                    next_url = j.get("url")
                    if next_url and next_url != current_url:
                        logger.info("Got next URL from grader: %s", next_url)
                        current_url = next_url
                        continue
                    else:
                        # if grader says correct/incorrect in JSON, return it
                        return {"status": "done", "submit_response": j}
                except Exception:
                    # not JSON — stop
                    return {"status": "submitted", "submit_response": last_submit_response}
            except Exception as e:
                logger.error("Error submitting answer: %s", e)
                last_submit_response = {"error": str(e)}
                break
        else:
            # no submit url: return the candidate answer and the page
            return {"status": "no_submit_url", "url": current_url, "answer_candidate": answer}
        # break safety
        break

    return {"status": "finished_max_steps", "submit_response": last_submit_response, "steps": steps}

# --- Renderer wrapper ---
def render_page(url: str) -> Optional[str]:
    """
    Use a browser-as-a-service to render URL and return HTML.
    If no service configured, fallback to plain requests (may not work for JS pages).
    """
    if BROWSERLESS_TOKEN:
        try:
            # Browserless has a /content endpoint that returns rendered HTML.
            # We POST JSON { "url": url, "options": {...} } to that endpoint.
            # Many providers accept token either via query param or Authorization header.
            endpoint = BROWSERLESS_URL.rstrip("/") + "/content"
            headers = {"Content-Type": "application/json"}
            # Some providers require token in query param 'token', others in header "X-API-Key" or Authorization.
            # We try both: query param if token present.
            params = {"token": BROWSERLESS_TOKEN}
            body = {"url": url, "options": {"waitUntil": "networkidle", "timeout": RENDER_TIMEOUT * 1000}}
            r = requests.post(endpoint, params=params, headers=headers, json=body, timeout=RENDER_TIMEOUT + 10)
            if r.status_code == 200:
                # Browserless returns JSON including 'result' or directly HTML depending on provider.
                try:
                    j = r.json()
                    # If provider returned JSON with 'result' or 'html'
                    for k in ("result", "html", "content"):
                        if k in j:
                            return j[k]
                    # fallback: find first large string
                    for v in j.values():
                        if isinstance(v, str) and len(v) > 100:
                            return v
                except Exception:
                    # not JSON — maybe HTML directly
                    return r.text
            else:
                logger.warning("Renderer returned status %s: %s", r.status_code, r.text[:200])
        except Exception as e:
            logger.error("Renderer error: %s", e)
    # Fallback: try simple requests
    try:
        r = requests.get(url, timeout=RENDER_TIMEOUT)
        if r.status_code == 200:
            return r.text
    except Exception as e:
        logger.error("Fallback requests error: %s", e)
    return None

# --- HTML parsing & extraction helpers ---
def find_submit_url(html: str, base_url: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    # 1) look for <a> or <button> or elements with 'submit' in href/text
    for a in soup.find_all(["a", "button"]):
        href = a.get("href")
        text = (a.get_text() or "").lower()
        if href and ("submit" in href or "submit" in text or "answer" in href or "answer" in text):
            return urljoin(base_url, href)
    # 2) find forms with an action
    form = soup.find("form")
    if form and form.get("action"):
        return urljoin(base_url, form.get("action"))
    # 3) check for meta or script that includes a submit endpoint
    scripts = soup.find_all("script")
    for s in scripts:
        txt = s.get_text()
        if "submit" in txt or "submit_url" in txt or "/submit" in txt:
            m = re.search(r"https?://[^\s'\"\\]+/submit[^\s'\"]*", txt)
            if m:
                return m.group(0)
    # 4) fallback: if the page instructs to POST to a known endpoint like /submit on same host
    parsed = urlparse(base_url)
    fallback = f"{parsed.scheme}://{parsed.netloc}/submit"
    return fallback

def extract_answer_from_html(html: str, base_url: str):
    # Try table sum
    soup = BeautifulSoup(html, "html.parser")
    # 1) Try HTML tables
    tables = soup.find_all("table")
    for table in tables:
        header_texts = [ (th.get_text() or "").strip().lower() for th in table.find_all("th") ]
        # find 'value' like columns
        idx = None
        for i,h in enumerate(header_texts):
            if any(k in h for k in ("value","amount","price","total","sum")):
                idx = i
                break
        # read rows
        rows = table.find_all("tr")
        nums = []
        for r in rows:
            cells = r.find_all(["td","th"])
            if not cells:
                continue
            if idx is not None and idx < len(cells):
                txt = (cells[idx].get_text() or "").strip().replace(",","")
                try:
                    nums.append(float(re.sub(r"[^\d\.\-]", "", txt)))
                except:
                    pass
            else:
                # find first numeric cell
                for c in cells:
                    txt = (c.get_text() or "").strip().replace(",","")
                    try:
                        nums.append(float(re.sub(r"[^\d\.\-]", "", txt)))
                        break
                    except:
                        continue
        if nums:
            return sum(nums)
    # 2) Find CSV/PDF links and process
    links = [a.get("href") for a in soup.find_all("a", href=True)]
    for href in links:
        href_full = urljoin(base_url, href)
        if href_full.lower().endswith(".csv"):
            s = download_text(href_full)
            if s:
                total = sum_csv_first_numeric_column(s)
                if total is not None:
                    return total
        if href_full.lower().endswith(".pdf") and PDFPLUMBER_AVAILABLE:
            tmp = download_binary(href_full)
            if tmp:
                s = sum_pdf_numeric_tables(tmp)
                if s is not None:
                    return s
    # 3) Heuristic fallback: first numeric token on page
    text = soup.get_text(separator=" ")
    m = re.findall(r"[-+]?[0-9]*\.?[0-9]+", text)
    if m:
        try:
            return float(m[0])
        except:
            return m[0]
    return "demo_answer"

def download_text(url: str) -> Optional[str]:
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            return r.text
    except Exception as e:
        logger.error("download_text error for %s : %s", url, e)
    return None

def download_binary(url: str) -> Optional[str]:
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            tmp.write(r.content)
            tmp.flush()
            tmp.close()
            return tmp.name
    except Exception as e:
        logger.error("download_binary error for %s : %s", url, e)
    return None

def sum_csv_first_numeric_column(csvtext: str):
    try:
        f = io.StringIO(csvtext)
        reader = csv.reader(f)
        headers = next(reader, None)
        totals = []
        for row in reader:
            for cell in row:
                s = re.sub(r"[^\d\.\-]", "", cell)
                if s:
                    try:
                        totals.append(float(s))
                        break
                    except:
                        pass
        return sum(totals) if totals else None
    except Exception as e:
        logger.error("sum_csv error: %s", e)
        return None

def sum_pdf_numeric_tables(pdfpath: str):
    if not PDFPLUMBER_AVAILABLE:
        logger.warning("pdfplumber not available")
        return None
    try:
        total = 0.0
        found = False
        with pdfplumber.open(pdfpath) as pdf:
            for p in pdf.pages:
                tables = p.extract_tables()
                for t in tables:
                    for row in t[1:]:
                        for cell in row:
                            if cell:
                                s = re.sub(r"[^\d\.\-]", "", cell)
                                try:
                                    total += float(s)
                                    found = True
                                    break
                                except:
                                    continue
        return total if found else None
    except Exception as e:
        logger.error("sum_pdf_numeric_tables error: %s", e)
        return None
