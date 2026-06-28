"""API views for IS-CMDB."""
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db import connection
from django.conf import settings
from django.db.models import Sum, Count, Q
from django.core.cache import cache
import json
import os
from cmdb.apps.environments.models import Environment, CloudCapacity
from cmdb.redis_client import get_redis_client


@api_view(['GET'])
def health(request):
    """
    API health check endpoint.

    Returns system status and database connectivity.
    """
    # Check database connection
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            db_status = "healthy"
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"

    return Response({
        'status': 'healthy' if db_status == 'healthy' else 'degraded',
        'version': '1.0.0',
        'database': db_status,
        'debug': settings.DEBUG,
    })


@api_view(['GET'])
def team_resource_utilization(request):
    """
    Get team resource utilization percentages.

    Query params:
        - cloud: Filter by cloud (e.g., ps5, ps6)
        - region: Filter by region (e.g., amer, emea)
        - architecture: Filter by architecture (e.g., x86_64, arm64)

    Returns aggregated resource usage percentages for all teams.
    """
    # Get query parameters
    cloud_filter = request.GET.get('cloud')
    region_filter = request.GET.get('region')
    arch_filter = request.GET.get('architecture')

    # Build cache key
    cache_key = f"team_utilization:{cloud_filter or 'all'}:{region_filter or 'all'}:{arch_filter or 'all'}"

    # Try to get from cache
    cached_data = cache.get(cache_key)
    if cached_data:
        return Response(cached_data)

    # Get total capacity from CloudCapacity
    capacity_query = CloudCapacity.objects.all()
    if cloud_filter:
        capacity_query = capacity_query.filter(cloud_name=cloud_filter)
    if arch_filter:
        capacity_query = capacity_query.filter(architecture=arch_filter)

    total_capacity = capacity_query.aggregate(
        total_cpu=Sum('total_cpu_cores'),
        total_ram=Sum('total_ram_gb'),
        allocated_cpu=Sum('allocated_cpu_cores'),
        allocated_ram=Sum('allocated_ram_gb'),
    )

    # Get allocated resources from Redis placement data
    redis_client = get_redis_client()

    # Aggregate resources by team
    team_resources = {}

    # Build pattern based on filters
    if cloud_filter:
        pattern = f"env:{cloud_filter}-*:placement"
    else:
        pattern = "env:*:placement"

    for key in redis_client.scan_iter(pattern):
        data_str = redis_client.get(key)
        if not data_str:
            continue

        data = json.loads(data_str)
        architecture = data.get('architecture', 'x86_64')

        # Apply architecture filter
        if arch_filter and architecture != arch_filter:
            continue

        # Extract team from environment name
        env_name = data.get('environment_name', '')
        # Team is typically the first part after cloud prefix
        # e.g., ps5-prod-launchpad -> launchpad (simplified)
        # For more accurate team extraction, we'd query the Environment model
        team = _extract_team_from_env_name(env_name)

        if team not in team_resources:
            team_resources[team] = {
                'cpu': 0,
                'ram_gb': 0,
                'env_count': 0
            }

        team_resources[team]['cpu'] += data.get('total_vcpus', 0)
        team_resources[team]['ram_gb'] += data.get('total_ram_mb', 0) // 1024
        team_resources[team]['env_count'] += 1

    # Calculate percentages
    result = []
    for team, resources in team_resources.items():
        team_data = {
            'team': team,
            'resources': resources,
            'percentages': {}
        }

        if total_capacity['total_cpu'] and total_capacity['total_cpu'] > 0:
            team_data['percentages']['cpu_of_total'] = round(
                (resources['cpu'] / total_capacity['total_cpu']) * 100, 2
            )
        else:
            team_data['percentages']['cpu_of_total'] = 0

        if total_capacity['allocated_cpu'] and total_capacity['allocated_cpu'] > 0:
            team_data['percentages']['cpu_of_allocated'] = round(
                (resources['cpu'] / total_capacity['allocated_cpu']) * 100, 2
            )
        else:
            team_data['percentages']['cpu_of_allocated'] = 0

        if total_capacity['total_ram'] and total_capacity['total_ram'] > 0:
            team_data['percentages']['ram_of_total'] = round(
                (resources['ram_gb'] / total_capacity['total_ram']) * 100, 2
            )
        else:
            team_data['percentages']['ram_of_total'] = 0

        if total_capacity['allocated_ram'] and total_capacity['allocated_ram'] > 0:
            team_data['percentages']['ram_of_allocated'] = round(
                (resources['ram_gb'] / total_capacity['allocated_ram']) * 100, 2
            )
        else:
            team_data['percentages']['ram_of_allocated'] = 0

        result.append(team_data)

    # Sort by CPU usage descending
    result.sort(key=lambda x: x['resources']['cpu'], reverse=True)

    response_data = {
        'teams': result,
        'total_capacity': total_capacity,
        'filters': {
            'cloud': cloud_filter,
            'region': region_filter,
            'architecture': arch_filter
        }
    }

    # Cache for 5 minutes
    cache.set(cache_key, response_data, 300)

    return Response(response_data)


def _extract_team_from_env_name(env_name):
    """
    Extract team name from environment name.

    Examples:
        ps5-prod-launchpad -> launchpad
        ps5-stg-snapstore-logging -> snapstore
        ps5-comsys-data-warehouse -> comsys
    """
    parts = env_name.split('-')
    if len(parts) < 3:
        return 'unknown'

    # Remove cloud prefix (ps5, ps6, etc.)
    if parts[0] in ['ps5', 'ps6', 'ps7', 'ps8', 'aws', 'gcp', 'azure']:
        parts = parts[1:]

    # Remove env type (prod, stg, dev)
    if parts[0] in ['prod', 'stg', 'staging', 'dev', 'dbaas']:
        parts = parts[1:]

    # First remaining part is usually the team
    return parts[0] if parts else 'unknown'
