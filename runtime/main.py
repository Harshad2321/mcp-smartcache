"""
Agent runtime stub service entrypoint (Phase 0).
Provides a lightweight health check endpoint.
No agent planning, graph execution, or tool invocation is implemented in Phase 0.
"""

from fastapi import FastAPI

app = FastAPI(
    title="MCP SmartCache Runtime Stub",
    description="Agent runtime stub for Phase 0 health validation",
    version="0.1.0",
)


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "runtime",
        "version": "0.1.0",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("runtime.main:app", host="0.0.0.0", port=8002, reload=False)
