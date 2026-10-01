from fastapi import APIRouter

from app.core.config import settings

router = APIRouter()


@router.get("/health")
async def health_check():
    return {"status": "healthy", "service": "PASP Control Center API"}


@router.get("/")
async def root():
    return {
        "name": "PASP Control Center",
        "version": settings.VERSION,
        "description": "Performance and Scale for AI Platforms - Cluster Management & Reservation System"
    }
