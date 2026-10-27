"""
Control API stub service entrypoint (Phase 0).
Provides a lightweight health check endpoint.
No administrative or policy logic is implemented in Phase 0.
"""

from fastapi import FastAPI

app = FastAPI(
    title="MCP SmartCache Control API",
    description="Control API stub for Phase 0 health validation",
    version="0.1.0",
)


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "control_api",
        "version": "0.1.0",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("control_api.main:app", host="0.0.0.0", port=8080, reload=False)
