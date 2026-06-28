"""
Django management command to update cloud_capacity table.

Calculates total available and allocated resources per cloud and architecture
from placement data in Redis and environment data in PostgreSQL.
"""
from django.core.management.base import BaseCommand
from django.db.models import Sum, Count
import json
from cmdb.apps.environments.models import CloudCapacity, Environment
from cmdb.redis_client import get_redis_client


class Command(BaseCommand):
    help = 'Update cloud_capacity table with current resource totals'

    def add_arguments(self, parser):
        parser.add_argument(
            '--cloud',
            type=str,
            help='Update specific cloud only (e.g., ps5, ps6)',
        )

    def handle(self, *args, **options):
        # Connect to Redis
        redis_client = get_redis_client()

        clouds_to_process = []
        if options['cloud']:
            clouds_to_process = [options['cloud']]
        else:
            # Get all unique clouds from placement keys
            clouds_to_process = self._get_clouds_from_redis(redis_client)

        self.stdout.write(f"Processing {len(clouds_to_process)} clouds...")

        for cloud in clouds_to_process:
            self.stdout.write(f"\n📊 Processing cloud: {cloud}")
            self._update_cloud_capacity(redis_client, cloud)

        self.stdout.write(self.style.SUCCESS('\n✅ Cloud capacity update complete'))

    def _get_clouds_from_redis(self, redis_client):
        """Get list of clouds that have placement data."""
        clouds = set()
        # Check for cloud:*:placement_available keys
        for key in redis_client.scan_iter("cloud:*:placement_available"):
            key_str = key.decode('utf-8') if isinstance(key, bytes) else key
            parts = key_str.split(':')
            if len(parts) >= 2:
                clouds.add(parts[1])
        return list(clouds)

    def _update_cloud_capacity(self, redis_client, cloud):
        """Update capacity for a specific cloud."""
        # Get all placement keys for this cloud
        pattern = f"env:{cloud}-*:placement"

        # Aggregate by architecture
        arch_totals = {}

        for key in redis_client.scan_iter(pattern):
            data_str = redis_client.get(key)
            if not data_str:
                continue

            data = json.loads(data_str)
            architecture = data.get('architecture', 'x86_64')

            if architecture not in arch_totals:
                arch_totals[architecture] = {
                    'total_vcpus': 0,
                    'total_ram_mb': 0,
                    'env_count': 0
                }

            arch_totals[architecture]['total_vcpus'] += data.get('total_vcpus', 0)
            arch_totals[architecture]['total_ram_mb'] += data.get('total_ram_mb', 0)
            arch_totals[architecture]['env_count'] += 1

        # For PS5, we can estimate total capacity based on observed usage
        # In a real scenario, you'd get this from cloud APIs or config
        # For now, we'll assume allocated = what we observe, and total = 2x allocated
        # (This is a simplification - real capacity data should come from cloud provider)

        for architecture, totals in arch_totals.items():
            allocated_cpu = totals['total_vcpus']
            allocated_ram_gb = totals['total_ram_mb'] // 1024
            allocated_storage_gb = 0  # Not tracked in PS5 data yet

            # Estimate total capacity (this should be replaced with real data)
            # For demo purposes, assuming 50% utilization target
            total_cpu = int(allocated_cpu * 2)
            total_ram_gb = int(allocated_ram_gb * 2)
            total_storage_gb = 0

            # Update or create CloudCapacity record
            capacity, created = CloudCapacity.objects.update_or_create(
                cloud_name=cloud,
                architecture=architecture,
                defaults={
                    'total_cpu_cores': total_cpu,
                    'total_ram_gb': total_ram_gb,
                    'total_storage_gb': total_storage_gb,
                    'allocated_cpu_cores': allocated_cpu,
                    'allocated_ram_gb': allocated_ram_gb,
                    'allocated_storage_gb': allocated_storage_gb,
                }
            )

            action = 'Created' if created else 'Updated'
            self.stdout.write(
                f"  {action} {cloud}/{architecture}: "
                f"{allocated_cpu}/{total_cpu} cores, "
                f"{allocated_ram_gb}/{total_ram_gb} GB RAM, "
                f"{totals['env_count']} environments"
            )
