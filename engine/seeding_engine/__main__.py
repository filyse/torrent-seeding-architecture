import os

import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("ENGINE_HTTP_PORT", "8081"))
    uvicorn.run("seeding_engine.main:app", host="0.0.0.0", port=port, factory=False)
