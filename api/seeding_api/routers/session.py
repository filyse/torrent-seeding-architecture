from fastapi import APIRouter, HTTPException
from seeding_db.repository import TorrentRepository

from seeding_api.deps import DbSession, EnginePoolDep
from seeding_api.schemas import SessionLimitsIn

router = APIRouter(tags=["session"])


@router.get("/session/stats")
async def get_session_stats(pool: EnginePoolDep):
    by_engine = await pool.session_stats_all()
    return pool.aggregate_session_stats(by_engine)


@router.post("/session/limits")
async def set_session_limits(body: SessionLimitsIn, pool: EnginePoolDep):
    if body.engine_id:
        try:
            stats = await pool.client_for(body.engine_id).set_session_limits(
                body.download_limit, body.upload_limit
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="engine not found") from exc
        return {"engine_id": body.engine_id, "stats": stats}
    results = await pool.set_session_limits_all(body.download_limit, body.upload_limit)
    return {"engines": results, "aggregate": pool.aggregate_session_stats(results)}


@router.get("/labels")
async def list_labels(session: DbSession):
    repo = TorrentRepository(session)
    return await repo.list_labels()
