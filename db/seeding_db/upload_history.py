"""Сэмплы накопителя отдачи и rollup дельт по корзинам (день / неделя / месяц).

График рисует объём ЗА корзину, не бегущую сумму. Дельта:
`curr >= prev ? curr - prev : curr` — сброс счётчика (рестарт/перенос) берём как
новое значение, как в carry квот. Первый сэмпл ряда даёт дельту 0.

Ферма и WAN при чтении складываются из рядов движков: сброс одного движка не
раздувает дельту всей фермы.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

SCOPE_FARM = "farm"
SCOPE_WAN = "wan"
SCOPE_ENGINE = "engine"

PERIODS = ("day", "week", "month")

# sampled_at, scope, scope_id, uploaded
SampleRow = tuple[datetime, str, str, int]


def aware(t: datetime) -> datetime:
    if t.tzinfo is None:
        return t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


def counter_delta(prev: int | None, curr: int) -> int:
    """Дельта накопителя. Нет предыдущего — 0; сброс — новое значение целиком."""
    if prev is None:
        return 0
    curr = max(0, int(curr))
    prev = int(prev)
    return (curr - prev) if curr >= prev else curr


def merge_engine_totals(
    live: dict[str, int],
    last: dict[str, int],
    known_ids: set[str],
) -> dict[str, int]:
    """Офлайн-движок не пишем как 0 — это выглядело бы как сброс счётчика."""
    out = {eid: int(last[eid]) for eid in known_ids if eid in last}
    for eid, val in live.items():
        if eid in known_ids:
            out[eid] = int(val)
    return out


def build_sample_rows(
    sampled_at: datetime,
    engine_totals: dict[str, int],
    engine_wan: dict[str, str],
) -> list[SampleRow]:
    """Один проход: ферма, каждый WAN, каждый движок."""
    at = aware(sampled_at)
    rows: list[SampleRow] = []
    farm = 0
    wan_sums: dict[str, int] = {}
    for eid, uploaded in sorted(engine_totals.items()):
        value = max(0, int(uploaded))
        rows.append((at, SCOPE_ENGINE, eid, value))
        farm += value
        wan_id = engine_wan.get(eid, "")
        if wan_id:
            wan_sums[wan_id] = wan_sums.get(wan_id, 0) + value
    rows.append((at, SCOPE_FARM, "", farm))
    for wan_id, value in sorted(wan_sums.items()):
        rows.append((at, SCOPE_WAN, wan_id, value))
    return rows


def period_windows(
    period: str, now: datetime
) -> tuple[timedelta, list[datetime], list[datetime]]:
    """Корзины текущего окна и такого же предыдущего. `now` — UTC."""
    if period not in PERIODS:
        raise ValueError(f"unknown period: {period}")
    now = aware(now)
    if period == "day":
        step = timedelta(hours=1)
        count = 24
        end = now.replace(minute=0, second=0, microsecond=0)
    else:
        step = timedelta(days=1)
        count = 7 if period == "week" else 30
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - step * (count - 1)
    buckets = [start + step * i for i in range(count)]
    prev_start = start - step * count
    prev_buckets = [prev_start + step * i for i in range(count)]
    return step, buckets, prev_buckets


def rollup_series(
    points: list[tuple[datetime, int]],
    buckets: list[datetime],
    step: timedelta,
) -> list[int]:
    """Сумма дельт в каждую корзину. `points` по возрастанию времени.

    Точка раньше первой корзины — только база для следующей дельты.
    """
    out = [0] * len(buckets)
    if not buckets:
        return out
    origin = buckets[0]
    step_s = step.total_seconds() or 1
    prev_u: int | None = None
    for raw_t, raw_u in points:
        t = aware(raw_t)
        u = max(0, int(raw_u))
        delta = counter_delta(prev_u, u)
        prev_u = u
        if delta <= 0 or t < origin:
            continue
        idx = int((t - origin).total_seconds() // step_s)
        if 0 <= idx < len(buckets):
            out[idx] += delta
    return out


@dataclass(frozen=True)
class HistoryTotals:
    farm: int
    wan: dict[str, int]


@dataclass(frozen=True)
class HistoryBucket:
    t: datetime
    farm: int
    wan: dict[str, int]
    engines: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class UploadHistory:
    period: str
    buckets: list[HistoryBucket]
    total: HistoryTotals
    previous_total: HistoryTotals


def _group_engine_points(samples: list[SampleRow]) -> dict[str, list[tuple[datetime, int]]]:
    grouped: dict[str, list[tuple[datetime, int]]] = {}
    for sampled_at, scope, scope_id, uploaded in samples:
        if scope != SCOPE_ENGINE or not scope_id:
            continue
        grouped.setdefault(scope_id, []).append((aware(sampled_at), int(uploaded)))
    for pts in grouped.values():
        pts.sort(key=lambda p: p[0])
    return grouped


def _sum_maps(maps: list[dict[str, int]], keys: list[str]) -> dict[str, int]:
    out = {k: 0 for k in keys}
    for m in maps:
        for k in keys:
            out[k] += int(m.get(k, 0))
    return out


def history_from_samples(
    samples: list[SampleRow],
    period: str,
    now: datetime,
    wan_ids: list[str],
    engine_wan: dict[str, str],
) -> UploadHistory:
    step, buckets, prev_buckets = period_windows(period, now)
    grouped = _group_engine_points(samples)
    engine_ids = sorted(grouped)

    def pack(window: list[datetime]) -> tuple[list[dict[str, int]], list[int], list[dict[str, int]]]:
        engine_series = {
            eid: rollup_series(grouped[eid], window, step) for eid in engine_ids
        }
        n = len(window)
        engine_maps: list[dict[str, int]] = []
        farm_vals: list[int] = []
        wan_maps: list[dict[str, int]] = []
        for i in range(n):
            emap = {eid: engine_series[eid][i] for eid in engine_ids}
            engine_maps.append(emap)
            farm_vals.append(sum(emap.values()))
            wmap = {wid: 0 for wid in wan_ids}
            for eid, val in emap.items():
                wid = engine_wan.get(eid)
                if wid in wmap:
                    wmap[wid] += val
            wan_maps.append(wmap)
        return engine_maps, farm_vals, wan_maps

    cur_engines, cur_farm, cur_wan = pack(buckets)
    _prev_engines, prev_farm, prev_wan = pack(prev_buckets)

    history_buckets = [
        HistoryBucket(t=buckets[i], farm=cur_farm[i], wan=cur_wan[i], engines=cur_engines[i])
        for i in range(len(buckets))
    ]
    return UploadHistory(
        period=period,
        buckets=history_buckets,
        total=HistoryTotals(farm=sum(cur_farm), wan=_sum_maps(cur_wan, wan_ids)),
        previous_total=HistoryTotals(farm=sum(prev_farm), wan=_sum_maps(prev_wan, wan_ids)),
    )
