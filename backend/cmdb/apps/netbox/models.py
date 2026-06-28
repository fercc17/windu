"""
Physical infrastructure models, synced from Netbox.

A ``Node`` is one Netbox ``dcim.device`` (a physical server or switch). Sync is
idempotent on ``netbox_id`` (see ``reconcile_netbox`` / the webhook receiver) and
soft-deletes only: a device that disappears from Netbox is marked
``status='decommissioning'``, never deleted.

Note: the live Netbox holds no interface or cable data (see
``docs/findings/netbox-audit.md`` §3), so ``NodeInterface`` / ``NodeCable`` are
expected to stay empty and ``physical_completeness`` defaults to 0.0.
"""
from django.db import models


class Node(models.Model):
    """A physical device (server or switch) mirrored from Netbox."""

    netbox_id = models.IntegerField(unique=True)
    hostname = models.CharField(max_length=255, unique=True, db_index=True)
    site = models.CharField(max_length=100)                       # Netbox site slug
    cloud = models.CharField(max_length=50, db_index=True)        # ps5, ps6, etc.
    role = models.CharField(max_length=100)                       # server, switch, etc.
    rack = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(max_length=50)                      # active, decommissioning, etc.
    primary_ip = models.GenericIPAddressField(blank=True, null=True)
    host_aggregate = models.CharField(                            # OpenStack host aggregate
        max_length=100, blank=True, null=True, db_index=True,
        help_text="OpenStack host aggregate the hypervisor belongs to "
                  "(e.g. production, builders, critical, SRIOV, staging).",
    )
    uplink_redundancy = models.BooleanField(default=False)
    physical_completeness = models.FloatField(
        default=0.0,
        help_text="0.0-1.0; fraction of interfaces with cable records",
    )
    last_synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'nodes'
        ordering = ['hostname']
        indexes = [
            models.Index(fields=['cloud', 'status']),
            models.Index(fields=['site']),
        ]

    def __str__(self) -> str:
        return self.hostname

    @property
    def completeness_band(self) -> str:
        """Traffic-light band for ``physical_completeness`` (see #27/#28)."""
        if self.physical_completeness >= 0.8:
            return 'green'
        if self.physical_completeness >= 0.5:
            return 'amber'
        return 'red'


class NodeInterface(models.Model):
    """A network interface on a Node (from Netbox ``dcim.interfaces``)."""

    node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name='interfaces')
    netbox_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=100)
    mac_address = models.CharField(max_length=17, blank=True, null=True)
    speed_mbps = models.IntegerField(blank=True, null=True)

    class Meta:
        db_table = 'node_interfaces'
        ordering = ['node', 'name']

    def __str__(self) -> str:
        return f"{self.node.hostname}:{self.name}"


class NodeCable(models.Model):
    """A cable between two interfaces (from Netbox ``dcim.cables``)."""

    netbox_id = models.IntegerField(unique=True)
    interface_a = models.ForeignKey(
        NodeInterface, on_delete=models.CASCADE, related_name='cables_as_a'
    )
    interface_b = models.ForeignKey(
        NodeInterface, on_delete=models.CASCADE, related_name='cables_as_b',
        blank=True, null=True,
    )
    cable_type = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        db_table = 'node_cables'

    def __str__(self) -> str:
        return f"cable:{self.netbox_id}"


class NodeSwitchConnection(models.Model):
    """
    A node's uplink to a switch, derived from NodeCable records by
    ``build_switch_graph`` (#39). Empty while Netbox holds no cable data.
    """
    node = models.ForeignKey(
        Node, on_delete=models.CASCADE, related_name='switch_connections'
    )
    switch_hostname = models.CharField(max_length=255, db_index=True)
    interface_name = models.CharField(max_length=100)
    port_name = models.CharField(max_length=100)

    class Meta:
        db_table = 'node_switch_connections'
        ordering = ['node', 'switch_hostname']

    def __str__(self) -> str:
        return f"{self.node.hostname} -> {self.switch_hostname}:{self.port_name}"


class CloudStakeholder(models.Model):
    """
    A stakeholder to notify about a cloud (e.g. decommission comms) (#59).
    Clouds have no model, so this keys on the cloud slug.
    """
    cloud_slug = models.CharField(max_length=50, db_index=True)
    email = models.EmailField()
    name = models.CharField(max_length=255)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'cloud_stakeholders'
        unique_together = ('cloud_slug', 'email')
        ordering = ['cloud_slug', 'email']

    def __str__(self) -> str:
        return f"{self.cloud_slug}: {self.email}"
