"""Django tables for environments."""
import re
from urllib.parse import quote

import django_tables2 as tables
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from .models import Environment


class EnvironmentTable(tables.Table):
    """Main environment list table."""

    def __init__(self, *args, under_maintenance_ids=None, **kwargs):
        # ids of environments whose node is under maintenance (#38)
        self.under_maintenance_ids = under_maintenance_ids or set()
        super().__init__(*args, **kwargs)

    name = tables.Column(
        verbose_name='Juju model',
        linkify=('environment-detail', {'name': tables.A('name')}),
        attrs={'td': {'class': 'font-mono text-sm'}}
    )

    env_type = tables.Column(verbose_name='Type')
    criticality_tier = tables.Column(verbose_name='Tier')
    status = tables.Column()
    # Placement comes from Redis; the list view stamps a ``placement_sort``
    # attribute on each row and feeds the table a list so django-tables2 can
    # order it in memory by that key (see EnvironmentListView.get_table_data).
    placement_status = tables.Column(verbose_name='Placement', orderable=True,
                                     order_by=('placement_sort',), empty_values=())
    # "Managed by" is the owning/providing team (owner). "Consumed by" is the
    # real consuming team (consumer_team), falling back to team until that is
    # populated. owner and team are synonyms in the parser, so Consumed-by only
    # diverges from Managed-by once consumer_team is set.
    managed_by = tables.Column(accessor='owner', verbose_name='Managed by',
                               order_by=('owner',), empty_values=())
    consumed_by = tables.Column(accessor='consumer_team', verbose_name='Consumed by',
                                order_by=('consumed_by_sort',), empty_values=())
    cloud = tables.Column()
    # empty_values=() so render_host_aggregate runs even when the value is
    # None (it defaults PS7 to 'production').
    host_aggregate = tables.Column(verbose_name='Host aggr', empty_values=())
    gitops = tables.Column(accessor='gitops_managed', verbose_name='GitOps',
                           order_by=('gitops_managed', 'gitops_repo'))
    cached_depends_on = tables.Column(verbose_name='Depends on')
    cached_dependents_count = tables.Column(verbose_name='Dependants')

    # Live VM count, HA and resilience flags (all from Redis placement). Sorted
    # in memory via keys stamped by EnvironmentListView.get_table_data.
    vms = tables.Column(verbose_name='VMs', orderable=True,
                        order_by=('vms_sort',), empty_values=())
    ha = tables.Column(verbose_name='HA', orderable=True,
                       order_by=('ha_sort',), empty_values=())
    resilient = tables.Column(verbose_name='Resilient', orderable=True,
                              order_by=('resilient_sort',), empty_values=())

    # Quota split into separate, individually-sortable columns. Each sorts by a
    # numeric annotation added in EnvironmentListView (sort_cores/ram/disk).
    quota_vcpu = tables.Column(accessor='quotas', verbose_name='Quota vCPU',
                               orderable=True, order_by=('sort_cores',), empty_values=())
    quota_ram = tables.Column(accessor='quotas', verbose_name='Quota RAM',
                              orderable=True, order_by=('sort_ram',), empty_values=())
    quota_disk = tables.Column(accessor='quotas', verbose_name='Quota Disk',
                               orderable=True, order_by=('sort_disk',), empty_values=())

    updated_at = tables.TemplateColumn('{{ value|utclocal }}', verbose_name='Last updated',
                                       order_by='updated_at')

    def render_env_type(self, value):
        """Render env_type as a badge."""
        badge_class = {
            'prod': 'badge-danger',
            'staging': 'badge-warning',
            'dev': 'badge-info',
            'lab': 'badge-secondary',
        }.get(value, 'badge-secondary')

        return format_html(
            '<span class="badge {}">{}</span>',
            badge_class,
            value or 'unknown'
        )

    def render_criticality_tier(self, value):
        """Render criticality_tier as a badge."""
        if value is None:
            return format_html('<span class="badge badge-light">-</span>')

        badge_class = {
            1: 'badge-danger',
            2: 'badge-warning',
            3: 'badge-secondary',
        }.get(value, 'badge-secondary')

        label = {
            1: 'Tier 1 (Critical)',
            2: 'Tier 2 (Important)',
            3: 'Tier 3 (Best effort)',
        }.get(value, f'Tier {value}')

        return format_html(
            '<span class="badge {}">{}</span>',
            badge_class,
            label
        )

    def render_status(self, value, record):
        """Render status as a badge, plus an amber maintenance badge (#38)."""
        badge_class = {
            'active': 'badge-success',
            'provisioning': 'badge-info',
            'maintenance': 'badge-warning',
            'degraded': 'badge-danger',
            'decommissioning': 'badge-secondary',
            'archived': 'badge-dark',
        }.get(str(value).lower(), 'badge-secondary')  # value may be the title-cased display label

        html = format_html('<span class="badge {}">{}</span>', badge_class, value)
        if record.id in self.under_maintenance_ids:
            html += format_html(' <span class="badge badge-warning" title="Node under maintenance">🔧 maint</span>')
        return html

    _DASH = mark_safe('<span class="text-muted">—</span>')

    def render_quota_vcpu(self, record):
        v = (record.quotas or {}).get('cores')
        if not isinstance(v, (int, float)):
            return self._DASH
        return format_html('{}', f'{v:g}')

    def render_quota_ram(self, record):
        v = (record.quotas or {}).get('ram')  # stored in MB
        if not isinstance(v, (int, float)):
            return self._DASH
        if v >= 1024:
            return format_html('{} GB', f'{v / 1024:.0f}')
        return format_html('{} MB', f'{v:g}')

    def render_quota_disk(self, record):
        v = (record.quotas or {}).get('gigabytes')
        if not isinstance(v, (int, float)):
            return self._DASH
        return format_html('{} GB', f'{v:g}')

    @staticmethod
    def _short_node(host, cloud):
        """Drop the cloud prefix from a node name: ps5-ra2-n1 -> ra2-n1."""
        short = (host or '').split('.')[0]
        if cloud and short.startswith(cloud + '-'):
            return short[len(cloud) + 1:]
        return re.sub(r'^ps\d+-', '', short)

    def render_vms(self, record):
        """Number of live VMs (from Redis placement)."""
        placement = getattr(self, 'placement_by_name', {}).get(record.name)
        vm_count = (placement or {}).get('vm_count')
        if not vm_count:
            return self._DASH
        return format_html('{}', vm_count)

    def render_ha(self, record):
        """Yes (green) when the env has more than 2 live VMs, else No (red)."""
        placement = getattr(self, 'placement_by_name', {}).get(record.name) or {}
        if (placement.get('vm_count') or 0) > 2:
            return format_html('<span class="badge badge-success">Yes</span>')
        return format_html('<span class="badge badge-danger">No</span>')

    def render_resilient(self, record):
        """Yes when GitOps-managed AND >3 VMs spread across more than one node."""
        placement = getattr(self, 'placement_by_name', {}).get(record.name) or {}
        vm_count = placement.get('vm_count') or 0
        node_count = len(placement.get('hosts') or [])
        resilient = bool(record.gitops_managed) and vm_count > 3 and node_count > 1
        if resilient:
            return format_html('<span class="badge badge-success">Yes</span>')
        return format_html('<span class="badge badge-danger">No</span>')

    def render_managed_by(self, record):
        return record.owner or self._DASH

    def render_consumed_by(self, record):
        return record.consumed_by or self._DASH

    def render_host_aggregate(self, value, record):
        # PS7 nodes are all production: default the aggregate to 'production'
        # when none is recorded. Explicit aggregates are left untouched.
        if not value and record.cloud == 'ps7':
            value = 'production'
        if not value:
            return format_html('<span class="text-muted">—</span>')
        return format_html('<span class="badge badge-info">{}</span>', value)

    def render_gitops(self, record):
        """GitOps badge: green if managed, amber if suspended, red 'No' if not."""
        if not record.gitops_managed:
            return format_html('<span class="badge badge-danger">No</span>')
        cls = 'badge-warning' if record.gitops_suspended else 'badge-success'
        title = record.gitops_repo or 'GitOps'
        label = 'GitOps' + (' ⏸' if record.gitops_suspended else '')
        return format_html('<span class="badge {}" title="{}">{}</span>', cls, title, label)

    def render_cached_depends_on(self, value):
        """Show the depended-on model name(s), truncated if many."""
        if not value:
            return format_html('<span class="text-muted">—</span>')
        names = [n.strip() for n in value.split(',') if n.strip()]
        if len(names) == 1:
            return format_html('<span class="font-mono text-sm">{}</span>', names[0])
        return format_html(
            '<span class="font-mono text-sm" title="{}">{} <span class="badge badge-light">+{}</span></span>',
            value, names[0], len(names) - 1,
        )

    def render_cached_dependents_count(self, value):
        if not value:
            return format_html('<span class="text-muted">0</span>')
        return format_html('<span class="badge badge-secondary">{}</span>', value)

    def render_placement_status(self, record):
        """Live placement: the node(s) the env runs on (up to 3, cloud prefix
        dropped, e.g. ps5-ra2-n1 -> ra2-n1). Full node list on hover.

        ``placement_by_name`` (name -> placement dict, from Redis) is attached
        per page by the list view.
        """
        placement = getattr(self, 'placement_by_name', {}).get(record.name)
        if not placement:
            return format_html('<span class="badge badge-secondary">—</span>')

        hosts = placement.get('hosts') or [
            h for h in (placement.get('primary_host'), placement.get('secondary_host')) if h
        ]
        if not hosts:
            return format_html('<span class="badge badge-secondary">—</span>')

        cloud = placement.get('cloud') or ''
        short = [self._short_node(h, cloud) for h in hosts]
        text = ', '.join(short[:3])
        remaining = len(short) - 3
        title = ', '.join(hosts)
        if remaining > 0:
            return format_html(
                '<span class="font-mono text-sm" title="{}">{} '
                '<span class="badge badge-light">+{}</span></span>',
                title, text, remaining,
            )
        return format_html('<span class="font-mono text-sm" title="{}">{}</span>', title, text)

    class Meta:
        model = Environment
        fields = (
            'name',
            'status',
            'criticality_tier',
            'env_type',
            'cloud',
            'host_aggregate',
            'placement_status',
            'vms',
            'ha',
            'resilient',
            'managed_by',
            'consumed_by',
            'gitops',
            'quota_vcpu',
            'quota_ram',
            'quota_disk',
            'cached_depends_on',
            'cached_dependents_count',
            'updated_at',
        )
        attrs = {
            'class': 'table table-striped table-hover table-sm',
            'thead': {'class': 'thead-light'}
        }
        # Default row order (most recently updated) is applied on the queryset
        # in EnvironmentListView; there is no Updated-at column to order by here.
        per_page = 50
        template_name = 'django_tables2/bootstrap4.html'
        orderable = True  # Enable sorting on all columns by default


class TeamAggregationTable(tables.Table):
    """Team resource aggregation table."""

    def __init__(self, *args, extra_query='', **kwargs):
        # querystring of the active teams-page filters (region/cloud/arch),
        # carried into the per-team drill-down links.
        self.extra_query = extra_query
        super().__init__(*args, **kwargs)

    team = tables.Column(verbose_name='Team')
    total_envs = tables.Column(verbose_name='Environments')
    cores = tables.Column(verbose_name='Cores')
    ram_gb = tables.Column(verbose_name='RAM (GB)')
    instances = tables.Column(verbose_name='Instances')
    gigabytes = tables.Column(verbose_name='Storage (GB)')
    volumes = tables.Column(verbose_name='Volumes')
    regions_display = tables.Column(verbose_name='Regions', orderable=False)
    clouds_display = tables.Column(verbose_name='Clouds', orderable=False)
    types_display = tables.Column(verbose_name='Types', orderable=False)

    def render_team(self, record):
        """Link the team to its filtered environments via the "Managed by"
        (owner_exact) filter. The synthetic 'Unknown' team (envs with no team)
        routes to the "(unknown)" sentinel so the drill-down works."""
        t = record['team']
        if t == 'Unknown':
            href = '/?owner_exact=__unknown__' + self.extra_query
        else:
            href = '/?owner_exact=' + quote(str(t)) + self.extra_query
        return format_html('<strong><a href="{}">{}</a></strong>', href, t)

    def render_cores(self, record):
        """Format cores."""
        return f"{record['cores']:.0f}"

    def render_ram_gb(self, record):
        """Format RAM in GB."""
        return f"{record['ram_gb']:.0f}"

    def render_instances(self, record):
        """Format instances."""
        return f"{record['instances']:.0f}"

    def render_gigabytes(self, record):
        """Format storage."""
        return f"{record['gigabytes']:.0f}"

    def render_volumes(self, record):
        """Format volumes."""
        return f"{record['volumes']:.0f}"

    def render_regions_display(self, record):
        """Render region breakdown as badges."""
        badges = []
        for region, count in record.get('by_region', {}).items():
            badges.append(
                format_html('<span class="badge badge-secondary">{}: {}</span>',
                           region, count)
            )
        if not badges:
            return '-'
        badges_html = ' '.join(str(b) for b in badges)
        return mark_safe(f'<small>{badges_html}</small>')

    def render_clouds_display(self, record):
        """Render cloud breakdown as badges."""
        badges = []
        for cloud, count in record.get('by_cloud', {}).items():
            badges.append(
                format_html('<span class="badge badge-info">{}: {}</span>',
                           cloud, count)
            )
        if not badges:
            return '-'
        badges_html = ' '.join(str(b) for b in badges)
        return mark_safe(f'<small>{badges_html}</small>')

    def render_types_display(self, record):
        """Render environment type breakdown as badges."""
        badges = []
        for env_type, count in record.get('by_env_type', {}).items():
            if env_type == 'prod':
                badge_class = 'badge-danger'
            elif env_type == 'staging':
                badge_class = 'badge-warning'
            else:
                badge_class = 'badge-secondary'

            badges.append(
                format_html('<span class="badge {}">{}: {}</span>',
                           badge_class, env_type, count)
            )
        if not badges:
            return '-'
        badges_html = ' '.join(str(b) for b in badges)
        return mark_safe(f'<small>{badges_html}</small>')

    class Meta:
        fields = (
            'team',
            'total_envs',
            'cores',
            'ram_gb',
            'instances',
            'gigabytes',
            'volumes',
            'regions_display',
            'clouds_display',
            'types_display',
        )
        attrs = {
            'class': 'table table-striped table-hover table-sm',
            'thead': {'class': 'thead-light'}
        }
        order_by = 'team'  # Default sort by team name
        per_page = 50
        template_name = 'django_tables2/bootstrap4.html'
        orderable = True


class CloudRegionTable(tables.Table):
    """Cloud/Region capacity aggregation table."""

    cloud_region = tables.Column(verbose_name='Cloud / Region')
    total_envs = tables.Column(verbose_name='Environments')
    cores = tables.Column(verbose_name='Cores')
    ram_gb = tables.Column(verbose_name='RAM (GB)')
    instances = tables.Column(verbose_name='Instances')
    gigabytes = tables.Column(verbose_name='Storage (GB)')
    volumes = tables.Column(verbose_name='Volumes')
    prod_count = tables.Column(verbose_name='Production')
    staging_count = tables.Column(verbose_name='Staging')

    def render_cloud_region(self, record):
        """Render cloud/region as a link."""
        cloud = record['cloud']
        region = record['region']
        return format_html('<strong>{} / {}</strong>', cloud, region)

    def render_cores(self, record):
        """Format cores."""
        return f"{record['cores']:.0f}"

    def render_ram_gb(self, record):
        """Format RAM in GB."""
        return f"{record['ram_gb']:.0f}"

    def render_instances(self, record):
        """Format instances."""
        return f"{record['instances']:.0f}"

    class Meta:
        attrs = {
            'class': 'table table-striped table-hover table-sm',
            'thead': {'class': 'thead-light'}
        }
        order_by = 'cloud_region'
        per_page = 50
        template_name = 'django_tables2/bootstrap4.html'
        orderable = True


class ControllerTable(tables.Table):
    """Juju controller aggregation table."""

    juju_controller = tables.Column(verbose_name='Controller')
    version = tables.Column(verbose_name='Juju version')
    stage = tables.Column(verbose_name='Stage')
    cloud = tables.Column(verbose_name='Cloud')
    total_envs = tables.Column(verbose_name='Models')
    active_count = tables.Column(verbose_name='Active')
    degraded_count = tables.Column(verbose_name='Degraded')
    tier1_count = tables.Column(verbose_name='Tier 1')
    depended_on_count = tables.Column(verbose_name='Total Blast Radius')

    def render_juju_controller(self, record):
        """Render controller name as a link to the models running on it."""
        return format_html('<strong><a href="/?juju_controller={}" title="List the juju models on this controller">{}</a></strong>',
                          record['juju_controller'], record['juju_controller'])

    def render_degraded_count(self, record):
        """Highlight degraded count if > 0."""
        count = record['degraded_count']
        if count > 0:
            return format_html('<span class="badge badge-danger">{}</span>', count)
        return count

    def render_tier1_count(self, record):
        """Highlight tier 1 count."""
        count = record['tier1_count']
        if count > 0:
            return format_html('<span class="badge badge-danger">{}</span>', count)
        return count

    class Meta:
        attrs = {
            'class': 'table table-striped table-hover table-sm',
            'thead': {'class': 'thead-light'}
        }
        order_by = '-total_envs'
        per_page = 50
        template_name = 'django_tables2/bootstrap4.html'
        orderable = True


class DependencyHotspotTable(tables.Table):
    """Dependency hotspot ranking table."""

    environment_name = tables.Column(
        verbose_name='Environment',
        linkify=('environment-detail', {'name': tables.A('environment_name')})
    )
    depended_on_count = tables.Column(verbose_name='Depended On By')
    total_blast_radius = tables.Column(verbose_name='Total Blast Radius')
    criticality_tier = tables.Column(verbose_name='Tier')
    env_type = tables.Column(verbose_name='Type')
    status = tables.Column(verbose_name='Status')
    owner = tables.Column(verbose_name='Owner')

    def render_environment_name(self, record):
        """Render environment name as link."""
        return format_html('<strong><a href="/model/{}/">{}</a></strong>',
                          record['environment_name'], record['environment_name'])

    def render_depended_on_count(self, record):
        """Highlight high dependency counts."""
        count = record['depended_on_count']
        if count >= 10:
            return format_html('<span class="badge badge-danger">{}</span>', count)
        elif count >= 5:
            return format_html('<span class="badge badge-warning">{}</span>', count)
        return count

    def render_total_blast_radius(self, record):
        """Highlight high blast radius."""
        count = record['total_blast_radius']
        if count >= 20:
            return format_html('<span class="badge badge-danger">{}</span>', count)
        elif count >= 10:
            return format_html('<span class="badge badge-warning">{}</span>', count)
        return count

    def render_criticality_tier(self, value):
        """Render criticality_tier as a badge."""
        if value is None:
            return format_html('<span class="badge badge-light">-</span>')

        badge_class = {
            1: 'badge-danger',
            2: 'badge-warning',
            3: 'badge-secondary',
        }.get(value, 'badge-secondary')

        return format_html('<span class="badge {}">T{}</span>', badge_class, value)

    def render_env_type(self, value):
        """Render env_type as a badge."""
        badge_class = {
            'prod': 'badge-danger',
            'staging': 'badge-warning',
            'dev': 'badge-info',
        }.get(value, 'badge-secondary')
        return format_html('<span class="badge {}">{}</span>', badge_class, value or '-')

    def render_status(self, value):
        """Render status as a badge."""
        badge_class = {
            'active': 'badge-success',
            'degraded': 'badge-danger',
            'maintenance': 'badge-warning',
        }.get(value, 'badge-secondary')
        return format_html('<span class="badge {}">{}</span>', badge_class, value or '-')

    class Meta:
        attrs = {
            'class': 'table table-striped table-hover table-sm',
            'thead': {'class': 'thead-light'}
        }
        order_by = '-total_blast_radius'
        per_page = 50
        template_name = 'django_tables2/bootstrap4.html'
        orderable = True


class VersionComplianceTable(tables.Table):
    """Version distribution table."""

    version_type = tables.Column(verbose_name='Type')
    version_value = tables.Column(verbose_name='Version')
    count = tables.Column(verbose_name='Environments')
    tier1_count = tables.Column(verbose_name='Tier 1')
    prod_count = tables.Column(verbose_name='Production')

    def render_version_type(self, record):
        """Render version type."""
        return format_html('<strong>{}</strong>', record['version_type'])

    def render_tier1_count(self, record):
        """Highlight tier 1 environments."""
        count = record['tier1_count']
        if count > 0:
            return format_html('<span class="badge badge-danger">{}</span>', count)
        return count

    class Meta:
        attrs = {
            'class': 'table table-striped table-hover table-sm',
            'thead': {'class': 'thead-light'}
        }
        order_by = '-count'
        per_page = 100
        template_name = 'django_tables2/bootstrap4.html'
        orderable = True


class ServicePrimitiveTable(tables.Table):
    """Service primitive inventory table."""

    service_primitive = tables.Column(verbose_name='Service Primitive')
    service_class = tables.Column(verbose_name='Service Class')
    total_envs = tables.Column(verbose_name='Environments')
    prod_count = tables.Column(verbose_name='Production')
    tier1_count = tables.Column(verbose_name='Tier 1')
    regions = tables.Column(verbose_name='Regions', orderable=False)

    def render_service_primitive(self, record):
        """Render service primitive."""
        return format_html('<strong>{}</strong>', record['service_primitive'] or '-')

    def render_regions(self, record):
        """Render region breakdown."""
        regions_dict = record.get('regions', {})
        badges = []
        for region, count in regions_dict.items():
            badges.append(
                format_html('<span class="badge badge-secondary">{}: {}</span>',
                           region, count)
            )
        if not badges:
            return '-'
        badges_html = ' '.join(str(b) for b in badges)
        return mark_safe(f'<small>{badges_html}</small>')

    class Meta:
        attrs = {
            'class': 'table table-striped table-hover table-sm',
            'thead': {'class': 'thead-light'}
        }
        order_by = '-total_envs'
        per_page = 50
        template_name = 'django_tables2/bootstrap4.html'
        orderable = True
