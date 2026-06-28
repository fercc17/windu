"""
Storage resource models (RadosGW / S3 / GCS buckets) and which environments
access them. Populated by ``tools/rados_ingest.py`` (stub until RadosGW admin
credentials exist).
"""
from django.db import models


class StorageResource(models.Model):
    name = models.CharField(max_length=255, unique=True, db_index=True)
    bucket_name = models.CharField(max_length=255)
    cloud = models.CharField(max_length=50, db_index=True)
    owner_team = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    size_gb = models.FloatField(default=0.0)
    object_count = models.IntegerField(default=0)

    STORAGE_TYPE_CHOICES = [
        ('radosgw', 'RadosGW'),
        ('s3', 'S3'),
        ('gcs', 'GCS'),
    ]
    storage_type = models.CharField(max_length=20, choices=STORAGE_TYPE_CHOICES, default='radosgw')
    last_synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'storage_resources'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class StorageEnvironmentAccess(models.Model):
    storage = models.ForeignKey(
        StorageResource, on_delete=models.CASCADE, related_name='environment_accesses'
    )
    environment = models.ForeignKey(
        'environments.Environment', on_delete=models.CASCADE, related_name='storage_accesses'
    )
    access_type = models.CharField(max_length=50, default='readwrite')

    class Meta:
        db_table = 'storage_environment_access'
        unique_together = ('storage', 'environment')

    def __str__(self) -> str:
        return f"{self.environment_id} -> {self.storage_id} ({self.access_type})"
