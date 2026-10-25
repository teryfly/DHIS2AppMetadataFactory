"""Replace the inert organisation-wide grants with real project-scoped ones.

`assign_role` early-returns when the same scoped role row already exists, and the project-scoped
row written before the fix left the membership row's role NULL — which is the column the
permission check actually reads.  So: revoke both scopes, then grant in the project.
"""
import sys

sys.path.insert(0, "/home/ubuntu/projects/DAMF/S1/scripts")
import seed_example as se  # noqa: E402

PROJECT = "prj-01M4E6B3NMSF8VZ887YD4W0TMD"
WANT = {"implementer": "DHIS2_IMPLEMENTER", "approver": "DEPLOYMENT_APPROVER"}

api = se.Api("http://127.0.0.1:8090", "admin")
api.sign_in("admin", sys.argv[1])

users = {row["subject"]: row["id"] for row in api.get("/api/v1/users", quiet=True)["items"]}

for subject, role in WANT.items():
    user_id = users[subject]
    api.call(
        "DELETE",
        f"/api/v1/users/{user_id}/roles",
        label=f"revoke org-wide {role} from {subject}",
        params={"role": role},
        quiet=True,
        accept=(200, 204),
    )
    api.call(
        "DELETE",
        f"/api/v1/users/{user_id}/roles",
        label=f"revoke the inert project row for {subject}",
        params={"role": role, "projectId": PROJECT},
        quiet=True,
        accept=(200, 204),
    )
    granted = api.put(
        f"/api/v1/users/{user_id}/roles",
        {"role": role, "projectId": PROJECT},
        label=f"grant {role} to {subject} in the project",
    )
    print(f"  {subject}: {granted['role']} @ {granted['projectId']}")

print("\nmembers (role is what the policy reads):")
for row in api.get(f"/api/v1/projects/{PROJECT}/members", quiet=True)["items"]:
    print(f"  {row['subject']:12} -> {row['role']}")

print("\nroles per account (GET /users):")
for row in api.get("/api/v1/users", quiet=True)["items"]:
    print(f"  {row['subject']:12} -> {[(r['role'], r['projectId']) for r in row['roles']]}")

# The gate actions are not asked about by `allowed-actions` until a Gate of that type exists, so
# the grant is checked against the policy itself — which is the same object the routes call.
from factory_control.identity.policy import RolePermissionPolicy  # noqa: E402

policy = RolePermissionPolicy()
PROBE = {
    "implementer": ("gate.approve.PACKAGE_GATE", "deployment.create", "stage.run"),
    "approver": ("gate.approve.DEPLOY_GATE.TEST", "deployment.create"),
}
print("\npolicy:")
for subject, actions in PROBE.items():
    for action in actions:
        print(
            f"  {subject:12} {action:30} allowed="
            f"{policy.is_allowed(operator=subject, project_id=PROJECT, action=action)}"
        )

