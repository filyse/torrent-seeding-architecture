import httpx
from fastapi import APIRouter, Depends

from seeding_api.auth import Principal, require_admin
from seeding_api.deps import DbSession, EnginePoolDep
from seeding_api.net_policy import load_net_policy, save_net_policy
from seeding_api.schemas import NetSettingsIn, NetSettingsOut, UploadLimitsIn, UploadLimitsOut
from seeding_api.upload_limits import load_upload_limits, save_upload_limits

router = APIRouter()


@router.get("/settings/net", response_model=NetSettingsOut)
async def get_net_settings(session: DbSession):
    return NetSettingsOut(**(await load_net_policy(session)))


@router.post("/settings/net", response_model=NetSettingsOut)
async def set_net_settings(body: NetSettingsIn, session: DbSession, pool: EnginePoolDep):
    """Глобальная политика DHT/PEX/LSD: сохраняем в БД и сразу рассылаем на все движки.

    DHT/LSD — настройки сессии libtorrent; PEX эмулируется per-torrent флагами (в lt 2.0
    глобального переключателя сессии для PEX нет). Значения переживают перезапуск движка —
    переприменяются при его саморегистрации."""
    current = await load_net_policy(session)
    merged = {
        "dht": body.dht if body.dht is not None else current["dht"],
        "pex": body.pex if body.pex is not None else current["pex"],
        "lsd": body.lsd if body.lsd is not None else current["lsd"],
    }
    saved = await save_net_policy(session, merged)
    await session.commit()

    applied = 0
    errors = 0
    for spec in pool.specs:
        try:
            await pool.client_for(spec.id).set_net_settings(
                saved["dht"], saved["pex"], saved["lsd"]
            )
            applied += 1
        except (KeyError, httpx.HTTPError):
            errors += 1

    return NetSettingsOut(**saved, applied=applied, errors=errors)


@router.get("/settings/upload", response_model=UploadLimitsOut)
async def get_upload_limits(session: DbSession):
    return UploadLimitsOut(**(await load_upload_limits(session)))


@router.post("/settings/upload", response_model=UploadLimitsOut)
async def set_upload_limits(
    body: UploadLimitsIn,
    session: DbSession,
    _: Principal = Depends(require_admin),
):
    current = await load_upload_limits(session)
    merged = {
        "max_parallel_uploads": (
            body.max_parallel_uploads
            if body.max_parallel_uploads is not None
            else current["max_parallel_uploads"]
        ),
        "chunk_concurrency": (
            body.chunk_concurrency
            if body.chunk_concurrency is not None
            else current["chunk_concurrency"]
        ),
    }
    saved = await save_upload_limits(session, merged)
    await session.commit()
    return UploadLimitsOut(**saved)
