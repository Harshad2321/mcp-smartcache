"""
Gateway stub service entrypoint (Phase 0).
Provides a lightweight health check endpoint.
No routing, caching, or business logic is implemented in Phase 0.
"""

from fastapi import FastAPI

app = FastAPI(
    title="MCP SmartCache Gateway Stub",
    description="Gateway stub for Phase 0 health validation",
    version="0.1.0",
)


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "gateway",
        "version": "0.1.0",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("gateway.main:app", host="0.0.0.0", port=8000, reload=False)
