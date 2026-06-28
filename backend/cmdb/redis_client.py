"""Redis client for IS-CMDB placement data."""
import json
import redis
from typing import Optional, Dict
from django.conf import settings


def get_redis_client() -> redis.Redis:
    """Get Redis client instance."""
    redis_url = getattr(settings, 'REDIS_URL', 'redis://localhost:6379/0')
    return redis.from_url(redis_url, protocol=2)


def get_placement(env_name: str) -> Optional[Dict]:
    """
    Get placement data for an environment from Redis.

    Args:
        env_name: Environment name

    Returns:
        Placement data dict or None if not available
    """
    client = get_redis_client()

    # Try exact match first
    key = f"env:{env_name}:placement"
    try:
        data = client.get(key)
        if data:
            return json.loads(data)
    except (redis.RedisError, json.JSONDecodeError):
        pass

    # For PS5 environments, also try with ps5- prefix
    # (parser may have prefixed model names when no DB match found)
    if not env_name.startswith('ps5-'):
        key_with_prefix = f"env:ps5-{env_name}:placement"
        try:
            data = client.get(key_with_prefix)
            if data:
                return json.loads(data)
        except (redis.RedisError, json.JSONDecodeError):
            pass

    return None


def placement_map(names) -> Dict[str, Optional[Dict]]:
    """Fetch placement for many env names in one pipelined round trip.

    Returns ``{name: placement_dict_or_None}``. Mirrors get_placement's
    ``ps5-`` prefix fallback.
    """
    names = list(names)
    if not names:
        return {}
    client = get_redis_client()
    pipe = client.pipeline()
    for name in names:
        pipe.get(f"env:{name}:placement")
    fallback_pos = {}
    for i, name in enumerate(names):
        if not name.startswith('ps5-'):
            fallback_pos[i] = len(names) + len(fallback_pos)
            pipe.get(f"env:ps5-{name}:placement")
    try:
        results = pipe.execute()
    except redis.RedisError:
        return {n: None for n in names}

    out: Dict[str, Optional[Dict]] = {}
    for i, name in enumerate(names):
        raw = results[i]
        if raw is None and i in fallback_pos:
            raw = results[fallback_pos[i]]
        try:
            out[name] = json.loads(raw) if raw else None
        except (json.JSONDecodeError, TypeError):
            out[name] = None
    return out


def resilient_env_names() -> set:
    """Names of environments that are 'resilient'.

    Resilient = GitOps-managed AND more than 3 live VMs spread across more than
    one node. Only GitOps-managed envs can qualify, so we only fetch placement
    for those.
    """
    from cmdb.apps.environments.models import Environment

    gitops = list(
        Environment.objects.filter(gitops_managed=True).values_list('name', flat=True)
    )
    pmap = placement_map(gitops)
    out = set()
    for name in gitops:
        p = pmap.get(name)
        if p and (p.get('vm_count') or 0) > 3 and len(p.get('hosts') or []) > 1:
            out.add(name)
    return out


def ha_env_names() -> set:
    """Names of environments with more than 2 live VMs (the HA bar).

    Scans all placement keys (env:*:placement) and returns the names whose
    payload reports vm_count > 2.
    """
    client = get_redis_client()
    try:
        keys = list(client.scan_iter("env:*:placement"))
    except redis.RedisError:
        return set()
    if not keys:
        return set()
    pipe = client.pipeline()
    for k in keys:
        pipe.get(k)
    try:
        vals = pipe.execute()
    except redis.RedisError:
        return set()

    out = set()
    for k, raw in zip(keys, vals):
        if not raw:
            continue
        try:
            p = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if (p.get('vm_count') or 0) > 2:
            key = k.decode() if isinstance(k, bytes) else k
            out.add(key[len("env:"):-len(":placement")])
    return out


def cloud_has_placement(cloud_name: str) -> bool:
    """
    Check if a cloud has placement data available.

    Args:
        cloud_name: Cloud name (e.g., 'ps5', 'aws')

    Returns:
        True if cloud has placement data, False otherwise
    """
    client = get_redis_client()
    key = f"cloud:{cloud_name}:placement_available"

    try:
        value = client.get(key)
        if value:
            return value.decode('utf-8') == 'true'
    except redis.RedisError:
        pass

    return False
