"""
Management command to import environments from a CSV produced by parser/parser.py.

Usage:
    python manage.py import_csv environments.csv
    python manage.py import_csv environments.csv --dry-run
"""
import csv
import json
import logging
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from cmdb.apps.environments.models import Environment, EnvironmentDependency

logger = logging.getLogger(__name__)

ENV_TYPE_MAP = {
    'prod': 'prod',
    'production': 'prod',
    'staging': 'staging',
    'stg': 'staging',
    'dev': 'dev',
    'development': 'dev',
    'lab': 'lab',
}

SERVICE_PRIMITIVE_CHOICES = {'compute', 'iam', 'network', 'storage'}


def _str_or_none(val: str) -> str | None:
    return val.strip() if val and val.strip() else None


def _int_or_none(val: str) -> int | None:
    v = val.strip() if val else ''
    try:
        return int(v) if v else None
    except ValueError:
        return None


def _bool_or_none(val: str) -> bool | None:
    v = val.strip().lower() if val else ''
    if v in ('true', '1', 'yes'):
        return True
    if v in ('false', '0', 'no'):
        return False
    return None


def _compliance_scope(val: str) -> list:
    v = val.strip() if val else ''
    if not v:
        return []
    try:
        parsed = json.loads(v)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return [s.strip() for s in v.split(',') if s.strip()]


def _env_type(val: str) -> str:
    v = val.strip().lower() if val else ''
    return ENV_TYPE_MAP.get(v, v) or 'dev'


def _service_primitive(val: str) -> str | None:
    v = val.strip().lower() if val else ''
    return v if v in SERVICE_PRIMITIVE_CHOICES else None


def row_to_fields(row: dict) -> dict:
    criticality = _int_or_none(row.get('criticality_tier', ''))
    if criticality is not None and not (1 <= criticality <= 3):
        criticality = None

    return dict(
        git_path=row.get('git_path', '').strip() or f"services/definitions/{row['name']}.yaml",
        region=_str_or_none(row.get('region', '')),
        cloud=_str_or_none(row.get('cloud', '')),
        owner=_str_or_none(row.get('owner', '')),
        team=_str_or_none(row.get('team', '')),
        env_type=_env_type(row.get('env_type', '')),
        criticality_tier=criticality,
        data_classification=_str_or_none(row.get('data_classification', '')),
        compliance_scope=_compliance_scope(row.get('compliance_scope', '')),
        description=_str_or_none(row.get('description', '')),
        status=_str_or_none(row.get('status', '')) or 'active',
        service_primitive=_service_primitive(row.get('service_primitive', '')),
        service_class=_str_or_none(row.get('service_class', '')),
        juju_controller=_str_or_none(row.get('juju_controller', '')),
        juju_series=_str_or_none(row.get('juju_series', '')),
        juju_controller_stage=_str_or_none(row.get('juju_controller_stage', '')),
        bastion_server=_str_or_none(row.get('bastion_server', '')),
        risk_group=_str_or_none(row.get('risk_group', '')),
        cia_owner=_str_or_none(row.get('cia_owner', '')),
        cia_risk_owner=_str_or_none(row.get('cia_risk_owner', '')),
        cia_custodian=_str_or_none(row.get('cia_custodian', '')),
        slo_level=_str_or_none(row.get('slo_level', '')),
        slo_rto=_int_or_none(row.get('slo_rto', '')),
        live=_bool_or_none(row.get('live', '')),
    )


def parse_dependencies(row: dict) -> list[str]:
    raw = row.get('dependencies', '').strip()
    if not raw:
        return []
    return [d.strip() for d in raw.split(',') if d.strip()]


class Command(BaseCommand):
    help = 'Import environments from a CSV file produced by parser/parser.py'

    def add_arguments(self, parser):
        parser.add_argument('csv_file', type=Path, help='Path to environments CSV')
        parser.add_argument('--dry-run', action='store_true',
                            help='Parse and validate without writing to the database')

    def handle(self, *args, **options):
        csv_path: Path = options['csv_file']
        dry_run: bool = options['dry_run']

        if not csv_path.exists():
            raise CommandError(f'File not found: {csv_path}')

        rows = list(csv.DictReader(csv_path.open()))
        self.stdout.write(f'Read {len(rows)} rows from {csv_path}')

        env_fields: list[tuple[str, dict]] = []
        dep_edges: list[tuple[str, str]] = []
        errors: list[str] = []

        for i, row in enumerate(rows, start=2):  # row 1 is header
            name = row.get('name', '').strip()
            if not name:
                errors.append(f'Row {i}: missing name, skipped')
                continue
            try:
                fields = row_to_fields(row)
                env_fields.append((name, fields))
                for dep in parse_dependencies(row):
                    dep_edges.append((name, dep))
            except Exception as exc:
                errors.append(f'Row {i} ({name}): {exc}')

        for err in errors:
            self.stderr.write(self.style.WARNING(err))

        self.stdout.write(f'Parsed {len(env_fields)} environments, {len(dep_edges)} dependency edges')

        if dry_run:
            self.stdout.write(self.style.SUCCESS('Dry run — no changes written.'))
            return

        upserted = 0
        dep_created = 0

        with transaction.atomic():
            for name, fields in env_fields:
                _, created = Environment.objects.update_or_create(
                    name=name,
                    defaults=fields,
                )
                upserted += 1

            known_names = set(Environment.objects.values_list('name', flat=True))
            for env_name, dep_name in dep_edges:
                if dep_name not in known_names:
                    continue  # dangling reference — skip
                EnvironmentDependency.objects.get_or_create(
                    environment_name=env_name,
                    depends_on_name=dep_name,
                    defaults={'dependency_type': 'declared'},
                )
                dep_created += 1

        self.stdout.write(self.style.SUCCESS(
            f'Done. {upserted} environments upserted, {dep_created} dependency edges created.'
        ))
