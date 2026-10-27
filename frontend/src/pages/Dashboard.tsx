import { Link } from 'react-router-dom'
import {
  ServerStackIcon,
  CpuChipIcon,
  CheckCircleIcon,
  FireIcon,
  LockClosedIcon,
  ExclamationTriangleIcon,
  CurrencyDollarIcon,
} from '@heroicons/react/24/outline'
import { useQueries } from '@tanstack/react-query'
import { useClusters } from '../hooks/useClusters'
import { useHearthClusters } from '../hooks/useHearth'
import { useFournosLiveJobSummary } from '../hooks/useFournos'
import { clusterApi } from '../services/api'
import { format } from 'date-fns'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import type { HearthCluster, GpuAllocationStatus, ClusterCost } from '../types'
import GpuDonutChart from '../components/GpuDonutChart'

export default function Dashboard() {
  const { data: clustersData, isLoading: clustersLoading } = useClusters()
  const {
    data: liveJobSummary,
    isLoading: liveJobSummaryLoading,
    isError: liveJobSummaryError,
  } = useFournosLiveJobSummary()
  
  const { data: hearthData } = useHearthClusters()
  const hearthClusters = hearthData?.clusters || []
  const hearthAvailable = hearthData?.available ?? false

  const clusters = clustersData?.clusters || []
  const liveJobStatuses = liveJobSummary?.by_status || {}
  const waitingJobs = ['Pending', 'Resolving', 'Admitted'].reduce(
    (total, status) => total + (liveJobStatuses[status] || 0),
    0,
  )
  const otherActiveJobs = Math.max(
    0,
    (liveJobSummary?.total || 0) - (liveJobStatuses.Running || 0) - waitingJobs,
  )

  const healthyClusters = clusters.filter((c) => c.status === 'healthy').length

  const healthyClusterIds = clusters.filter((c) => c.status === 'healthy').map((c) => c.id)
  const gpuStatusQueries = useQueries({
    queries: healthyClusterIds.map((id) => ({
      queryKey: ['gpu-status', id],
      queryFn: () => clusterApi.getGpuStatus(id),
      staleTime: 30_000,
      refetchInterval: 60_000,
    })),
  })

  const gpuTotals = gpuStatusQueries.reduce(
    (acc, q) => {
      const data = q.data as GpuAllocationStatus | undefined
      if (data) {
        acc.total += data.total_gpus
        acc.allocated += data.allocated_gpus
      }
      return acc
    },
    { total: 0, allocated: 0 }
  )
  const gpuStatusLoading = gpuStatusQueries.some((q) => q.isLoading)
  const totalGpus = gpuTotals.total || clusters.reduce((sum, c) => sum + parseInt(c.gpu_count || '0'), 0)

  const ibmClusterIds = clusters.filter((c) => c.provider === 'ibm').map((c) => c.id)
  const costQueries = useQueries({
    queries: ibmClusterIds.map((id) => ({
      queryKey: ['clusterCost', id],
      queryFn: () => clusterApi.getCost(id),
      staleTime: 60_000,
    })),
  })
  const costByCluster = ibmClusterIds.reduce<Record<string, ClusterCost | undefined>>((acc, id, i) => {
    const costs = costQueries[i]?.data as ClusterCost[] | undefined
    acc[id] = costs?.[0]
    return acc
  }, {})
  const costCurrency = (() => {
    for (const q of costQueries) {
      const costs = q.data as ClusterCost[] | undefined
      if (costs?.[0]?.currency) return costs[0].currency
    }
    return 'USD'
  })()

  const monthlyCostData = (() => {
    const byMonth: Record<string, number> = {}
    for (const q of costQueries) {
      const costs = q.data as ClusterCost[] | undefined
      if (!costs) continue
      for (const c of costs) {
        if (c.error || c.total_cost == null || !c.billing_month) continue
        byMonth[c.billing_month] = (byMonth[c.billing_month] || 0) + c.total_cost
      }
    }
    return Object.entries(byMonth)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([month, cost]) => ({ month, cost: Math.round(cost) }))
  })()

  const stats = [
    {
      name: 'Total Clusters',
      value: clusters.length,
      icon: ServerStackIcon,
      color: 'bg-blue-500',
      href: '/clusters',
    },
    {
      name: 'Healthy Clusters',
      value: healthyClusters,
      icon: CheckCircleIcon,
      color: 'bg-green-500',
      href: '/clusters',
    },
  ]

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 font-display">Overview</h1>
          <p className="mt-1 text-sm text-gray-500">
            Cluster infrastructure, GPU capacity, and active workload at a glance
          </p>
        </div>
        <div className="flex gap-3">
          <Link to="/clusters" className="btn-secondary">
            View Clusters
          </Link>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
        {stats.map((stat) => (
          <Link
            key={stat.name}
            to={stat.href}
            className="card p-6 hover:shadow-md transition-shadow"
          >
            <div className="flex items-center">
              <div className={`${stat.color} rounded-lg p-3`}>
                <stat.icon className="h-6 w-6 text-white" />
              </div>
              <div className="ml-4">
                <p className="text-sm font-medium text-gray-500">{stat.name}</p>
                <p className="text-2xl font-semibold text-gray-900">
                  {clustersLoading ? '...' : stat.value}
                </p>
              </div>
            </div>
          </Link>
        ))}
        <Link
          to="/clusters"
          className="card p-6 hover:shadow-md transition-shadow"
        >
          <div className="flex items-center gap-4">
            {clustersLoading || gpuStatusLoading ? (
              <div className="bg-purple-500 rounded-lg p-3">
                <CpuChipIcon className="h-6 w-6 text-white" />
              </div>
            ) : (
              <GpuDonutChart used={gpuTotals.allocated} total={totalGpus} size={64} strokeWidth={7} />
            )}
            <div>
              <p className="text-sm font-medium text-gray-500">GPU Utilization</p>
              {clustersLoading || gpuStatusLoading ? (
                <p className="text-2xl font-semibold text-gray-900">...</p>
              ) : (
                <div className="flex items-baseline gap-1.5">
                  <p className="text-2xl font-semibold text-gray-900">{gpuTotals.allocated}</p>
                  <p className="text-sm text-gray-400">/</p>
                  <p className="text-lg text-gray-500">{totalGpus}</p>
                </div>
              )}
              <p className="text-xs text-gray-400 mt-0.5">used / total</p>
            </div>
          </div>
        </Link>
        {ibmClusterIds.length > 0 && monthlyCostData.length > 0 && (
          <div className="card p-6 lg:col-span-2">
            <div className="flex items-center gap-2 mb-3">
              <CurrencyDollarIcon className="h-5 w-5 text-emerald-600" />
              <p className="text-sm font-medium text-gray-500">Monthly Cost</p>
            </div>
            <ResponsiveContainer width="100%" height={80}>
              <LineChart data={monthlyCostData} margin={{ top: 4, right: 8, bottom: 0, left: 8 }}>
                <XAxis
                  dataKey="month"
                  tick={{ fontSize: 11, fill: '#9ca3af' }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fontSize: 11, fill: '#9ca3af' }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => {
                    const symbol = (0).toLocaleString(undefined, { style: 'currency', currency: costCurrency }).replace(/[\d.,\s]/g, '')
                    return `${symbol}${(v / 1000).toFixed(0)}k`
                  }}
                  width={45}
                />
                <Tooltip
                  formatter={(value) => [
                    Number(value).toLocaleString(undefined, { style: 'currency', currency: costCurrency, maximumFractionDigits: 0 }),
                    'Total',
                  ]}
                  contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #e5e7eb' }}
                />
                <Line
                  type="monotone"
                  dataKey="cost"
                  stroke="#059669"
                  strokeWidth={2}
                  dot={{ r: 4, fill: '#059669' }}
                  activeDot={{ r: 6 }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      <section className="card p-6" aria-labelledby="active-jobs-heading">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 id="active-jobs-heading" className="text-lg font-semibold text-gray-900">
              Active Fournos Jobs
            </h2>
            <p className="mt-1 text-sm text-gray-500">
              A count-only snapshot. Open Live Jobs for individual runs, progress, and actions.
            </p>
          </div>
          <Link to="/testing?tab=live" className="text-sm font-medium text-primary-600 hover:text-primary-700">
            View live jobs →
          </Link>
        </div>

        {liveJobSummaryError ? (
          <p className="mt-5 rounded-md bg-amber-50 px-4 py-3 text-sm text-amber-800">
            The live job summary is temporarily unavailable. You can still check the Live Jobs page.
          </p>
        ) : (
          <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
            {[
              { label: 'Active jobs', value: liveJobSummary?.total },
              { label: 'Running', value: liveJobStatuses.Running || 0 },
              { label: 'Waiting to start', value: waitingJobs },
            ].map((item) => (
              <div key={item.label} className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                <p className="text-sm font-medium text-gray-500">{item.label}</p>
                <p className="mt-1 text-2xl font-semibold text-gray-900">
                  {liveJobSummaryLoading ? '…' : item.value ?? 0}
                </p>
                {item.label === 'Waiting to start' && (
                  <p className="mt-0.5 text-xs text-gray-400">Pending, resolving, or admitted</p>
                )}
              </div>
            ))}
          </div>
        )}
        {!liveJobSummaryError && otherActiveJobs > 0 && (
          <p className="mt-3 text-xs text-gray-500">
            {otherActiveJobs} additional active {otherActiveJobs === 1 ? 'job is' : 'jobs are'} in another phase.
          </p>
        )}
      </section>

      <div className="grid grid-cols-1 gap-6">
        <div className="card">
          <div className="px-6 py-4 border-b border-gray-200">
            <h2 className="text-lg font-semibold text-gray-900">Cluster Status</h2>
          </div>
          <div className="divide-y divide-gray-200">
            {clustersLoading ? (
              <div className="px-6 py-8 text-center text-gray-500">Loading clusters...</div>
            ) : clusters.length === 0 ? (
              <div className="px-6 py-8 text-center">
                <ServerStackIcon className="mx-auto h-12 w-12 text-gray-400" />
                <p className="mt-2 text-sm text-gray-500">No clusters configured</p>
                <Link to="/clusters" className="mt-3 inline-block text-sm text-primary-600 hover:text-primary-700">
                  Add your first cluster
                </Link>
              </div>
            ) : (
              clusters.slice(0, 5).map((cluster) => {
                const gpuData = gpuStatusQueries.find(
                  (_q, i) => healthyClusterIds[i] === cluster.id
                )?.data as GpuAllocationStatus | undefined
                const clusterTotal = gpuData?.total_gpus ?? parseInt(cluster.gpu_count || '0')
                const clusterUsed = gpuData?.allocated_gpus ?? 0
                const gpuNodeCount = gpuData?.gpu_types.reduce(
                  (total, gpuType) => total + gpuType.node_count,
                  0,
                )
                const cost = costByCluster[cluster.id]

                return (
                  <Link
                    key={cluster.id}
                    to={`/clusters/${cluster.id}`}
                    className="flex items-center justify-between px-6 py-4 hover:bg-gray-50 transition-colors"
                  >
                    <div className="flex items-center gap-3">
                      <div
                        className={`h-3 w-3 rounded-full ${
                          cluster.status === 'healthy'
                            ? 'bg-green-500'
                            : cluster.status === 'error'
                            ? 'bg-orange-500'
                            : 'bg-yellow-500'
                        }`}
                      />
                      <div>
                        <p className="font-medium text-gray-900">{cluster.name}</p>
                        <p className="text-sm text-gray-500">
                          {gpuNodeCount ?? '?'} GPU nodes · {clusterUsed}/{clusterTotal} GPUs
                          {cluster.gpu_type && <span className="ml-1">({cluster.gpu_type})</span>}
                          {cost && !cost.error && cost.total_cost != null && (
                            <span className="ml-1">
                              · {cost.total_cost.toLocaleString(undefined, { style: 'currency', currency: cost.currency, maximumFractionDigits: 0 })}
                            </span>
                          )}
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-3">
                      {clusterTotal > 0 && (
                        <GpuDonutChart used={clusterUsed} total={clusterTotal} size={40} strokeWidth={5} />
                      )}
                      <span
                        className={`badge ${
                          cluster.status === 'healthy'
                            ? 'badge-success'
                            : cluster.status === 'error'
                            ? 'badge-error'
                            : 'badge-warning'
                        }`}
                      >
                        {cluster.status}
                      </span>
                    </div>
                  </Link>
                )
              })
            )}
          </div>
          {clusters.length > 5 && (
            <div className="px-6 py-3 bg-gray-50 border-t border-gray-200">
              <Link to="/clusters" className="text-sm text-primary-600 hover:text-primary-700">
                View all {clusters.length} clusters →
              </Link>
            </div>
          )}
        </div>

      </div>

      {/* Hearth GPU Cluster Status */}
      {hearthAvailable && hearthClusters.length > 0 && (
        <div className="card">
          <div className="px-6 py-4 border-b border-gray-200">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <FireIcon className="h-5 w-5 text-orange-500" />
                <h2 className="text-lg font-semibold text-gray-900">Hearth GPU Clusters</h2>
              </div>
              <span className="text-xs text-gray-400 bg-gray-100 px-2 py-1 rounded-full">
                via FournosCluster CRDs
              </span>
            </div>
            <p className="text-sm text-gray-500 mt-1">
              Auto-discovered clusters with GPU hardware and Kueue quota status
            </p>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Cluster
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    GPU Hardware
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Kubeconfig
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Lock Status
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Owner
                  </th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {hearthClusters.map((hc: HearthCluster) => (
                  <tr key={hc.name} className="hover:bg-gray-50">
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex items-center gap-2">
                        <ServerStackIcon className="h-5 w-5 text-gray-400" />
                        <span className="font-medium text-gray-900">{hc.name}</span>
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      {hc.gpu_summary ? (
                        <div className="flex items-center gap-2">
                          <CpuChipIcon className="h-4 w-4 text-purple-500" />
                          <span className="text-sm font-medium text-gray-900">{hc.gpu_summary}</span>
                        </div>
                      ) : hc.hardware?.last_error ? (
                        <div className="flex items-center gap-1 text-sm text-orange-600">
                          <ExclamationTriangleIcon className="h-4 w-4" />
                          Discovery error
                        </div>
                      ) : (
                        <span className="text-sm text-gray-400">Pending discovery</span>
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span className={`badge ${
                        hc.kubeconfig_status === 'Valid' ? 'badge-success' :
                        hc.kubeconfig_status === 'Unreachable' ? 'badge-error' :
                        'badge-warning'
                      }`}>
                        {hc.kubeconfig_status || 'Unknown'}
                      </span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      {hc.locked ? (
                        <span className="flex items-center gap-1 text-sm font-medium text-orange-600">
                          <LockClosedIcon className="h-4 w-4" />
                          Locked
                          {hc.lock_expires_at && (
                            <span className="text-xs text-gray-400 ml-1">
                              (expires {format(new Date(hc.lock_expires_at), 'MMM d, h:mm a')})
                            </span>
                          )}
                        </span>
                      ) : (
                        <span className="text-sm text-green-600">Available</span>
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      {hc.owner || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="px-6 py-3 bg-gray-50 border-t border-gray-200 text-sm text-gray-500">
            {hearthClusters.length} cluster{hearthClusters.length !== 1 ? 's' : ''} discovered
            {' · '}
            {hearthClusters.reduce((sum, c) => sum + (c.hardware?.total_gpus || 0), 0)} total GPUs
            {' · '}
            {hearthClusters.filter(c => c.locked).length} locked
          </div>
        </div>
      )}

    </div>
  )
}
