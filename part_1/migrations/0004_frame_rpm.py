from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("part_1", "0003_experiment_refactor"),
    ]

    operations = [
        migrations.AddField(
            model_name="frame",
            name="rpm",
            field=models.FloatField(default=0),
        ),
    ]
