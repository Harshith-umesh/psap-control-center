# Deploying PSAP Control Center on OpenShift

This guide deploys the PSAP Control Center on an OpenShift (OCP) cluster
using container images hosted on [Quay.io](https://quay.io).

## Prerequisites

- `oc` CLI installed
- `podman` installed (for building and pushing images)
- Node.js and `npm` installed (for building frontend static assets)
- Credentials for the target OpenShift cluster
- A Quay.io account with push access to your organization

## Image Registry

Create two repositories under your Quay.io organization:

| Image | Repository |
| ----- | ---------- |
| Backend  | `quay.io/<your-org>/psap-control-center-backend` |
| Frontend | `quay.io/<your-org>/psap-control-center-frontend` |

Set both to **public** so the cluster can pull without an image pull secret.
If you prefer private repos, create a pull secret on the cluster and link it
to the `default` service account in the project namespace.

## Naming Convention

All OpenShift resources use the `psap-control-center-*` prefix:

| Resource    | Name                                |
| ----------- | ----------------------------------- |
| Namespace   | `psap-control-center`               |
| Secrets     | `psap-control-center-admin`         |
|             | `psap-control-center-config`        |
| PVCs        | `psap-control-center-data`          |
|             | `psap-control-center-kubeconfigs`   |
| Deployments | `psap-control-center-backend`       |
|             | `psap-control-center-frontend`      |
| Services    | `psap-control-center-backend`       |
|             | `psap-control-center-frontend`      |
| Route       | `psap-control-center`               |

## Deployment Steps

### 1. Log in to the cluster

```bash
oc login <cluster-api-url> --username=<user> --password=<pass>
```

### 2. Build and push images to Quay.io

Set your Quay organization (used throughout the remaining steps):

```bash
export QUAY_ORG=<your-org>
```

Log in to Quay.io:

```bash
podman login quay.io
```

Build both images for linux/amd64 and push:

```bash
# Backend
podman build --platform linux/amd64 \
  -t quay.io/${QUAY_ORG}/psap-control-center-backend:latest ./backend
podman push quay.io/${QUAY_ORG}/psap-control-center-backend:latest

# Frontend — build static assets locally, then package into the Nginx image
cd frontend && npm ci && npm run build && cd ..
podman build --platform linux/amd64 \
  -t quay.io/${QUAY_ORG}/psap-control-center-frontend:latest \
  -f - ./frontend <<'DOCKERFILE'
FROM nginxinc/nginx-unprivileged:alpine
COPY dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
DOCKERFILE
podman push quay.io/${QUAY_ORG}/psap-control-center-frontend:latest
```

> **Note:** The frontend is built locally (not inside the container) to avoid
> QEMU emulation issues when cross-compiling Node.js on Apple Silicon. If you
> are building on an x86_64 host, you can omit `--platform linux/amd64` and
> use the standard multi-stage `frontend/Dockerfile` directly.

### 3. Create the namespace

```bash
oc new-project psap-control-center
```

### 4. Configure runtime settings

Provision authentication, database, and application settings through the
organization's approved runtime secret-management process. Credential values,
creation commands, and rotation procedures are intentionally excluded from
this repository. The Google OAuth redirect URI must exactly match an authorized
redirect URI configured for its web client.

### 5. Create persistent volume claims

```bash
oc apply -f deploy/pvcs.yaml
```

Edit `deploy/pvcs.yaml` before applying if your cluster uses a non-default
storage class (e.g. `ocs-storagecluster-cephfs`).

### 6. Deploy the backend

```bash
oc create deployment psap-control-center-backend \
  --image=quay.io/${QUAY_ORG}/psap-control-center-backend:latest

oc set env deployment/psap-control-center-backend \
  --from=secret/psap-control-center-admin
oc set env deployment/psap-control-center-backend \
  --from=secret/psap-control-center-config
oc set env deployment/psap-control-center-backend \
  KUBECONFIG_STORAGE_PATH=/app/kubeconfigs

oc set volume deployment/psap-control-center-backend \
  --add --name=data --mount-path=/app/data \
  --claim-name=psap-control-center-data
oc set volume deployment/psap-control-center-backend \
  --add --name=kubeconfigs --mount-path=/app/kubeconfigs \
  --claim-name=psap-control-center-kubeconfigs

# Both backend volumes are ReadWriteOnce. Recreate prevents a rolling update
# from blocking while the replacement pod tries to attach volumes still in use.
oc patch deployment psap-control-center-backend --type=merge \
  -p '{"spec":{"strategy":{"type":"Recreate","rollingUpdate":null}}}'

oc expose deployment psap-control-center-backend --port=8000
```

### 7. Deploy the frontend

```bash
oc create deployment psap-control-center-frontend \
  --image=quay.io/${QUAY_ORG}/psap-control-center-frontend:latest

oc expose deployment psap-control-center-frontend --port=8080
```

### 8. Create the route

```bash
APPS_DOMAIN=$(oc get ingresses.config/cluster -o jsonpath='{.spec.domain}' 2>/dev/null)

oc create route edge psap-control-center \
  --service=psap-control-center-frontend \
  --hostname=control-center.${APPS_DOMAIN}
```

If you lack permissions for `ingresses.config`, find the apps domain from
an existing route or from the cluster console URL and substitute manually:

```bash
oc create route edge psap-control-center \
  --service=psap-control-center-frontend \
  --hostname=control-center.<apps-domain>
```

### 9. Verify

```bash
oc get pods
oc get route psap-control-center
curl -k https://control-center.<apps-domain>/api/v1/health
```

## Hearth/Management-Cluster Permissions

The Hearth connection is also the Kubernetes connection used by the Testing
tab. Control Center uses the identity from the uploaded management-cluster
kubeconfig or from the OpenShift username/password login; it does not replace
that identity with the Control Center pod's service account and does not
elevate its permissions.

Before connecting Hearth, ensure that identity has the required permissions in
the configured namespaces:

- In `HEARTH_NAMESPACE` (default: `hearth`), read access to
  `fournosclusters.fournos.dev`.
- In `FOURNOS_NAMESPACE` (default: `psap-automation`), read access to
  `fournosjobs.fournos.dev`, `pipelineruns.tekton.dev`,
  `taskruns.tekton.dev`, Pods, and the Pod log subresource.
- Testing actions additionally require the corresponding `create`, `patch`, or
  `delete` permission on `fournosjobs.fournos.dev`.

Use the same kubeconfig that will be supplied to Control Center to verify the
timeline permissions:

```bash
oc --kubeconfig=<management-kubeconfig> auth can-i get taskruns.tekton.dev \
  -n <fournos-namespace>
oc --kubeconfig=<management-kubeconfig> auth can-i list taskruns.tekton.dev \
  -n <fournos-namespace>
oc --kubeconfig=<management-kubeconfig> auth can-i watch taskruns.tekton.dev \
  -n <fournos-namespace>
```

If an existing Fournos user has all other required permissions but lacks
TaskRun read access, a management-cluster RBAC administrator can apply this
least-privilege grant. Replace the namespace and subject with the values used
by that deployment:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: psap-control-center-taskrun-reader
  namespace: <fournos-namespace>
rules:
  - apiGroups: ["tekton.dev"]
    resources: ["taskruns"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: psap-control-center-taskrun-reader
  namespace: <fournos-namespace>
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: psap-control-center-taskrun-reader
subjects:
  - apiGroup: rbac.authorization.k8s.io
    kind: User
    name: <management-cluster-user>
```

Without TaskRun read access, the Pods view may still show accurate status while
the Pipeline Timeline remains `Pending` for active runs or `UNKNOWN` for
terminal runs. Control Center also cannot preserve durable stage snapshots in
that state. The connected user usually cannot grant this permission to itself;
the Role and RoleBinding must be created by an appropriately authorized
management-cluster user.

## CI/CD: Automatic Build & Deploy

The repository includes GitHub Actions workflows and OCP manifests for
fully automatic build-and-deploy on every push. Two environments are
supported side-by-side on the same cluster:

| Environment | Branch | Image Tag | OCP Namespace | Route Prefix | Banner |
|-------------|--------|-----------|---------------|--------------|--------|
| **Production** | `main` | `:latest` | `psap-control-center` | `control-center` | None |
| **Development** | `development` | `:dev` | `psap-control-center-dev` | `dev-control-center` | "DEVELOPMENT" |

### How it works

1. **Push to branch** → GitHub Actions builds frontend + backend images and
   pushes them to Quay.io with the appropriate tag.
2. **OCP CronJob** (runs every 2 minutes) polls Quay for new image digests.
   When a change is detected, it triggers `kubectl rollout restart` for the
   corresponding deployment. The last successfully deployed digest is kept in
   the `psap-control-center.io/image-digest` Deployment annotation so it
   persists across the CronJob's short-lived pods.
3. **Total time from push to live:** ~4–6 minutes.

### GitHub Actions workflows

| File | Trigger | Images |
|------|---------|--------|
| `.github/workflows/prod-deploy.yml` | Push to `main` | `:latest` |
| `.github/workflows/dev-deploy.yml` | Push to `development` | `:dev` (with DEVELOPMENT banner) |

Both workflows use the same Quay credentials stored as GitHub repository
secrets:

- `QUAY_CONTROL_CENTER_USERNAME`
- `QUAY_CONTROL_CENTER_PASSWORD`

### Setting up the OCP image updater

The image updater runs inside OCP and requires no external cluster access
from GitHub Actions. Apply the manifests in order:

```bash
# 1. ServiceAccount + RBAC
oc apply -n <namespace> -f deploy/ocp/image-updater-sa.yaml

# 2. Updater script
oc apply -n <namespace> -f deploy/ocp/image-updater-script.yaml

# 3. CronJob (edit IMAGE_TAG and proxy settings first if needed)
oc apply -n <namespace> -f deploy/ocp/image-updater-cronjob.yaml
```

**Before applying the CronJob**, edit `deploy/ocp/image-updater-cronjob.yaml`:

- Pin the updater container to the CLI image referenced by the target
  cluster. Refresh this digest after an OpenShift upgrade:

  ```bash
  oc get imagestream cli -n openshift \
    -o jsonpath='{.status.tags[?(@.tag=="latest")].items[0].dockerImageReference}{"\n"}'
  ```

  Use the returned immutable reference for the updater container. Do not use
  `registry.redhat.io/openshift4/ose-cli:latest`; that repository requires an
  explicit version or digest.
- Set `IMAGE_TAG` to `latest` (prod) or `dev` (dev).
- Adjust `HTTPS_PROXY` / `NO_PROXY` for your cluster's network, or remove
  them entirely if no proxy is needed.

### Verifying the updater

```bash
# Check CronJob status
oc get cronjob image-updater -n <namespace>

# Confirm the persistent digest recorded for each deployment
oc get deployment -n <namespace> \
  -o custom-columns=NAME:.metadata.name,DIGEST:.metadata.annotations.psap-control-center\\.io/image-digest

# View the latest job's logs
oc logs -n <namespace> job/$(oc get jobs -n <namespace> \
  --sort-by=.metadata.creationTimestamp -o name | tail -1 | cut -d/ -f2)
```

### Setting up a dev namespace from scratch

```bash
# Create namespace
oc new-project psap-control-center-dev

# Copy secrets from prod (or create fresh ones)
oc get secret psap-control-center-admin -n psap-control-center -o json \
  | jq '.metadata.namespace = "psap-control-center-dev" | del(.metadata.uid,.metadata.resourceVersion,.metadata.creationTimestamp)' \
  | oc apply -f -
oc get secret psap-control-center-config -n psap-control-center -o json \
  | jq '.metadata.namespace = "psap-control-center-dev" | del(.metadata.uid,.metadata.resourceVersion,.metadata.creationTimestamp)' \
  | oc apply -f -

# Apply PVCs, deploy PostgreSQL, backend, frontend (same as prod steps above
# but using :dev image tags and the dev namespace)

# Apply image updater with IMAGE_TAG=dev
oc apply -n psap-control-center-dev -f deploy/ocp/image-updater-sa.yaml
oc apply -n psap-control-center-dev -f deploy/ocp/image-updater-script.yaml
# Edit IMAGE_TAG to "dev" in the CronJob, then:
oc apply -n psap-control-center-dev -f deploy/ocp/image-updater-cronjob.yaml

# Create route with dev prefix
oc create route edge psap-control-center-dev \
  --service=psap-control-center-frontend \
  --hostname=dev-control-center.<apps-domain>
```

## Manual Rebuilding (without CI/CD)

If you need to build and deploy manually (e.g. no GitHub Actions access):

```bash
# Backend
podman build --platform linux/amd64 \
  -t quay.io/${QUAY_ORG}/psap-control-center-backend:latest ./backend
podman push quay.io/${QUAY_ORG}/psap-control-center-backend:latest
oc rollout restart deployment/psap-control-center-backend

# Frontend
cd frontend && npm run build && cd ..
podman build --platform linux/amd64 \
  -t quay.io/${QUAY_ORG}/psap-control-center-frontend:latest \
  -f - ./frontend <<'DOCKERFILE'
FROM nginxinc/nginx-unprivileged:alpine
COPY dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
DOCKERFILE
podman push quay.io/${QUAY_ORG}/psap-control-center-frontend:latest
oc rollout restart deployment/psap-control-center-frontend
```

## Authentication

Authentication uses HttpOnly session cookies (JWT) and supports Google
Workspace SSO plus optional local break-glass accounts. Administrator-only
operations are enforced by the backend. Selected read-only APIs, including
Testing run history and pod logs, intentionally remain accessible without
authentication.

Sessions expire after eight hours by default (configurable via `ACCESS_TOKEN_EXPIRE_MINUTES`).

## Teardown

```bash
oc delete project psap-control-center
```

This removes all resources in the namespace.
