from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health():
    """Liveness/readiness check for Docker and Kubernetes (no login needed)."""
    return {"status": "ok"}
