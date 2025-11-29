# main.py — safe version (no secret leakage)
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import os
import logging

app = FastAPI()
EXPECTED_SECRET = os.environ.get("QUIZ_SECRET", "TDS2025_madhav_!37")
MY_EMAIL = os.environ.get("MY_EMAIL", "24f2002722@ds.study.iitm.ac.in")

# basic logger setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tdsproject2")

def redact_text(text: str, secrets: list) -> str:
    """
    Replace any exact occurrences of sensitive strings with [REDACTED].
    Keep it simple: exact substring replacement.
    """
    if not text or not secrets:
        return text
    out = text
    for s in secrets:
        if not s:
            continue
        out = out.replace(s, "[REDACTED]")
    return out

@app.get("/")
def root():
    return {"service": "TDSProject-2", "status": "running"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/quiz_hook")
async def quiz_hook(req: Request):
    # parse JSON payload
    try:
        payload = await req.json()
    except:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # validate secret
    if payload.get("secret") != EXPECTED_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    # redact any sensitive info from logs before logging payload
    safe_payload_str = redact_text(str(payload), [EXPECTED_SECRET])
    logger.info("Received quiz_hook payload (redacted): %s", safe_payload_str)

    # Build response WITHOUT the secret (do not echo it back)
    out = {
        "email": MY_EMAIL,
        "url": payload.get("url"),
        "answer": "demo_answer"
    }

    # If you ever include model-generated text, redact it like:
    # model_text = redact_text(model_text, [EXPECTED_SECRET])

    # Return a safe response (no secret included)
    return JSONResponse(status_code=200, content={"status": "accepted", "result_summary": out})
