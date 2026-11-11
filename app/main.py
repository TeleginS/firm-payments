from fastapi import FastAPI

app = FastAPI(title="Firm Payments")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe for the container and the load balancer. Doesn't touch the database."""
    return {"status": "ok"}
