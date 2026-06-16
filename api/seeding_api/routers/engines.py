from fastapi import APIRouter

from seeding_api.deps import EnginePoolDep
from seeding_api.schemas import EngineOut

router = APIRouter()


@router.get("", response_model=list[EngineOut])
async def list_engines(pool: EnginePoolDep):
    stats = await pool.session_stats_all()
    out: list[EngineOut] = []
    for s in pool.specs:
        st = stats.get(s.id) or {}
        online = not st.get("error")
        dt = st.get("disk_total")
        df = st.get("disk_free")
        out.append(
            EngineOut(
                id=s.id,
                url=s.url,
                storage_prefix=s.storage_prefix,
                listen_port=s.listen_port,
                disk_total=int(dt) if dt is not None else None,
                disk_free=int(df) if df is not None else None,
                online=online,
            )
        )
    return out
