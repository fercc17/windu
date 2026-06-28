"""
Django models for IS-CMDB environments.

Schema per SCHEMA.md with extensions from field_mapping.yaml to capture
all data available in is-infrastructure.
"""
import uuid
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator


class Environment(models.Model):
    """
    Primary environment table. One row per environment declared in is-infrastructure.
    Soft-delete only: when git_path disappears, set end_date and status='decommissioning'.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Identity
    name = models.CharField(max_length=255, unique=True, db_index=True,
                           help_text="Environment name, e.g. amer-prod-launchpad")
    git_path = models.CharField(max_length=512,
                               help_text="Relative path in is-infrastructure, e.g. services/definitions/compute/foo.yaml")
    region = models.CharField(max_length=50, blank=True, null=True, db_index=True,
                             help_text="Geographic region: amer, apac, emea, edge")

    # Ownership
    owner = models.CharField(max_length=255, blank=True, null=True,
                            help_text="Team slug, e.g. is, webdesign, snapstore")
    team = models.CharField(max_length=255, blank=True, null=True, db_index=True,
                           help_text="Team slug (synonym for owner)")
    oncall_handle = models.CharField(max_length=255, blank=True, null=True,
                                     help_text="PagerDuty service ID or Mattermost handle")
    cost_center = models.CharField(max_length=100, blank=True, null=True)

    # Classification
    ENV_TYPE_CHOICES = [
        ('prod', 'Production'),
        ('staging', 'Staging'),
        ('dev', 'Development'),
        ('lab', 'Lab'),
    ]
    env_type = models.CharField(max_length=20, choices=ENV_TYPE_CHOICES, db_index=True)

    criticality_tier = models.IntegerField(
        blank=True, null=True,
        validators=[MinValueValidator(1), MaxValueValidator(3)],
        help_text="1=business critical, 2=important, 3=best effort"
    )

    DATA_CLASS_CHOICES = [
        ('pii', 'PII'),
        ('internal', 'Internal'),
        ('public', 'Public'),
    ]
    data_classification = models.CharField(max_length=20, choices=DATA_CLASS_CHOICES,
                                          blank=True, null=True)

    compliance_scope = models.JSONField(default=list, blank=True,
                                       help_text="List of compliance frameworks, e.g. ['soc2', 'iso27001']")

    # Declared state (parsed from Git)
    charm_versions = models.JSONField(default=dict, blank=True,
                                     help_text='{"postgresql": "14/stable", "vault": "1.8/edge"}')
    declared_at = models.DateTimeField(auto_now=True,
                                      help_text="Timestamp of last parser run")
    last_git_commit = models.CharField(max_length=40, blank=True, null=True,
                                      help_text="SHA that triggered the last parse")

    # Lifecycle
    STATUS_CHOICES = [
        ('provisioning', 'Provisioning'),
        ('active', 'Active'),
        ('degraded', 'Degraded'),
        ('maintenance', 'Maintenance'),
        ('decommissioning', 'Decommissioning'),
        ('archived', 'Archived'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='provisioning',
                             db_index=True)
    created_at = models.DateTimeField(blank=True, null=True,
                                     help_text="From cmdb label or git blame")
    end_date = models.DateTimeField(blank=True, null=True,
                                   help_text="null = live; set when manifest deleted")

    # Operational metadata
    maintenance_window = models.JSONField(blank=True, null=True,
                                         help_text='{"day": "sunday", "start": "02:00", "end": "06:00", "tz": "UTC"}')
    runbook_url = models.URLField(blank=True, null=True)

    # Flux / reconciliation (updated by Flux notification hook in Phase 4+)
    last_reconciled_at = models.DateTimeField(blank=True, null=True)
    last_good_commit = models.CharField(max_length=40, blank=True, null=True,
                                       help_text="Last SHA that reconciled cleanly")
    last_good_reconcile = models.DateTimeField(blank=True, null=True)

    updated_at = models.DateTimeField(auto_now=True)

    # Additional fields from is-infrastructure
    cloud = models.CharField(max_length=50, blank=True, null=True,
                            help_text="ps5, ps6, ps7, ps8, microcloud-drs, edge-tel, edge-et3")

    SERVICE_PRIMITIVE_CHOICES = [
        ('compute', 'Compute'),
        ('iam', 'IAM'),
        ('network', 'Network'),
        ('storage', 'Storage'),
    ]
    service_primitive = models.CharField(max_length=20, choices=SERVICE_PRIMITIVE_CHOICES,
                                        blank=True, null=True, db_index=True)
    service_class = models.CharField(max_length=100, blank=True, null=True,
                                    help_text="machine_model, container_model, database, kubernetes_cluster, etc.")

    K8S_DISTRIBUTION_CHOICES = [
        ('ck8s', 'Canonical Kubernetes (CK8s)'),
        ('ck8s-jenkins-aas', 'Jenkins aaS on CK8s'),
        ('legacy-k8s', 'Legacy k8s (pre-CK8s)'),
    ]
    k8s_distribution = models.CharField(
        max_length=20, choices=K8S_DISTRIBUTION_CHOICES, blank=True, null=True,
        db_index=True,
        help_text="Kubernetes distribution, derived from charm signature / naming. "
                  "CK8s = deploys the k8s/k8s-worker charms; jenkins-aas is a CK8s "
                  "subtype; legacy-k8s = pre-CK8s (microk8s / charmed-k8s).",
    )

    # Pre-computed dependency summary (refreshed by `refresh_dependency_cache`),
    # so the list table never has to join EnvironmentDependency at request time.
    cached_depends_on = models.TextField(
        blank=True, null=True,
        help_text="Comma-joined names of juju models this env depends on (cached).",
    )
    cached_dependents_count = models.PositiveIntegerField(
        default=0, db_index=True,
        help_text="Number of environments that depend on this one (cached).",
    )

    # Host aggregate the env runs on, cached from its primary node (#host-aggr).
    host_aggregate = models.CharField(
        max_length=100, blank=True, null=True, db_index=True,
        help_text="OpenStack host aggregate of the env's primary node (cached).",
    )

    # Real consuming team. For IS-operated aaS clusters (CK8s/Jenkins) the team
    # field is 'is' (the platform operator); the actual consumer is inferred from
    # the CIA asset/risk owner's home team. Cached by `infer_consumer_teams`.
    consumer_team = models.CharField(
        max_length=255, blank=True, null=True, db_index=True,
        help_text="Real consuming team (CIA-owner's home team for aaS; else team).",
    )

    juju_controller = models.CharField(max_length=255, blank=True, null=True,
                                      help_text="Name of Juju controller managing this environment")
    juju_series = models.CharField(max_length=20, blank=True, null=True,
                                  help_text="Juju version, e.g. '3.5' or '3.6'")
    juju_controller_stage = models.CharField(max_length=50, blank=True, null=True,
                                            help_text="production, staging, migration, etc.")

    bastion_server = models.CharField(max_length=255, blank=True, null=True,
                                     help_text="Hostname of bastion server")
    risk_group = models.CharField(max_length=50, blank=True, null=True,
                                 help_text="Risk group classification, typically 'stable'")

    description = models.TextField(blank=True, null=True)

    # CIA assessment individual owners
    cia_owner = models.EmailField(blank=True, null=True,
                                  help_text="Individual owner from cia_assessment.asset.owner")
    cia_risk_owner = models.EmailField(blank=True, null=True)
    cia_custodian = models.CharField(max_length=255, blank=True, null=True,
                                    help_text="Team slug from cia_assessment.asset.custodian")

    # SLO fields
    slo_level = models.CharField(max_length=50, blank=True, null=True,
                                help_text="IS24x5, IS24x7, etc.")
    slo_rto = models.IntegerField(blank=True, null=True,
                                 help_text="Recovery Time Objective in seconds")

    live = models.BooleanField(blank=True, null=True,
                              help_text="If false, environment is declared but not yet provisioned")

    # Resource capacity and sizing fields
    quotas = models.JSONField(default=dict, blank=True,
                             help_text='Resource quotas: {"cores": 80, "ram": 212992, "instances": 22, "gigabytes": 1160, ...}')

    # Denormalized quota fields for efficient aggregation
    quota_cpu_cores = models.IntegerField(blank=True, null=True,
                                         help_text="CPU cores quota (extracted from quotas JSON or placement)")
    quota_ram_mb = models.BigIntegerField(blank=True, null=True,
                                         help_text="RAM quota in MB")
    quota_storage_gb = models.IntegerField(blank=True, null=True,
                                          help_text="Storage quota in GB")
    quota_instances = models.IntegerField(blank=True, null=True,
                                         help_text="Instance count quota")

    # Architecture field for resource aggregation
    architecture = models.CharField(max_length=50, blank=True, null=True, db_index=True,
                                   help_text="Architecture: x86_64, arm64, ppc64le, s390x")

    database_size = models.CharField(max_length=20, blank=True, null=True,
                                    help_text="Database size: xsmall, small, medium, large, custom")
    worker_groups = models.JSONField(default=list, blank=True,
                                    help_text='K8s worker groups: [{"name": "k8s-worker", "size": "large", "units": 3}]')
    control_plane_size = models.CharField(max_length=20, blank=True, null=True,
                                         help_text="K8s control plane size: small, medium, large")
    control_plane_units = models.IntegerField(blank=True, null=True,
                                             help_text="K8s control plane unit count (typically 3)")
    network_size = models.IntegerField(blank=True, null=True,
                                      help_text="Network CIDR prefix length (e.g., 24, 27)")
    compute_architecture = models.CharField(max_length=20, blank=True, null=True,
                                           help_text="Compute architecture: amd64, arm64, etc.")
    postgresql_major_version = models.CharField(max_length=10, blank=True, null=True,
                                               help_text="PostgreSQL major version (for databases)")

    # Network and infrastructure configuration
    cluster = models.CharField(max_length=255, blank=True, null=True,
                              help_text="K8s cluster name for container_model environments")
    network_type = models.CharField(max_length=50, blank=True, null=True,
                                   help_text="isolated, shared, etc.")
    extra_network_peers = models.JSONField(default=list, blank=True,
                                          help_text="List of additional network peers")
    ingress_addresses = models.JSONField(default=list, blank=True,
                                        help_text='Public ingress addresses: ["example.com", "*.example.com"]')
    extra_subnet = models.CharField(max_length=50, blank=True, null=True,
                                   help_text="Additional subnet configuration")

    # Juju and model configuration
    jaas_managed = models.BooleanField(blank=True, null=True,
                                      help_text="Whether managed by JAAS")
    juju_model_config = models.JSONField(default=dict, blank=True,
                                        help_text='Juju model config: {"juju-http-proxy": "...", ...}')
    gitops_model_management = models.JSONField(default=dict, blank=True,
                                              help_text="GitOps configuration for model management")

    # Denormalized GitOps flags, derived from gitops_model_management by the
    # `refresh_gitops` command for fast filtering/aggregation. An env is
    # GitOps-managed when is-infrastructure declares a gitops_model_management
    # block pointing at a terraform-models repo (is-terraform-models,
    # ubuntu-engineering-terraform-models, ...). The model in turn consumes
    # reusable modules from is-terraform-modules (recorded in gitops_modules).
    gitops_managed = models.BooleanField(
        default=False, db_index=True,
        help_text="True if a gitops_model_management block is declared (reconciled via GitOps).",
    )
    gitops_repo = models.CharField(
        max_length=255, blank=True, null=True, db_index=True,
        help_text="terraform-models repo managing this env, e.g. is-terraform-models.",
    )
    gitops_path = models.CharField(
        max_length=512, blank=True, null=True,
        help_text="Path of the terraform model within the gitops repo, e.g. models/prod-is-vault-ps7.",
    )
    gitops_enabled = models.BooleanField(
        blank=True, null=True,
        help_text="gitops_model_management.enabled — whether GitOps management is enabled.",
    )
    gitops_suspended = models.BooleanField(
        blank=True, null=True,
        help_text="gitops_model_management.suspend — whether reconciliation is currently suspended.",
    )
    gitops_modules = models.JSONField(
        default=list, blank=True,
        help_text="is-terraform-modules subpaths consumed by this env's model "
                  "(resolved on disk for is-terraform-models envs), e.g. ['subordinates', 'juju/applications/lego'].",
    )

    # Access and permissions
    iam_groups = models.JSONField(default=list, blank=True,
                                 help_text='IAM groups with access: ["is-platform-services-team"]')
    grant_team_access = models.BooleanField(blank=True, null=True,
                                           help_text="Whether to grant team access")
    role_account_lp_users = models.JSONField(default=list, blank=True,
                                            help_text="Launchpad user IDs for role accounts")
    accessing_iam_groups = models.JSONField(default=list, blank=True,
                                           help_text="IAM groups that access this environment")

    # Compute and workload features
    builder_workloads = models.BooleanField(blank=True, null=True,
                                           help_text="Whether builder workloads are allowed")
    allow_vgpu = models.BooleanField(blank=True, null=True,
                                    help_text="Whether vGPU is allowed")
    allow_gpu_passthrough = models.BooleanField(blank=True, null=True,
                                               help_text="Whether GPU passthrough is allowed")
    allow_dedicated_cpu = models.BooleanField(blank=True, null=True,
                                             help_text="Whether dedicated CPU is allowed")
    manage_flavor_access = models.BooleanField(blank=True, null=True,
                                              help_text="Whether flavor access is managed")
    openstack_credential_access = models.BooleanField(blank=True, null=True,
                                                     help_text="Whether OpenStack credential access is granted")

    # Database-specific fields
    postgresql_use_local_storage = models.BooleanField(blank=True, null=True,
                                                      help_text="Whether PostgreSQL uses local storage")
    legacy_certificates_charm = models.BooleanField(blank=True, null=True,
                                                   help_text="Whether using legacy certificates charm")
    datastore = models.CharField(max_length=100, blank=True, null=True,
                                help_text="Datastore type or name")

    # Kubernetes-specific fields
    k8s_models = models.JSONField(default=list, blank=True,
                                 help_text="List of K8s models")
    kube_apiserver_extra_sans = models.JSONField(default=list, blank=True,
                                                help_text="Extra SANs for kube-apiserver certificate")
    enable_cilium_ingress = models.BooleanField(blank=True, null=True,
                                               help_text="Whether Cilium ingress is enabled")
    enable_exposed_companion = models.BooleanField(blank=True, null=True,
                                                  help_text="Whether exposed companion is enabled")

    # Related models and dependencies (for display, not FK)
    remote_cmr_models = models.JSONField(default=list, blank=True,
                                        help_text="Remote CMR models this environment uses")
    accessing_juju_models = models.JSONField(default=list, blank=True,
                                            help_text="Juju models that access this environment")
    data_integrator_accessing_juju_model = models.JSONField(default=list, blank=True,
                                                           help_text="Data integrator models accessing this")
    services = models.JSONField(default=list, blank=True,
                               help_text="Services deployed in this environment")

    # Metadata and ownership extensions
    user = models.CharField(max_length=255, blank=True, null=True,
                           help_text="User associated with environment (for personal envs)")
    requester = models.CharField(max_length=255, blank=True, null=True,
                                help_text="Person who requested this environment")

    # Physical placement — set by link_placement_nodes (#26) from live placement
    # data, matched against netbox.Node.hostname. Nullable: not every env is
    # placed, and a node may be soft-deleted out from under it (SET_NULL).
    primary_node = models.ForeignKey(
        'netbox.Node', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='primary_environments',
        help_text="Physical node hosting the primary unit (from placement).",
    )
    secondary_node = models.ForeignKey(
        'netbox.Node', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='secondary_environments',
        help_text="Physical node hosting the secondary/standby unit, if any.",
    )

    class Meta:
        db_table = 'environments'
        ordering = ['name']
        indexes = [
            models.Index(fields=['region', 'env_type', 'status']),
            models.Index(fields=['team']),
            models.Index(fields=['service_primitive', 'service_class']),
            models.Index(fields=['team', 'architecture']),
            models.Index(fields=['cloud', 'architecture']),
        ]

    def __str__(self):
        return self.name

    @property
    def consumed_by(self):
        """Consuming team: the requester (real consumer, esp. for aaS where
        team/owner is the provider 'is'), falling back to the owning team."""
        return (self.requester or '').strip() or (self.team or '').strip() or None


class EnvironmentDependency(models.Model):
    """
    Explicit dependency graph. One row per directed edge.
    Populated by parser from Terraform remote state refs, Juju relations, and declared labels.
    """
    environment_name = models.CharField(max_length=255, db_index=True,
                                       help_text="Environment that has the dependency")
    depends_on_name = models.CharField(max_length=255, db_index=True,
                                      help_text="Environment that is depended upon")

    DEPENDENCY_TYPE_CHOICES = [
        ('infrastructure', 'Infrastructure'),  # Inferred from Terraform or Juju
        ('declared', 'Declared'),             # Explicitly set via label
    ]
    dependency_type = models.CharField(max_length=20, choices=DEPENDENCY_TYPE_CHOICES)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'environment_dependencies'
        unique_together = ('environment_name', 'depends_on_name')
        indexes = [
            models.Index(fields=['depends_on_name']),  # For "what would break if I take down X" query
        ]

    def __str__(self):
        return f"{self.environment_name} → {self.depends_on_name} ({self.dependency_type})"


class CloudCapacity(models.Model):
    """
    Track total available and allocated resources per cloud and architecture.
    Updated by management command or parser to enable percentage calculations.
    """
    cloud_name = models.CharField(max_length=50, db_index=True,
                                 help_text="Cloud name: ps5, ps6, ps7, aws, gcp, azure")
    architecture = models.CharField(max_length=50, db_index=True,
                                   help_text="Architecture: x86_64, arm64, etc.")

    # Total capacity available in this cloud
    total_cpu_cores = models.IntegerField(default=0,
                                         help_text="Total CPU cores available")
    total_ram_gb = models.IntegerField(default=0,
                                      help_text="Total RAM in GB available")
    total_storage_gb = models.IntegerField(default=0,
                                          help_text="Total storage in GB available")

    # Currently allocated/deployed resources
    allocated_cpu_cores = models.IntegerField(default=0,
                                             help_text="CPU cores currently allocated")
    allocated_ram_gb = models.IntegerField(default=0,
                                          help_text="RAM in GB currently allocated")
    allocated_storage_gb = models.IntegerField(default=0,
                                              help_text="Storage in GB currently allocated")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'cloud_capacity'
        unique_together = ('cloud_name', 'architecture')
        indexes = [
            models.Index(fields=['cloud_name', 'architecture']),
        ]

    def __str__(self):
        return f"{self.cloud_name} ({self.architecture})"


class PlacementHistory(models.Model):
    """
    Written by poller once per hour (not every 5 minutes).
    Used for incident retrospectives.
    Pruned to a rolling 30-day window by scheduled job.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    environment_name = models.CharField(max_length=255, db_index=True)
    primary_node = models.CharField(max_length=255, blank=True, null=True)
    secondary_node = models.CharField(max_length=255, blank=True, null=True)
    juju_model = models.CharField(max_length=255, blank=True, null=True)
    juju_units = models.JSONField(default=list, blank=True,
                                 help_text='[{"unit": "postgresql/0", "machine": "3"}]')

    SOURCE_CHOICES = [
        ('juju-api', 'Juju API'),
        ('kubectl', 'Kubernetes API'),
    ]
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES)

    recorded_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'placement_history'
        ordering = ['-recorded_at']
        indexes = [
            models.Index(fields=['environment_name', '-recorded_at']),
        ]

    def __str__(self):
        return f"{self.environment_name} @ {self.recorded_at}"


class CharmRelease(models.Model):
    """
    Cache of the latest revision Charmhub has published per charm channel.

    One row per ``(charm, track, risk)``. Populated by the ``refresh_charmhub``
    command from charmhub.io (never from the infra/terraform repos). The charm
    statistics "Outdated" view joins this against ``Environment.charm_versions``
    to flag deployments that are behind or — for prod — not on ``stable``.
    """
    charm = models.CharField(max_length=255, db_index=True,
                             help_text="Charmhub charm name, e.g. postgresql")
    track = models.CharField(max_length=64, help_text="Channel track, e.g. 14, latest")
    risk = models.CharField(max_length=20, help_text="Channel risk: stable/candidate/beta/edge")

    latest_revision = models.IntegerField(
        blank=True, null=True,
        help_text="Highest revision published to this channel; null if unknown")
    latest_version = models.CharField(max_length=255, blank=True, default='',
                                      help_text="Version string of the latest revision")
    released_at = models.DateTimeField(blank=True, null=True,
                                       help_text="When that revision was released to the channel")
    checked_at = models.DateTimeField(auto_now=True,
                                      help_text="Last time this row was refreshed from Charmhub")

    class Meta:
        db_table = 'charm_release'
        unique_together = [('charm', 'track', 'risk')]
        ordering = ['charm', 'track', 'risk']
        indexes = [
            models.Index(fields=['charm']),
        ]

    def __str__(self):
        return f"{self.charm} {self.track}/{self.risk} rev {self.latest_revision}"
