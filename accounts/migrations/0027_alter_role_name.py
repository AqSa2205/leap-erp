# Adds the four HR/administration authorization roles to Role.name choices.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0026_enable_devtracking_for_ai_head"),
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
                    ("developer", "Developer"),
                    ("ai_head", "AI Head"),
                    ("ai_intern", "AI Intern"),
                    ("ai_engineer", "AI Engineer"),
                    ("ai_junior_engineer", "Junior AI Engineer"),
                    ("project_manager", "Project Manager"),
                    ("site_manager", "Site Manager"),
                    ("erp_admin", "ERP Admin"),
                    ("document_controller", "Document Controller"),
                ],
                max_length=20,
                unique=True,
            ),
        ),
    ]
