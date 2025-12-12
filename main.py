from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import os, requests, json

app = FastAPI()
EXPECTED_SECRET = os.environ.get("QUIZ_SECRET", "TDS2025_madhav_!37")
MY_EMAIL = os.environ.get("MY_EMAIL", "24f2002722@ds.study.iitm.ac.in")

@app.get("/health")
def health():
    return {"status":"ok"}

@app.post("/quiz_hook")
async def quiz_hook(req: Request):
    try:
        payload = await req.json()
    except:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if payload.get("secret") != EXPECTED_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    out = {
        "email": MY_EMAIL,
        "secret": EXPECTED_SECRET,
        "url": payload.get("url"),
        "answer": "demo_answer"
    }

    return JSONResponse(status_code=200, content={"status":"accepted","result_summary":out})
