# Releasing PSAP Control Center

Releases are proposed automatically and remain subject to human approval. The
initial automated release is `v1.5.0`.

## Version selection

Release Please derives the next semantic version from conventional commit
messages merged into `main`:

- `fix:` proposes a patch release, such as `v1.5.1`.
- `feat:` proposes a minor release, such as `v1.6.0`.
- `feat!:` or a `BREAKING CHANGE:` footer proposes a major release, such as
  `v2.0.0`.
- Other commit types can appear in the changelog but do not independently
  trigger a version increase.

Release Please maintains a release PR containing the proposed version,
changelog, and version files. Merging that PR creates the GitHub release and
publishes backend and frontend images with exact (`v1.5.0`), minor (`v1.5`),
and major (`v1`) tags. Production continues to publish `latest` independently.
Use an exact version or image digest when a deployment must be reproducible.

## Repository configuration

The workflow uses the repository `GITHUB_TOKEN` by default. If release PRs must
trigger other GitHub Actions workflows, configure a `RELEASE_PLEASE_TOKEN`
repository secret containing a suitably scoped GitHub App or personal access
token. GitHub suppresses follow-on workflows for resources created with the
default token.

Quay publication uses the existing `QUAY_CONTROL_CENTER_USERNAME` and
`QUAY_CONTROL_CENTER_PASSWORD` repository secrets.

Application release tags are separate from the versioned OpenShift CLI image
used by the in-cluster image-updater CronJob.
