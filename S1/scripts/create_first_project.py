"""Create the live deployment's first project, then grant the two project-scoped roles.

Reuses the seed script's own helpers so the environment registration follows the same order the
full flow proved against the real DHIS2 instance: secret → environment → connection test →
capability probe. Project creation requires a target, so an environment has to land first.
"""
import argparse
import json
import sys

sys.path.insert(0, "/home/ubuntu/projects/DAMF/S1/scripts")
import seed_example as se  # noqa: E402

SUBJECTS = {"implementer": "DHIS2_IMPLEMENTER", "approver": "DEPLOYMENT_APPROVER"}

args = argparse.Namespace(
    base_url="http://127.0.0.1:8090",
    operator="admin",
    run_tag="MT",
    llm_url="",
    provider="",
    model="",
    dhis2_credential_env="DHIS2_CRED",
    dhis2_base_url=se.DHIS2_DEMO,
    test_env="sbtest",
    project_type="greenfield",
    secret_slug="sb-mt",
    timeout=180.0,
    skip_activate=True,
    manual_stages=set(),
    until="setup",
)

api = se.Api(args.base_url, args.operator)
# the admin password comes from the environment, never argv: a command line is world-readable
# through `ps`, and this is the account that can mint credentials
import os  # noqa: E402

password = os.environ.get("FACTORY_SEED_PASSWORD", "").strip()
if not password:
    raise SystemExit("set FACTORY_SEED_PASSWORD to the admin password")
api.sign_in(args.operator, password)

se.register_environment(api, args, args.test_env, "TEST")
project = se.create_project(api, args)

for subject, role in SUBJECTS.items():
    users = api.get("/api/v1/users", quiet=True)["items"]
    user_id = next(u["id"] for u in users if u["subject"] == subject)
    granted = api.put(
        f"/api/v1/users/{user_id}/roles",
        {"role": role, "projectId": project["id"]},
        label=f"grant {role} to {subject} in {project['id']}",
    )
    print(f"  {subject}: role={granted['role']} projectId={granted['projectId']}")

members = api.get(f"/api/v1/projects/{project['id']}/members", quiet=True)["items"]
print("\nmembers:")
for row in members:
    print(" ", row["subject"], "->", row["role"])
print("\nproject:", json.dumps(
    {"id": project["id"], "name": project["name"], "workflowId": project["workflowId"],
     "milestone": project.get("milestoneStatus"), "currentStage": project.get("currentStageId")},
    ensure_ascii=False, indent=2))
