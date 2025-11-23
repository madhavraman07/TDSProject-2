# main.py
import os
import json
import asyncio
import tempfile
import traceback
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from playwright.async_api import async_playwright, TimeoutError as PWTimeout
import requests
import pdfplumber
import pandas as pd
from datetime import datetime, timezone
import base64
import re

app = FastAPI()
EXPECTED_SECRET = os.environ.get("QUIZ_SECRET", "changeme_replace_in_prod")
MY_EMAIL = os.environ.get("MY_EMAIL", "you@example.com")

class Incoming(BaseModel):
    email: str
    secret: str
    url: str

async def render_page_and_collect_links(url, timeout=60_000):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context()
        page = await context.new_page()
        try:
            await page.goto(url, timeout=timeout)
            await page.wait_for_load_state("networkidle", timeout=timeout)
        except PWTimeout:
            pass
        visible_text = await page.locator("body").inner_text()
        inner_html = await page.content()
        anchors = await page.eval_on_selector_all("a", "els => els.map(e => ({href: e.href, text: e.innerText}))")
        pre_texts = await page.eval_on_selector_all("pre, code", "els => els.map(e => e.innerText)")
        submit_url = None
        for a in anchors:
            if a['href'] and ("submit" in (a['href'].lower() if a['href'] else "") or "submit" in (a['text'] or "").lower()):
                submit_url = a['href']
                break
        await browser.close()
        return {"text": visible_text, "html": inner_html, "anchors": anchors, "pres": pre_texts, "submit_url": submit_url}

def download_file(url, session=None, timeout=60):
    session = session or requests.Session()
    r = session.get(url, timeout=timeout)
    r.raise_for_status()
    suffix = ""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(r.content)
    tmp.flush()
    tmp.close()
    return tmp.name

def parse_pdf_sum_value_column(pdf_path, page_number=2, column_name="value"):
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if page_number - 1 >= len(pdf.pages):
                return None
            page = pdf.pages[page_number - 1]
            tables = page.extract_tables()
            for table in tables:
                df = pd.DataFrame(table[1:], columns=table[0])
                cols = [c.strip().lower() for c in df.columns]
                if column_name.lower() in cols:
                    col = df.columns[cols.index(column_name.lower())]
                    s = pd.to_numeric(df[col].replace({',': ''}, regex=True), errors='coerce')
                    return float(s.sum(skipna=True))
            text = page.extract_text() or ""
            nums = re.findall(r"[-+]?\d{1,3}(?:,\d{3})*(?:\.\d+)?", text)
            nums = [float(n.replace(",","")) for n in nums] if nums else []
            return sum(nums) if nums else None
    except Exception as e:
        return {"error": str(e)}

async def solve_quiz(url):
    result = {"started_at": datetime.now(timezone.utc).isoformat()}
    page_info = await render_page_and_collect_links(url)
    result["page_text_snippet"] = (page_info["text"][:400] if page_info["text"] else None)
    candidate_payload = None
    for pre in page_info.get("pres", []):
        try:
            # try base64 decode where appropriate
            try:
                dec = base64.b64decode(pre.strip()).decode('utf-8')
                if "secret" in dec or "url" in dec:
                    candidate_payload = dec
                    break
            except Exception:
                pass
            j = json.loads(pre)
            candidate_payload = j
            break
        except Exception:
            continue

    file_urls = []
    for a in page_info.get("anchors", []):
        href = a.get("href")
        if not href:
            continue
        if any(href.lower().endswith(ext) for ext in [".pdf", ".csv", ".xlsx", ".xls", ".json"]):
            file_urls.append(href)

    answers = {}
    session = requests.Session()

    for f in file_urls:
        if f.lower().endswith(".pdf"):
            try:
                path = download_file(f, session=session)
                s = parse_pdf_sum_value_column(path, page_number=2, column_name="value")
                answers["pdf_parse"] = s
            except Exception as e:
                answers["pdf_error"] = str(e)

    if isinstance(candidate_payload, str):
        try:
            j = json.loads(candidate_payload)
            candidate_payload = j
        except Exception:
            pass
    if isinstance(candidate_payload, dict):
        answers["embedded_payload"] = candidate_payload

    result["found_files"] = file_urls
    result["answers"] = answers
    result["submit_hint_url"] = page_info.get("submit_url")

    return result

@app.post("/quiz_hook")
async def quiz_hook(req: Request):
    try:
        payload = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    email = payload.get("email")
    secret = payload.get("secret")
    url = payload.get("url")
    if not (email and secret and url):
        raise HTTPException(status_code=400, detail="Missing fields")

    if secret != EXPECTED_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    try:
        result = await asyncio.wait_for(solve_quiz(url), timeout=160)
    except asyncio.TimeoutError:
        return JSONResponse(status_code=200, content={"status": "timeout", "detail": "solver timed out"})
    except Exception as e:
        tb = traceback.format_exc()
        return JSONResponse(status_code=200, content={"status": "error", "detail": str(e), "trace": tb})

    submit_url = result.get("submit_hint_url") or None
    out_payload = {"email": MY_EMAIL, "secret": EXPECTED_SECRET, "url": url}

    if isinstance(result["answers"].get("pdf_parse"), (int, float)):
        out_payload["answer"] = result["answers"]["pdf_parse"]
    elif isinstance(result["answers"].get("embedded_payload"), dict):
        ep = result["answers"]["embedded_payload"]
        if "answer" in ep:
            out_payload["answer"] = ep["answer"]
    else:
        txt = result.get("page_text_snippet","") or ""
        nums = [float(n.replace(",","")) for n in re.findall(r"[-+]?\d{1,3}(?:,\d{3})*(?:\.\d+)?", txt)]
        if nums:
            out_payload["answer"] = sum(nums)
        else:
            out_payload["answer"] = "unable_to_solve_automatically"

    submit_response = None
    if submit_url:
        try:
            r = requests.post(submit_url, json=out_payload, timeout=30)
            try:
                submit_response = {"status_code": r.status_code, "body": r.json() if r.content else r.text}
            except Exception:
                submit_response = {"status_code": r.status_code, "body": r.text}
        except Exception as e:
            submit_response = {"error": str(e)}
    result["submission"] = submit_response
    result["completed_at"] = datetime.now(timezone.utc).isoformat()
    return JSONResponse(status_code=200, content={"status":"accepted", "result_summary":{"found_files": result.get("found_files"), "submission": submit_response}})

@app.get("/health")
def health():
    return {"status": "ok"}
