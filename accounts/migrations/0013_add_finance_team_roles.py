from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0012_seed_proposal_team_roles"),
    ]

    operations = [
        migrations.AlterField(
            model_name="role",
            name="name",
            field=models.CharField(
                choices=[
                    ("super_admin", "Super Administrator"),
                    ("admin", "Administrator"),
                    ("manager", "Manager"),
                    ("sales_rep", "Sales Representative"),
                    ("procurement_mgr", "Procurement Manager"),
                    ("procurement_off", "Procurement Officer"),
                    ("proposal_head", "Proposal Team Head"),
                    ("proposal_rep", "Proposal Representative"),
                    ("finance_head", "Finance Head"),
                    ("finance_manager", "Finance Manager"),
                    ("finance_rep", "Finance Representative"),
                ],
                max_length=20,
                unique=True,
            ),
        ),
    ]
