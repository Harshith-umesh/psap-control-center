import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowPathIcon, StopIcon } from '@heroicons/react/24/outline'
import clsx from 'clsx'
import { isAdmin } from '../stores/authStore'
import { useCptRunGroup, useCptRuns, useStopCptRunGroup, useUpdateCptRunVersion } from '../hooks/useFournos'
import type { CptRunGroup } from '../types'

function statusClass(status: string): string {
  if (status === 'Succeeded') return 'bg-green-50 text-green-700 ring-green-200'
  if (status === 'Running' || status === 'Submitting') return 'bg-blue-50 text-blue-700 ring-blue-200'
  if (status === 'Failed') return 'bg-red-50 text-red-700 ring-red-200'
  if (status === 'Partially failed' || status === 'Partially stopped') return 'bg-amber-50 text-amber-800 ring-amber-200'
  return 'bg-gray-100 text-gray-600 ring-gray-200'
}

function Status({ value, title }: { value: string; title?: string }) {
  return (
    <span title={title} className={clsx('inline-flex rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset', statusClass(value))}>
      {value}
    </span>
  )
}

function Filter({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: string[]
  onChange: (value: string) => void
}) {
  return (
    <label className="flex items-center gap-1.5 text-xs font-medium text-gray-500">
      {label}
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="max-w-48 rounded-md border-gray-300 py-1 pl-2 pr-7 text-xs text-gray-700 shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
      >
        <option value="">All {label.toLowerCase()}s</option>
        {options.map((option) => <option key={option} value={option}>{option}</option>)}
      </select>
    </label>
  )
}

export function CptRunGroupDetails({
  group,
  onVersionUpdated,
}: {
  group: CptRunGroup
  onVersionUpdated: (groupId: string) => void
}) {
  const stopGroup = useStopCptRunGroup()
  const updateVersion = useUpdateCptRunVersion()
  const profiles = group.profiles

  const editVersion = (runId: string) => {
    const next = window.prompt('Correct the version label for this CPT submission:', group.version_label)
    if (next?.trim()) {
      updateVersion.mutate(
        { runId, versionLabel: next.trim() },
        { onSuccess: (result) => onVersionUpdated(result.group_id) },
      )
    }
  }

  return (
    <div className="space-y-5 border-t border-gray-200 bg-gray-50/70 p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-gray-900">Latest result per model and profile</p>
          <p className="mt-1 text-xs text-gray-500">
            New submissions update only the profiles they run; older profile results remain visible until replaced.
          </p>
          <p className="mt-1 max-w-4xl text-xs text-gray-500">
            Status is reported per FournosJob. If one job requests several profiles, its status appears for each requested profile; use submission history to inspect earlier attempts.
          </p>
        </div>
        {isAdmin() && group.active_jobs.length > 0 && (
          <button
            type="button"
            disabled={stopGroup.isPending}
            onClick={() => {
              if (window.confirm(`Request stop for ${group.active_jobs.length} active job(s) in this CPT group?`)) {
                stopGroup.mutate(group.id)
              }
            }}
            className="inline-flex items-center gap-1.5 rounded-md border border-red-200 bg-white px-3 py-1.5 text-xs font-semibold text-red-700 hover:bg-red-50 disabled:opacity-50"
          >
            <StopIcon className="h-4 w-4" /> Stop {group.active_jobs.length} active job{group.active_jobs.length === 1 ? '' : 's'}
          </button>
        )}
      </div>

      <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
        <table className="min-w-full divide-y divide-gray-200 text-left">
          <thead className="bg-gray-100">
            <tr>
              <th className="px-3 py-2 text-xs font-semibold uppercase tracking-wide text-gray-500">Model</th>
              {profiles.map((profile) => (
                <th key={profile} className="min-w-36 px-3 py-2 text-xs font-semibold uppercase tracking-wide text-gray-500">{profile}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {group.models.map((model) => (
              <tr key={model.model_key}>
                <th className="max-w-64 px-3 py-3 text-sm font-medium text-gray-800" title={model.model_name}>
                  <span className="block">{model.model_name}</span>
                  <span className="mt-1 flex flex-wrap items-center gap-2">
                    <Status value={model.status} />
                    <span className="text-[11px] font-normal text-gray-400">{model.attempt_count} attempt(s)</span>
                  </span>
                </th>
                {profiles.map((profile) => {
                  const cell = model.cells[profile]
                  return (
                    <td key={profile} className="px-3 py-3 text-sm">
                      {cell ? (
                        <div className="space-y-1">
                          <Status value={cell.status} title={cell.message || undefined} />
                          <div>
                            {cell.job_name ? (
                              <Link to={`/testing/jobs/${encodeURIComponent(cell.job_name)}`} className="font-mono text-[11px] text-indigo-600 hover:underline">
                                {cell.job_name}
                              </Link>
                            ) : (
                              <span className="text-[11px] text-gray-500" title={cell.message || 'No FournosJob was created for this model/profile.'}>Not submitted</span>
                            )}
                          </div>
                        </div>
                      ) : <span className="text-gray-300">Not run</span>}
                    </td>
                  )
                })}
              </tr>
            ))}
            {!group.models.length && (
              <tr><td colSpan={Math.max(1, profiles.length + 1)} className="px-3 py-8 text-center text-sm text-gray-400">No model jobs recorded.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-semibold text-gray-900">Submission history ({group.attempt_count})</h3>
        <p className="text-xs text-gray-500">Editing a version label changes only the dashboard grouping; it does not modify already submitted FournosJobs.</p>
        <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
          <table className="min-w-full divide-y divide-gray-100 text-left">
            <thead className="bg-gray-100">
              <tr>
                <th className="px-3 py-2 text-xs font-semibold uppercase text-gray-500">Started</th>
                <th className="px-3 py-2 text-xs font-semibold uppercase text-gray-500">Source</th>
                <th className="px-3 py-2 text-xs font-semibold uppercase text-gray-500">Models submitted</th>
                <th className="px-3 py-2 text-xs font-semibold uppercase text-gray-500">Jobs</th>
                <th className="px-3 py-2 text-xs font-semibold uppercase text-gray-500">Status</th>
                <th className="px-3 py-2 text-xs font-semibold uppercase text-gray-500">Owner</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {group.attempts.map((attempt) => (
                <tr key={attempt.id}>
                  <td className="whitespace-nowrap px-3 py-2.5 text-xs text-gray-600">{attempt.created_at ? new Date(attempt.created_at).toLocaleString() : '-'}</td>
                  <td className="max-w-52 truncate px-3 py-2.5 font-mono text-xs text-gray-600" title={attempt.source}>{attempt.source}</td>
                  <td className="px-3 py-2.5 text-xs text-gray-600">{attempt.model_names.join(', ') || '-'}</td>
                  <td className="px-3 py-2.5 text-xs text-gray-600">{attempt.succeeded_jobs} passed · {attempt.failed_jobs} failed · {attempt.stopped_jobs} stopped / {attempt.job_count}</td>
                  <td className="px-3 py-2.5"><Status value={attempt.status} /></td>
                  <td className="px-3 py-2.5 text-xs text-gray-600">{attempt.owner || '-'}</td>
                  <td className="whitespace-nowrap px-3 py-2.5 text-right">
                    {isAdmin() && (
                      <button type="button" onClick={() => editVersion(attempt.id)} className="text-xs font-medium text-indigo-600 hover:text-indigo-800">
                        Edit version label
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {group.attempts.some((attempt) => attempt.jobs.length > 0) && (
        <details className="rounded-md border border-gray-200 bg-white p-3">
          <summary className="cursor-pointer text-xs font-semibold text-gray-600">Jobs in each submission</summary>
          <div className="mt-3 space-y-3">
            {group.attempts.map((attempt) => (
              <div key={attempt.id}>
                <p className="mb-1 text-xs font-medium text-gray-500">{new Date(attempt.created_at).toLocaleString()}</p>
                <div className="flex flex-wrap gap-2">
                  {attempt.jobs.map((job) => (
                    job.name ? (
                      <Link key={job.name} to={`/testing/jobs/${encodeURIComponent(job.name)}`} className="inline-flex items-center gap-1 rounded border border-gray-200 px-2 py-1 text-xs text-indigo-600 hover:bg-indigo-50">
                        {job.model}: {job.status}
                      </Link>
                    ) : (
                      <span key={`${attempt.id}-${job.model}`} className="inline-flex items-center gap-1 rounded border border-gray-200 px-2 py-1 text-xs text-gray-500">
                        {job.model}: {job.status}
                      </span>
                    )
                  ))}
                </div>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  )
}

export default function CptJobsPanel() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [project, setProject] = useState('')
  const [version, setVersion] = useState('')
  const [accelerator, setAccelerator] = useState('')
  const [cluster, setCluster] = useState('')
  const [owner, setOwner] = useState('')
  const [showStopped, setShowStopped] = useState(false)
  const groupId = searchParams.get('group') || ''
  const { data, isLoading, isError, error, refetch } = useCptRuns({
    project, version, accelerator, cluster, owner, show_stopped: showStopped,
  })
  const selectedGroupQuery = useCptRunGroup(groupId)

  const projectOptions = useMemo(() => data?.projects || [], [data?.projects])
  const backParams = new URLSearchParams(searchParams)
  backParams.set('tab', 'cpt')
  backParams.delete('group')

  if (groupId) {
    const group = selectedGroupQuery.data
    return (
      <section className="space-y-4 p-4 sm:p-5">
        <Link to={`/testing?${backParams.toString()}`} className="inline-flex items-center gap-1 text-sm font-medium text-indigo-600 hover:text-indigo-800">
          ← CPT Jobs
        </Link>
        {selectedGroupQuery.isLoading ? (
          <div className="rounded-lg border border-gray-200 bg-white px-4 py-12 text-center text-sm text-gray-400">Loading CPT group…</div>
        ) : selectedGroupQuery.isError || !group ? (
          <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
            <p className="font-medium">Could not load this CPT group</p>
            <p className="mt-1">{selectedGroupQuery.error instanceof Error ? selectedGroupQuery.error.message : 'The group may have been removed.'}</p>
            <button type="button" onClick={() => void selectedGroupQuery.refetch()} className="mt-2 font-medium underline">Retry</button>
          </div>
        ) : (
          <>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-xl font-semibold text-gray-900">{group.pipeline_key} · {group.version_label}</h2>
                <p className="mt-1 text-sm text-gray-500">{group.cluster || 'Unknown cluster'} · {group.accelerator || 'Unknown accelerator'} / {group.engine || 'Unknown engine'}</p>
              </div>
              <Status value={group.status} />
            </div>

            <dl className="grid gap-x-6 gap-y-3 rounded-lg border border-gray-200 bg-white p-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
              <div><dt className="text-xs font-medium uppercase text-gray-400">Project</dt><dd className="mt-1 text-gray-700">{group.project}</dd></div>
              <div><dt className="text-xs font-medium uppercase text-gray-400">CPT pipeline</dt><dd className="mt-1 text-gray-700">{group.pipeline_key}</dd></div>
              <div><dt className="text-xs font-medium uppercase text-gray-400">Benchmark version</dt><dd className="mt-1 font-semibold text-gray-800">{group.version_label}</dd></div>
              <div><dt className="text-xs font-medium uppercase text-gray-400">Cluster</dt><dd className="mt-1 text-gray-700">{group.cluster || '-'}</dd></div>
              <div><dt className="text-xs font-medium uppercase text-gray-400">Accelerator / engine</dt><dd className="mt-1 text-gray-700">{group.accelerator || '-'} / {group.engine || '-'}</dd></div>
              <div><dt className="text-xs font-medium uppercase text-gray-400">Engine image version</dt><dd className="mt-1 break-all font-mono text-xs text-gray-700">{group.engine_version || '-'}</dd></div>
              <div><dt className="text-xs font-medium uppercase text-gray-400">Forge execution pipeline</dt><dd className="mt-1 text-gray-700">{group.fournos_pipeline || '-'}</dd></div>
              <div><dt className="text-xs font-medium uppercase text-gray-400">Forge source(s)</dt><dd className="mt-1 break-all font-mono text-xs text-gray-700">{group.sources.join(', ') || '-'}</dd></div>
              <div><dt className="text-xs font-medium uppercase text-gray-400">Owner(s)</dt><dd className="mt-1 text-gray-700">{group.owner_label || '-'}</dd></div>
              <div><dt className="text-xs font-medium uppercase text-gray-400">Submissions</dt><dd className="mt-1 text-gray-700">{group.attempt_count}</dd></div>
              <div><dt className="text-xs font-medium uppercase text-gray-400">Latest submission</dt><dd className="mt-1 text-gray-700">{group.latest_created_at ? new Date(group.latest_created_at).toLocaleString() : '-'}</dd></div>
            </dl>

            <CptRunGroupDetails
              group={group}
              onVersionUpdated={(updatedGroupId) => {
                const next = new URLSearchParams(searchParams)
                next.set('tab', 'cpt')
                next.set('group', updatedGroupId)
                setSearchParams(next, { replace: true })
              }}
            />
          </>
        )}
      </section>
    )
  }

  return (
    <section className="space-y-4 p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">CPT Jobs</h2>
          <p className="mt-1 max-w-4xl text-sm text-gray-500">
            Group CPT submissions by pipeline and version. Each group keeps the newest result per model/profile and preserves every submission in its history, including archived submissions from the FourNos dashboard.
          </p>
        </div>
        <button type="button" onClick={() => void refetch()} className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700">
          <ArrowPathIcon className="h-4 w-4" /> Refresh
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Filter label="Project" value={project} options={projectOptions} onChange={setProject} />
        <Filter label="Version" value={version} options={data?.filter_options.versions || []} onChange={setVersion} />
        <Filter label="Accelerator" value={accelerator} options={data?.filter_options.accelerators || []} onChange={setAccelerator} />
        <Filter label="Cluster" value={cluster} options={data?.filter_options.clusters || []} onChange={setCluster} />
        <Filter label="Owner" value={owner} options={data?.filter_options.owners || []} onChange={setOwner} />
        <button type="button" onClick={() => setShowStopped((value) => !value)} className="ml-auto rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50">
          {showStopped ? 'Hide stopped groups' : `Show stopped groups${data?.hidden_stopped_count ? ` (${data.hidden_stopped_count})` : ''}`}
        </button>
      </div>

      {isError && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          <p className="font-medium">Could not load CPT runs</p>
          <p className="mt-1 text-red-600">{error instanceof Error ? error.message : 'The API did not respond.'}</p>
          <button type="button" onClick={() => void refetch()} className="mt-2 font-medium underline">Retry</button>
        </div>
      )}

      <div className="overflow-x-auto rounded-lg border border-gray-200">
        <table className="min-w-[1450px] w-full divide-y divide-gray-200 text-left">
          <thead className="bg-gray-100">
            <tr>
              {['CPT Pipeline', 'Version', 'Forge Source(s)', 'Cluster', 'Accelerator / Engine', 'Engine Version (Image)', 'Model Jobs', 'Status', 'Latest Results', 'Submissions', 'Latest Submission', 'Owner(s)'].map((heading) => (
                <th key={heading} className="px-3 py-3 text-[11px] font-semibold uppercase tracking-wide text-gray-500">{heading}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200 bg-white">
            {isLoading ? (
              <tr><td colSpan={12} className="px-4 py-12 text-center text-sm text-gray-400">Loading CPT runs…</td></tr>
            ) : data?.groups.length ? data.groups.map((group: CptRunGroup) => {
              const passedModels = group.models.filter((model) => model.submitted && model.status === 'Succeeded').length
              const failedModels = group.models.filter((model) => model.submitted && (model.status === 'Failed' || model.status === 'Partially failed')).length
              const stoppedModels = group.models.filter((model) => model.submitted && (model.status === 'Stopped' || model.status === 'Partially stopped')).length
              const activeModels = group.models.filter((model) => model.submitted && ['Running', 'Submitting', 'Pending', 'Resolving', 'Admitted'].includes(model.status)).length
              const groupParams = new URLSearchParams(searchParams)
              groupParams.set('tab', 'cpt')
              groupParams.set('group', group.id)
              return (
                <tr key={group.id} className="align-top hover:bg-gray-50">
                  <td className="px-3 py-3 text-sm font-medium"><Link to={`/testing?${groupParams.toString()}`} className="text-indigo-600 hover:underline">{group.pipeline_key}</Link></td>
                  <td className="px-3 py-3 text-sm font-semibold text-gray-800">{group.version_label}</td>
                  <td className="max-w-40 truncate px-3 py-3 font-mono text-xs text-gray-600" title={group.sources.join(', ')}>{group.source_label}</td>
                  <td className="px-3 py-3 text-sm text-gray-700">{group.cluster || '-'}</td>
                  <td className="px-3 py-3 text-sm text-gray-700">{group.accelerator || '-'} / {group.engine || '-'}</td>
                  <td className="max-w-56 break-all px-3 py-3 font-mono text-[11px] text-gray-600" title={group.engine_version}>{group.engine_version || 'Default / unspecified'}</td>
                  <td className="min-w-44 px-3 py-3 text-sm text-gray-700">
                    <span className="block text-xs text-green-700">Passed {passedModels}</span>
                    <span className="block text-xs text-red-700">Failed {failedModels}</span>
                    <span className="block text-xs text-gray-500">Stopped {stoppedModels}{activeModels ? ` · Running ${activeModels}` : ''}</span>
                    <span className="mt-1 block text-xs text-gray-400">{group.submitted_model_count}/{group.configured_model_count ?? group.submitted_model_count} configured models submitted</span>
                    {group.not_submitted_model_count > 0 && <span className="block text-xs text-amber-700">{group.not_submitted_model_count} model(s) not submitted</span>}
                  </td>
                  <td className="px-3 py-3"><Status value={group.status} /></td>
                  <td className="min-w-44 px-3 py-3 text-sm text-gray-700">
                    {group.total_profile_count ? `${group.passed_profile_count}/${group.total_profile_count} latest model/profile results succeeded` : 'No model/profile results recorded yet'}
                    <span className="mt-1 block text-xs text-gray-400">{group.failed_profile_count} failed · {group.stopped_profile_count} stopped · {group.active_profile_count} active</span>
                  </td>
                  <td className="px-3 py-3 text-sm text-gray-700">{group.attempt_count}</td>
                  <td className="min-w-36 px-3 py-3 text-sm text-gray-600">{group.latest_created_at ? new Date(group.latest_created_at).toLocaleString() : '-'}</td>
                  <td className="px-3 py-3 text-sm text-gray-700">{group.owner_label}</td>
                </tr>
              )
            }) : !isError ? (
              <tr><td colSpan={12} className="px-4 py-12 text-center text-sm text-gray-400">No tracked CPT submissions match these filters yet.</td></tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </section>
  )
}
