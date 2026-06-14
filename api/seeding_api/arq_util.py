from urllib.parse import urlparse

from arq.connections import RedisSettings


def redis_settings_from_url(url: str) -> RedisSettings:
    u = urlparse(url)
    host = u.hostname or "localhost"
    port = u.port or 6379
    return RedisSettings(host=host, port=port, password=u.password)
