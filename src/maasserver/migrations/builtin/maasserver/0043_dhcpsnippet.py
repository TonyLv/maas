# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import (
    migrations,
    models,
)
import maasserver.models.cleansave


class Migration(migrations.Migration):

    dependencies = [
        ('maasserver', '0042_add_routable_rack_controllers_to_bmc'),
    ]

    operations = [
        migrations.CreateModel(
            name='DHCPSnippet',
            fields=[
                ('id', models.AutoField(primary_key=True, auto_created=True, verbose_name='ID', serialize=False)),
                ('created', models.DateTimeField(editable=False)),
                ('updated', models.DateTimeField(editable=False)),
                ('name', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True)),
                ('enabled', models.BooleanField(default=True)),
                ('node', models.ForeignKey(to='maasserver.Node', null=True, blank=True)),
                ('subnet', models.ForeignKey(to='maasserver.Subnet', null=True, blank=True)),
                ('value', models.ForeignKey(to='maasserver.VersionedTextFile')),
            ],
            options={
                'abstract': False,
            },
            bases=(maasserver.models.cleansave.CleanSave, models.Model, object),
        ),
    ]
