from fastapi import APIRouter

from seeding_api.deps import EnginePoolDep
from seeding_api.schemas import EngineOut

router = APIRouter()


@router.get("", response_model=list[EngineOut])
async def list_engines(pool: EnginePoolDep):
    return [
        EngineOut(
            id=s.id,
            url=s.url,
            storage_prefix=s.storage_prefix,
            listen_port=s.listen_port,
        )
        for s in pool.specs
    ]
