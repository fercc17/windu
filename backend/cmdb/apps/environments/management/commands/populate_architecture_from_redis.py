"""
Django management command to populate architecture field from Redis placement data.
"""
from django.core.management.base import BaseCommand
import json
from cmdb.apps.environments.models import Environment
from cmdb.redis_client import get_redis_client


class Command(BaseCommand):
    help = 'Populate architecture field in Environment model from Redis placement data'

    def handle(self, *args, **options):
        # Connect to Redis
        redis_client = get_redis_client()

        updated_count = 0
        skipped_count = 0

        # Get all placement keys
        for key in redis_client.scan_iter("env:*:placement"):
            data_str = redis_client.get(key)
            if not data_str:
                continue

            data = json.loads(data_str)
            env_name = data.get('environment_name')
            architecture = data.get('architecture', 'x86_64')
            cpu = data.get('total_vcpus', 0)
            ram_mb = data.get('total_ram_mb', 0)

            # Redis keys have cloud prefix (e.g., ps5-prod-launchpad)
            # but database names don't (e.g., prod-launchpad)
            # Try to find matching environment by removing cloud prefix
            env_name_variations = [env_name]

            # Try removing cloud prefixes
            for cloud in ['ps5', 'ps6', 'ps7', 'ps8', 'aws', 'gcp', 'azure']:
                if env_name.startswith(f'{cloud}-'):
                    env_name_variations.append(env_name.replace(f'{cloud}-', '', 1))

            # Try to find matching environment
            env = None
            for name_variant in env_name_variations:
                try:
                    env = Environment.objects.get(name=name_variant)
                    break
                except Environment.DoesNotExist:
                    continue

            if not env:
                skipped_count += 1
                continue

            # Update architecture and quota fields
            updated = False
            if not env.architecture:
                env.architecture = architecture
                updated = True

            if not env.quota_cpu_cores and cpu > 0:
                env.quota_cpu_cores = cpu
                updated = True

            if not env.quota_ram_mb and ram_mb > 0:
                env.quota_ram_mb = ram_mb
                updated = True

            if updated:
                env.save()
                updated_count += 1
                self.stdout.write(f"  ✓ Updated {env.name}: {architecture}, {cpu} cores, {ram_mb} MB RAM")
            else:
                skipped_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'\n✅ Complete: Updated {updated_count} environments, '
                f'skipped {skipped_count}'
            )
        )
