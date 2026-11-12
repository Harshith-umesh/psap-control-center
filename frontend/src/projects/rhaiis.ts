export interface ForgeProjectUiSettings {
  requiresBuildSource: boolean
  buildSourceLabel: string
  defaultPipeline: string
  loadsPublishedReleases: boolean
}

const DEFAULT_SETTINGS: ForgeProjectUiSettings = {
  requiresBuildSource: false,
  buildSourceLabel: 'Pull Request',
  defaultPipeline: 'forge-test-only',
  loadsPublishedReleases: false,
}

export const RHAIIS_UI_SETTINGS: ForgeProjectUiSettings = {
  requiresBuildSource: true,
  buildSourceLabel: 'Build Source',
  defaultPipeline: 'forge-full',
  loadsPublishedReleases: true,
}

export function getForgeProjectUiSettings(project: string): ForgeProjectUiSettings {
  return project === 'rhaiis' ? RHAIIS_UI_SETTINGS : DEFAULT_SETTINGS
}
