# Generated migration for CloudCapacity model

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('environments', '0004_add_quota_and_architecture_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='CloudCapacity',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cloud_name', models.CharField(db_index=True, help_text='Cloud name: ps5, ps6, ps7, aws, gcp, azure', max_length=50)),
                ('architecture', models.CharField(db_index=True, help_text='Architecture: x86_64, arm64, etc.', max_length=50)),
                ('total_cpu_cores', models.IntegerField(default=0, help_text='Total CPU cores available')),
                ('total_ram_gb', models.IntegerField(default=0, help_text='Total RAM in GB available')),
                ('total_storage_gb', models.IntegerField(default=0, help_text='Total storage in GB available')),
                ('allocated_cpu_cores', models.IntegerField(default=0, help_text='CPU cores currently allocated')),
                ('allocated_ram_gb', models.IntegerField(default=0, help_text='RAM in GB currently allocated')),
                ('allocated_storage_gb', models.IntegerField(default=0, help_text='Storage in GB currently allocated')),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'cloud_capacity',
                'unique_together': {('cloud_name', 'architecture')},
            },
        ),
        migrations.AddIndex(
            model_name='cloudcapacity',
            index=models.Index(fields=['cloud_name', 'architecture'], name='cloud_capacity_cloud_arch_idx'),
        ),
    ]
