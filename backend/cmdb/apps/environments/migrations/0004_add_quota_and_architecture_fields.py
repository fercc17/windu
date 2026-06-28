# Generated migration for adding quota and architecture fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('environments', '0003_environment_accessing_iam_groups_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='environment',
            name='quota_cpu_cores',
            field=models.IntegerField(blank=True, help_text='CPU cores quota (extracted from quotas JSON or placement)', null=True),
        ),
        migrations.AddField(
            model_name='environment',
            name='quota_ram_mb',
            field=models.BigIntegerField(blank=True, help_text='RAM quota in MB', null=True),
        ),
        migrations.AddField(
            model_name='environment',
            name='quota_storage_gb',
            field=models.IntegerField(blank=True, help_text='Storage quota in GB', null=True),
        ),
        migrations.AddField(
            model_name='environment',
            name='quota_instances',
            field=models.IntegerField(blank=True, help_text='Instance count quota', null=True),
        ),
        migrations.AddField(
            model_name='environment',
            name='architecture',
            field=models.CharField(blank=True, db_index=True, help_text='Architecture: x86_64, arm64, ppc64le, s390x', max_length=50, null=True),
        ),
        migrations.AddIndex(
            model_name='environment',
            index=models.Index(fields=['team', 'architecture'], name='environment_team_arch_idx'),
        ),
        migrations.AddIndex(
            model_name='environment',
            index=models.Index(fields=['cloud', 'architecture'], name='environment_cloud_arch_idx'),
        ),
    ]
