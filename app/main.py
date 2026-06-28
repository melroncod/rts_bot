# app/main.py

from fastapi import FastAPI
from app.routers import teas

app = FastAPI(
    title="Tea Store API",
    description="CRUD API for Tea Catalog",
    version="1.0.0",
)

app.include_router(teas.router)

@app.get("/")
async def root():
    return {"message": "Tea Store API is running"}
