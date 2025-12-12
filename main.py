from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import os

app = FastAPI(
    title="TDS Project 2 Quiz Endpoint",
    description="Stable endpoint for receiving quiz tasks",
    version="1.0"
)

# Load environment variables
EXPECTED_SECRET = os.environ.get("QUIZ_SECRET", "")
MY_EMAIL = os.environ.get("MY_EMAIL", "")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/quiz_hook")
async def quiz_hook(request: Request):
    """
    This endpoint:
    - validates secret
    - echoes required fields back
    - formats the response cleanly
    - NEVER fails / NEVER crashes
    - does NOT attempt solving the quiz (we solve in Colab)
    """
    try:
        payload = await request.json()
    except:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Secret validation
    if payload.get("secret") != EXPECTED_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    # Construct safe echo response
    response_payload = {
        "email": MY_EMAIL,
        "secret": EXPECTED_SECRET,
        "received_url": payload.get("url"),
        "note": "Endpoint OK. Solver runs separately.",
        "answer": "placeholder"
    }

    return JSONResponse(status_code=200, content=response_payload)
