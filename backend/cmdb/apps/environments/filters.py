"""Django filters for environments."""
import django_filters
from django.db.models import Q

from .models import Environment

# owner_exact sentinel for "no team" environments (owner == team in the data).
UNKNOWN_OWNER = '__unknown__'


class EnvironmentFilter(django_filters.FilterSet):
    """Filters for environment list view."""

    name = django_filters.CharFilter(
        lookup_expr='icontains',
        label='Name contains'
    )

    owner_exact = django_filters.ChoiceFilter(
        field_name='owner',
        choices=[],  # Will be populated in __init__ (incl. the "(unknown)" sentinel)
        empty_label='All (managed by)',
        label='Managed by',
        method='filter_owner_exact',
    )

    def filter_owner_exact(self, queryset, name, value):
        """Exact managed-by (== team) match, plus a synthetic "(unknown / no
        team)" option for environments with no owner/team set."""
        if not value:
            return queryset
        if value == UNKNOWN_OWNER:
            return queryset.filter(Q(owner__isnull=True) | Q(owner=''))
        return queryset.filter(owner=value)

    consumed_by = django_filters.ChoiceFilter(
        choices=[],  # Will be populated in __init__
        empty_label='All (consumed by)',
        method='filter_consumed_by',
        label='Consumed by'
    )

    def filter_consumed_by(self, queryset, name, value):
        """Consuming team = requester (real consumer for aaS), else owning team."""
        if not value:
            return queryset
        return queryset.filter(
            Q(requester__iexact=value)
            | (Q(requester__isnull=True) & Q(team__iexact=value))
            | (Q(requester='') & Q(team__iexact=value))
        )

    env_type = django_filters.ChoiceFilter(
        choices=Environment.ENV_TYPE_CHOICES,
        empty_label='All types',
        label='Environment Type'
    )

    status = django_filters.ChoiceFilter(
        choices=Environment.STATUS_CHOICES,
        empty_label='All statuses',
        label='Status'
    )

    criticality_tier = django_filters.ChoiceFilter(
        choices=[
            (1, 'Tier 1 (Critical)'),
            (2, 'Tier 2 (Important)'),
            (3, 'Tier 3 (Best effort)'),
        ],
        empty_label='All tiers',
        label='Criticality Tier'
    )

    cloud = django_filters.ChoiceFilter(
        choices=[],  # Will be populated in __init__
        empty_label='All clouds',
        label='Cloud'
    )

    region = django_filters.ChoiceFilter(
        choices=[
            ('amer', 'AMER'),
            ('emea', 'EMEA'),
            ('apac', 'APAC'),
        ],
        empty_label='All regions',
        label='Region'
    )

    service_primitive = django_filters.ChoiceFilter(
        choices=Environment.SERVICE_PRIMITIVE_CHOICES,
        empty_label='All primitives',
        label='Service Primitive'
    )

    compute_architecture = django_filters.ChoiceFilter(
        choices=[],  # Will be populated in __init__
        empty_label='All architectures',
        label='Architecture'
    )

    charm = django_filters.CharFilter(
        method='filter_charm',
        label='Charm Name'
    )

    charm_version = django_filters.CharFilter(
        method='filter_charm_version',
        label='Charm Version'
    )

    # CIA Assessment filters
    cia_owner = django_filters.CharFilter(
        lookup_expr='icontains',
        label='CIA Owner'
    )

    cia_custodian = django_filters.CharFilter(
        lookup_expr='icontains',
        label='CIA Custodian'
    )

    slo_level = django_filters.ChoiceFilter(
        choices=[],  # Will be populated in __init__
        empty_label='All SLO levels',
        label='SLO Level'
    )

    gitops = django_filters.ChoiceFilter(
        field_name='gitops_managed',
        choices=[('yes', 'GitOps-managed'), ('no', 'Not GitOps-managed')],
        empty_label='All (GitOps + not)',
        method='filter_gitops',
        label='GitOps'
    )

    resilient = django_filters.ChoiceFilter(
        choices=[('yes', 'Resilient'), ('no', 'Not resilient')],
        empty_label='All (resilient + not)',
        method='filter_resilient',
        label='Resilient'
    )

    def filter_resilient(self, queryset, name, value):
        """Filter by the (Redis-derived) resilient flag: GitOps-managed AND
        >3 VMs across >1 node."""
        if value not in ('yes', 'no'):
            return queryset
        from cmdb.redis_client import resilient_env_names
        names = resilient_env_names()
        if value == 'yes':
            return queryset.filter(name__in=names)
        return queryset.exclude(name__in=names)

    ha = django_filters.ChoiceFilter(
        choices=[('yes', 'HA'), ('no', 'Not HA')],
        empty_label='All (HA + not)',
        method='filter_ha',
        label='HA'
    )

    def filter_ha(self, queryset, name, value):
        """Filter by the (Redis-derived) HA flag: more than 2 live VMs."""
        if value not in ('yes', 'no'):
            return queryset
        from cmdb.redis_client import ha_env_names
        names = ha_env_names()
        if value == 'yes':
            return queryset.filter(name__in=names)
        return queryset.exclude(name__in=names)

    gitops_repo = django_filters.ChoiceFilter(
        choices=[],  # Will be populated in __init__
        empty_label='All GitOps repos',
        label='GitOps Repo'
    )

    def filter_gitops(self, queryset, name, value):
        """Filter by GitOps-managed boolean from a yes/no choice."""
        if value == 'yes':
            return queryset.filter(gitops_managed=True)
        if value == 'no':
            return queryset.filter(gitops_managed=False)
        return queryset

    def filter_charm(self, queryset, name, value):
        """Filter environments that have the specified charm."""
        if not value:
            return queryset
        # Use PostgreSQL JSON operator to check if key exists
        return queryset.filter(charm_versions__has_key=value)

    def filter_charm_version(self, queryset, name, value):
        """Filter by charm name:version (e.g., 'lego:4/edge')."""
        if not value or ':' not in value:
            return queryset
        charm_name, version = value.split(':', 1)
        # Filter where charm_versions contains the charm with that version
        return queryset.filter(
            charm_versions__has_key=charm_name,
            charm_versions__contains={charm_name: version}
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Dynamically populate owner choices from database
        owners = Environment.objects.values_list('owner', flat=True).distinct().order_by('owner')
        owner_choices = [(owner, owner) for owner in owners if owner]
        self.filters['owner_exact'].extra['choices'] = (
            [(UNKNOWN_OWNER, '(unknown / no team)')] + owner_choices
        )

        # Dynamically populate "consumed by" choices = distinct requester-or-team
        consumers = set()
        for r, t in Environment.objects.values_list('requester', 'team'):
            c = (r or '').strip() or (t or '').strip()
            if c:
                consumers.add(c)
        self.filters['consumed_by'].extra['choices'] = [(c, c) for c in sorted(consumers)]

        # Dynamically populate cloud choices
        clouds = Environment.objects.values_list('cloud', flat=True).distinct().order_by('cloud')
        cloud_choices = [(cloud, cloud) for cloud in clouds if cloud]
        self.filters['cloud'].extra['choices'] = cloud_choices

        # Dynamically populate architecture choices
        archs = Environment.objects.values_list('compute_architecture', flat=True).distinct().order_by('compute_architecture')
        arch_choices = [(arch, arch) for arch in archs if arch]
        self.filters['compute_architecture'].extra['choices'] = arch_choices

        # Dynamically populate SLO level choices
        slo_levels = Environment.objects.values_list('slo_level', flat=True).distinct().order_by('slo_level')
        slo_choices = [(level, level) for level in slo_levels if level]
        self.filters['slo_level'].extra['choices'] = slo_choices

        # Dynamically populate GitOps repo choices (only managed envs have one)
        repos = (Environment.objects.filter(gitops_managed=True)
                 .values_list('gitops_repo', flat=True).distinct().order_by('gitops_repo'))
        self.filters['gitops_repo'].extra['choices'] = [(r, r) for r in repos if r]

    class Meta:
        model = Environment
        fields = [
            'name',
            'owner_exact',
            'consumed_by',
            'env_type',
            'status',
            'criticality_tier',
            'data_classification',
            'cloud',
            'region',
            'service_primitive',
            'compute_architecture',
            'charm',
            'charm_version',
            'cia_owner',
            'cia_custodian',
            'slo_level',
            'gitops',
            'gitops_repo',
            'resilient',
            'ha',
        ]
