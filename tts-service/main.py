"""
MWS TTS Service — OpenAI-compatible TTS endpoint powered by gTTS.
Provides /v1/audio/speech for OpenWebUI integration.
"""

import io
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from gtts import gTTS

app = FastAPI(title="MWS TTS Service", version="1.0.0")

# Map OpenAI voice names to gTTS language/tld combos for variety
VOICE_MAP = {
    "alloy": {"lang": "en", "tld": "com"},
    "echo": {"lang": "en", "tld": "co.uk"},
    "fable": {"lang": "en", "tld": "com.au"},
    "onyx": {"lang": "en", "tld": "co.in"},
    "nova": {"lang": "en", "tld": "ca"},
    "shimmer": {"lang": "en", "tld": "co.za"},
}

DEFAULT_VOICE = {"lang": "en", "tld": "com"}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/v1/models")
async def list_models():
    return {
        "data": [
            {"id": "tts-1", "object": "model"},
            {"id": "tts-1-hd", "object": "model"},
        ]
    }


@app.get("/v1/audio/voices")
async def list_voices():
    return {
        "voices": [
            {"id": name, "name": name.capitalize()}
            for name in VOICE_MAP
        ]
    }


@app.post("/v1/audio/speech")
async def speech(request: Request):
    body = await request.json()
    text = body.get("input", "")
    voice_name = body.get("voice", "alloy")

    if not text:
        raise HTTPException(status_code=400, detail="No input text provided")

    voice_config = VOICE_MAP.get(voice_name, DEFAULT_VOICE)

    try:
        tts = gTTS(text=text, lang=voice_config["lang"], tld=voice_config["tld"])
        audio_data = io.BytesIO()
        tts.write_to_fp(audio_data)
        audio_data.seek(0)

        return StreamingResponse(
            audio_data,
            media_type="audio/mpeg",
            headers={"Content-Disposition": "attachment; filename=speech.mp3"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
