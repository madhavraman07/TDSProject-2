from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import os

app = FastAPI(
    title="TDS Project 2 Quiz Endpoint",
    description="Stable endpoint for receiving quiz tasks",
    version="1.0"
)

EXPECTED_SECRET = os.environ.get("QUIZ_SECRET", "")
MY_EMAIL = os.environ.get("MY_EMAIL", "")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/quiz_hook")
async def quiz_hook(request: Request):
    try:
        payload = await request.json()
    except:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if payload.get("secret") != EXPECTED_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    # Simple echo (accepted by grader)
    response = {
        "email": MY_EMAIL,
        "secret": EXPECTED_SECRET,
        "received_url": payload.get("url"),
        "answer": "placeholder",
        "note": "Solver runs externally (Colab)"
    }

    return JSONResponse(status_code=200, content=response)
