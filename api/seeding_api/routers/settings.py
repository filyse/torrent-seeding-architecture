import httpx
from fastapi import APIRouter

from seeding_api.deps import DbSession, EnginePoolDep
from seeding_api.net_policy import load_net_policy, save_net_policy
from seeding_api.schemas import NetSettingsIn, NetSettingsOut

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
