import {
  Fragment,
  useState,
  useEffect,
  useCallback,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import { Outlet, NavLink, useLocation } from 'react-router-dom'
import { Dialog, Transition } from '@headlessui/react'
import {
  Bars3Icon,
  HomeIcon,
  ServerStackIcon,
  BeakerIcon,
  ChartBarIcon,
  ArrowTopRightOnSquareIcon,
  CurrencyDollarIcon,
  Cog6ToothIcon,
  WrenchScrewdriverIcon,
  LockClosedIcon,
  LockOpenIcon,
  FireIcon,
} from '@heroicons/react/24/outline'
import clsx from 'clsx'
import LoginModal from './LoginModal'
import { useAuthConfig } from '../hooks/useAuthConfig'
import HearthConnectModal from './HearthConnectModal'
import { isAuthenticated, isAdmin, getDisplayName, clearSession } from '../stores/authStore'
import { authApi } from '../services/api'
import { useHearthStatus, useDisconnectHearth } from '../hooks/useHearth'

type NavigationItem = {
  name: string
  href: string
  icon: typeof HomeIcon
  adminOnly?: boolean
  comingSoon?: boolean
  badge?: string
}

const navigation: NavigationItem[] = [
  { name: 'Overview', href: '/dashboard', icon: HomeIcon },
  { name: 'Clusters', href: '/clusters', icon: ServerStackIcon },
  { name: 'Fournos Dashboard', href: '/testing?tab=live', icon: BeakerIcon },
  { name: 'Cost Explorer', href: '/cost-explorer', icon: CurrencyDollarIcon, adminOnly: true },
  { name: 'Settings', href: '/settings', icon: Cog6ToothIcon, adminOnly: true },
]

const fournDashboardSubnav = [
  { id: 'jobs', name: 'Jobs', href: '/testing?tab=live' },
  { id: 'submit', name: 'Submit', href: '/testing?tab=submit' },
  { id: 'cpt', name: 'CPT Jobs', href: '/testing?tab=cpt' },
] as const

const performanceDashboardSubnav = [
  { name: 'Production', href: 'https://aidash.app.intlab.redhat.com' },
    { name: 'Staging', href: 'https://staging-aidash.apps.ocp4.intlab.redhat.com/' },
] as const

const psapToolsSubnav = [
  { name: 'PSAP Report Hub', href: 'https://reports.psap.aws.rhperfscale.org' },
  { name: 'ConfigIQ', href: 'https://configiq.apps.ocp4.intlab.redhat.com' },
  { name: 'Servit Studio', href: 'https://inftune-launcher-ui-inftune.apps.ocp4.intlab.redhat.com' },
] as const

const SIDEBAR_WIDTH_STORAGE_KEY = 'psap-control-center-sidebar-width'
const DEFAULT_SIDEBAR_WIDTH = 288
const MIN_SIDEBAR_WIDTH = 240
const MAX_SIDEBAR_WIDTH = 480

function clampSidebarWidth(width: number) {
  return Math.min(MAX_SIDEBAR_WIDTH, Math.max(MIN_SIDEBAR_WIDTH, width))
}

function PerformanceDashboardSection({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <li>
      <div className="flex items-center gap-x-3 border-l-[3px] border-l-transparent px-6 py-3 text-lg font-medium text-gray-300">
        <ChartBarIcon className="h-5 w-5 shrink-0 text-gray-500" />
        <span>Performance Dashboard</span>
      </div>
      <ul className="space-y-0.5 pb-2">
        {performanceDashboardSubnav.map((item) => (
          <li key={item.name}>
            <a
              href={item.href}
              target="_blank"
              rel="noopener noreferrer"
              onClick={onNavigate}
              aria-label={`${item.name} Performance Dashboard (opens in a new tab)`}
              className="group ml-9 mr-3 flex items-center gap-2 rounded-md px-3 py-2 text-lg text-gray-400 transition-colors hover:bg-white/5 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#73BCF7]"
            >
              <span>{item.name}</span>
              <ArrowTopRightOnSquareIcon className="ml-auto h-4 w-4 shrink-0 text-gray-500 transition-colors group-hover:text-gray-300" />
            </a>
          </li>
        ))}
      </ul>
    </li>
  )
}

function PsapToolsSection({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <li>
      <div className="flex items-center gap-x-3 border-l-[3px] border-l-transparent px-6 py-3 text-lg font-medium text-gray-300">
        <WrenchScrewdriverIcon className="h-5 w-5 shrink-0 text-gray-500" />
        <span>Tools</span>
      </div>
      <ul className="space-y-0.5 pb-2">
        {psapToolsSubnav.map((item) => (
          <li key={item.name}>
            <a
              href={item.href}
              target="_blank"
              rel="noopener noreferrer"
              onClick={onNavigate}
              aria-label={`${item.name} (opens in a new tab)`}
              className="group ml-9 mr-3 flex items-center gap-2 rounded-md px-3 py-2 text-lg text-gray-400 transition-colors hover:bg-white/5 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#73BCF7]"
            >
              <span>{item.name}</span>
              <ArrowTopRightOnSquareIcon className="ml-auto h-4 w-4 shrink-0 text-gray-500 transition-colors group-hover:text-gray-300" />
            </a>
          </li>
        ))}
      </ul>
    </li>
  )
}

function HearthIndicator({
  onConnectClick,
}: {
  onConnectClick: () => void
}) {
  const { data: hearthStatus } = useHearthStatus()
  const disconnectHearth = useDisconnectHearth()

  const available = hearthStatus?.available ?? false
  const configured = hearthStatus?.configured ?? false

  if (!configured) {
    return (
      <button
        onClick={onConnectClick}
        className="w-full rounded-lg border border-dashed border-gray-600 p-3 hover:border-orange-400 hover:bg-white/5 transition-colors group"
      >
        <div className="flex items-center gap-2.5">
          <div className="h-8 w-8 rounded-lg bg-white/10 group-hover:bg-orange-500/20 flex items-center justify-center transition-colors">
            <FireIcon className="h-4 w-4 text-gray-400 group-hover:text-orange-400 transition-colors" />
          </div>
          <div className="text-left">
            <p className="text-xs font-semibold text-gray-300 group-hover:text-orange-300">Connect Hearth</p>
            <p className="text-[10px] text-gray-500">GPU cluster operator</p>
          </div>
        </div>
      </button>
    )
  }

  return (
    <div className={clsx(
      'rounded-lg p-3 border',
      available
        ? 'bg-white/5 border-green-500/30'
        : 'bg-white/5 border-orange-500/30'
    )}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className={clsx(
            'h-8 w-8 rounded-lg flex items-center justify-center',
            available ? 'bg-green-500/20' : 'bg-orange-500/20'
          )}>
            <FireIcon className={clsx(
              'h-4 w-4',
              available ? 'text-green-400' : 'text-orange-400'
            )} />
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <p className="text-xs font-semibold text-gray-200">Hearth</p>
              <span className="relative flex h-2 w-2">
                {available && (
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
                )}
                <span className={clsx(
                  'relative inline-flex rounded-full h-2 w-2',
                  available ? 'bg-green-400' : 'bg-orange-400'
                )} />
              </span>
            </div>
            <p className="text-[10px] text-gray-400">
              {available
                ? `${hearthStatus?.cluster_count || 0} clusters · ${hearthStatus?.total_gpus || 0} GPUs`
                : 'Connection error'}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={onConnectClick}
            className="text-[10px] text-gray-400 hover:text-orange-300 transition-colors px-1.5 py-0.5 rounded hover:bg-white/10"
            title="Change Hearth / admin cluster connection"
          >
            Change
          </button>
          <button
            onClick={() => disconnectHearth.mutate()}
            disabled={disconnectHearth.isPending}
            className="text-[10px] text-gray-500 hover:text-orange-400 transition-colors px-1.5 py-0.5 rounded hover:bg-white/10"
            title="Disconnect Hearth"
          >
            {disconnectHearth.isPending ? '...' : 'Disconnect'}
          </button>
        </div>
      </div>
    </div>
  )
}

const envBanner = import.meta.env.VITE_ENV_BANNER as string | undefined

export default function Layout() {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    if (typeof window === 'undefined') return DEFAULT_SIDEBAR_WIDTH

    try {
      const savedWidth = window.localStorage.getItem(SIDEBAR_WIDTH_STORAGE_KEY)
      if (savedWidth === null) return DEFAULT_SIDEBAR_WIDTH

      const parsedWidth = Number(savedWidth)
      return Number.isFinite(parsedWidth) ? clampSidebarWidth(parsedWidth) : DEFAULT_SIDEBAR_WIDTH
    } catch {
      return DEFAULT_SIDEBAR_WIDTH
    }
  })
  const [isSidebarResizing, setIsSidebarResizing] = useState(false)
  const [loginOpen, setLoginOpen] = useState(false)
  const [hearthConnectOpen, setHearthConnectOpen] = useState(false)
  const [authed, setAuthed] = useState(isAuthenticated())
  const location = useLocation()
  const authConfigQuery = useAuthConfig()
  const currentTestingTab = new URLSearchParams(location.search).get('tab') || 'live'
  const isTestingPage = location.pathname === '/testing'
  const isTestingJobDetail = location.pathname.startsWith('/testing/jobs/')
  const isFournosSubnavActive = (id: (typeof fournDashboardSubnav)[number]['id']) => {
    if (id === 'jobs') {
      return isTestingJobDetail || (isTestingPage && (currentTestingTab === 'live' || currentTestingTab === 'history'))
    }
    return isTestingPage && currentTestingTab === id
  }

  const { data: hearthStatus } = useHearthStatus()
  const admin = isAdmin()
  const filteredNav = navigation.filter(item => !item.adminOnly || admin)

  const syncAuth = useCallback(() => setAuthed(isAuthenticated()), [])

  useEffect(() => {
    window.addEventListener('auth-change', syncAuth)
    return () => window.removeEventListener('auth-change', syncAuth)
  }, [syncAuth])

  useEffect(() => {
    try {
      window.localStorage.setItem(SIDEBAR_WIDTH_STORAGE_KEY, String(sidebarWidth))
    } catch {
      // Keep the sidebar usable when browser storage is unavailable.
    }
  }, [sidebarWidth])

  useEffect(() => {
    if (!isSidebarResizing) return

    const previousCursor = document.body.style.cursor
    const previousUserSelect = document.body.style.userSelect
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    const handlePointerMove = (event: PointerEvent) => {
      setSidebarWidth(clampSidebarWidth(event.clientX))
    }
    const stopResizing = () => setIsSidebarResizing(false)

    window.addEventListener('pointermove', handlePointerMove)
    window.addEventListener('pointerup', stopResizing)
    window.addEventListener('pointercancel', stopResizing)

    return () => {
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousUserSelect
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', stopResizing)
      window.removeEventListener('pointercancel', stopResizing)
    }
  }, [isSidebarResizing])

  const startSidebarResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return
    event.preventDefault()
    setIsSidebarResizing(true)
  }

  const resizeSidebarWithKeyboard = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault()
      const direction = event.key === 'ArrowRight' ? 1 : -1
      setSidebarWidth((width) => clampSidebarWidth(width + direction * 16))
    } else if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault()
      setSidebarWidth(event.key === 'Home' ? MIN_SIDEBAR_WIDTH : MAX_SIDEBAR_WIDTH)
    }
  }

  const handleLogout = () => {
    clearSession()
    authApi.logout().catch(() => {})
  }

  return (
    <div
      className="min-h-screen bg-[#f0f0f0]"
      style={{ '--sidebar-width': `${sidebarWidth}px` } as CSSProperties}
    >
      {envBanner && (
        <div className="fixed top-0 inset-x-0 z-[60] bg-amber-500 text-black text-center text-xs font-bold tracking-widest uppercase py-1 select-none">
          {envBanner}
        </div>
      )}
      <Transition.Root show={sidebarOpen} as={Fragment}>
        <Dialog as="div" className="relative z-50 lg:hidden" onClose={setSidebarOpen}>
          <Transition.Child
            as={Fragment}
            enter="transition-opacity ease-linear duration-300"
            enterFrom="opacity-0"
            enterTo="opacity-100"
            leave="transition-opacity ease-linear duration-300"
            leaveFrom="opacity-100"
            leaveTo="opacity-0"
          >
            <div className="fixed inset-0 bg-gray-900/80" />
          </Transition.Child>

          <div className="fixed inset-0 flex">
            <Transition.Child
              as={Fragment}
              enter="transition ease-in-out duration-300 transform"
              enterFrom="-translate-x-full"
              enterTo="translate-x-0"
              leave="transition ease-in-out duration-300 transform"
              leaveFrom="translate-x-0"
              leaveTo="-translate-x-full"
            >
              <Dialog.Panel className="relative mr-16 flex w-full max-w-xs flex-1">
                <div className="flex grow flex-col gap-y-5 overflow-y-auto bg-[#212427] px-0 pb-4">
                  <div className="flex h-16 shrink-0 items-center px-6">
                    <div className="flex items-center gap-2">
                      <div className="h-8 w-8 rounded-lg bg-primary-600 flex items-center justify-center">
                        <span className="text-white font-bold text-sm">P</span>
                      </div>
                      <span className="text-2xl font-bold text-white font-display">PSAP Control Center</span>
                    </div>
                  </div>
                  <nav className="flex flex-1 flex-col">
                    <ul className="flex flex-1 flex-col gap-y-0.5">
                      {filteredNav.map((item) => (
                        <Fragment key={item.name}>
                        <li>
                          <NavLink
                            to={item.href}
                            onClick={() => setSidebarOpen(false)}
                            className={({ isActive }) =>
                              clsx(
                                'group flex gap-x-3 px-6 py-3 text-lg font-medium transition-colors border-l-[3px]',
                                isActive
                                  ? 'bg-white/10 text-white border-l-[#73BCF7]'
                                  : 'text-gray-300 hover:bg-white/5 hover:text-white border-l-transparent'
                              )
                            }
                          >
                            <item.icon className="h-5 w-5 shrink-0" />
                            {item.name}
                            {item.comingSoon && (
                              <span className="ml-auto text-xs text-gray-500 bg-white/10 px-2 py-0.5 rounded-full">
                                Soon
                              </span>
                            )}
                            {item.badge && (
                              <span className="ml-auto rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-medium text-amber-300">
                                {item.badge}
                              </span>
                            )}
                          </NavLink>
                          {item.name === 'Fournos Dashboard' && (
                            <ul className="space-y-0.5 pb-2">
                              {fournDashboardSubnav
                                .filter((subItem) => subItem.id !== 'submit' || authed)
                                .map((subItem) => {
                                  const isActive = isFournosSubnavActive(subItem.id)
                                  return (
                                    <li key={subItem.id}>
                                      <NavLink
                                        to={subItem.href}
                                        onClick={() => setSidebarOpen(false)}
                                        aria-current={isActive ? 'page' : undefined}
                                        className={clsx(
                                          'ml-9 mr-3 flex rounded-md px-3 py-2 text-lg transition-colors',
                                          isActive
                                            ? 'bg-white/10 text-white'
                                            : 'text-gray-400 hover:bg-white/5 hover:text-white'
                                        )}
                                      >
                                        {subItem.name}
                                      </NavLink>
                                    </li>
                                  )
                                })}
                            </ul>
                          )}
                        </li>
                        {item.name === 'Fournos Dashboard' && (
                          <PerformanceDashboardSection onNavigate={() => setSidebarOpen(false)} />
                        )}
                        {item.name === 'Fournos Dashboard' && (
                          <PsapToolsSection onNavigate={() => setSidebarOpen(false)} />
                        )}
                        </Fragment>
                      ))}
                    </ul>
                  </nav>
                </div>
              </Dialog.Panel>
            </Transition.Child>
          </div>
        </Dialog>
      </Transition.Root>

      <div
        className={clsx('hidden lg:fixed lg:inset-y-0 lg:z-50 lg:flex lg:flex-col', envBanner && 'lg:top-7')}
        style={{ width: sidebarWidth }}
      >
        <div className="flex grow flex-col gap-y-5 overflow-y-auto bg-[#212427] px-0 pb-4">
          <div className="flex h-16 shrink-0 items-center px-6">
            <div className="flex items-center gap-3">
              <div className="h-10 w-10 rounded-xl bg-primary-600 flex items-center justify-center">
                <span className="text-white font-bold text-lg">P</span>
              </div>
              <div>
                <h1 className="text-xl font-bold text-white font-display">PSAP Control Center</h1>
              </div>
            </div>
          </div>
          <nav className="flex flex-1 flex-col">
            <ul className="flex flex-1 flex-col gap-y-0.5">
              {filteredNav.map((item) => (
                <Fragment key={item.name}>
                <li>
                  <NavLink
                    to={item.href}
                    className={({ isActive }) =>
                      clsx(
                        'group flex gap-x-3 px-6 py-3 text-lg font-medium transition-all duration-200 border-l-[3px]',
                        isActive
                          ? 'bg-white/10 text-white border-l-[#73BCF7]'
                          : 'text-gray-300 hover:bg-white/5 hover:text-white border-l-transparent'
                      )
                    }
                  >
                    <item.icon
                      className={clsx(
                        'h-5 w-5 shrink-0 transition-colors',
                        (location.pathname === item.href.split('?')[0] || location.pathname.startsWith(`${item.href.split('?')[0]}/`))
                          ? 'text-[#73BCF7]'
                          : 'text-gray-500 group-hover:text-gray-300'
                      )}
                    />
                    {item.name}
                    {item.comingSoon && (
                      <span className="ml-auto text-xs text-gray-500 bg-white/10 px-2 py-0.5 rounded-full">
                        Soon
                      </span>
                    )}
                    {item.badge && (
                      <span className="ml-auto rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-medium text-amber-300">
                        {item.badge}
                      </span>
                    )}
                  </NavLink>
                  {item.name === 'Fournos Dashboard' && (
                    <ul className="space-y-0.5 pb-2">
                      {fournDashboardSubnav
                        .filter((subItem) => subItem.id !== 'submit' || authed)
                        .map((subItem) => {
                          const isActive = isFournosSubnavActive(subItem.id)
                          return (
                            <li key={subItem.id}>
                              <NavLink
                                to={subItem.href}
                                aria-current={isActive ? 'page' : undefined}
                                className={clsx(
                                  'ml-9 mr-3 flex rounded-md px-3 py-2 text-lg transition-colors',
                                  isActive
                                    ? 'bg-white/10 text-white'
                                    : 'text-gray-400 hover:bg-white/5 hover:text-white'
                                )}
                              >
                                {subItem.name}
                              </NavLink>
                            </li>
                          )
                        })}
                    </ul>
                  )}
                </li>
                {item.name === 'Fournos Dashboard' && <PerformanceDashboardSection />}
                {item.name === 'Fournos Dashboard' && <PsapToolsSection />}
                </Fragment>
              ))}
            </ul>

            <div className="mt-auto pt-4 border-t border-white/10 space-y-3 px-6">
              <HearthIndicator onConnectClick={() => setHearthConnectOpen(true)} />
              <a
                href="https://github.com/openshift-psap"
                target="_blank"
                rel="noopener noreferrer"
                className="block rounded-lg bg-white/5 p-4 transition-colors hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#73BCF7]"
              >
                <p className="text-sm font-medium text-gray-500 uppercase tracking-wide">Organization</p>
                <p className="mt-1 text-lg font-semibold text-white">OpenShift PSAP</p>
                <p className="text-sm text-gray-400">Performance & Scale for AI Platforms</p>
              </a>
            </div>
          </nav>
        </div>
        <div
          role="separator"
          aria-label="Resize sidebar"
          aria-orientation="vertical"
          aria-valuemin={MIN_SIDEBAR_WIDTH}
          aria-valuemax={MAX_SIDEBAR_WIDTH}
          aria-valuenow={sidebarWidth}
          aria-valuetext={`${sidebarWidth} pixels`}
          tabIndex={0}
          onPointerDown={startSidebarResize}
          onKeyDown={resizeSidebarWithKeyboard}
          onDoubleClick={() => setSidebarWidth(DEFAULT_SIDEBAR_WIDTH)}
          title="Drag to resize sidebar; double-click to reset"
          className="group absolute right-0 top-0 z-[60] hidden h-full w-2 translate-x-1/2 cursor-col-resize touch-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#73BCF7] lg:block"
        >
          <span className="absolute inset-y-0 left-1/2 w-px bg-transparent transition-colors group-hover:bg-[#73BCF7] group-focus-visible:bg-[#73BCF7]" />
        </div>
      </div>

      <div className="lg:pl-[var(--sidebar-width)]">
        <div className={clsx('sticky z-40 flex h-16 shrink-0 items-center gap-x-4 border-b border-white/10 bg-[#151515] px-4 sm:gap-x-6 sm:px-6 lg:px-8', envBanner ? 'top-7' : 'top-0')}>
          <button
            type="button"
            className="-m-2.5 p-2.5 text-gray-300 lg:hidden"
            onClick={() => setSidebarOpen(true)}
          >
            <span className="sr-only">Open sidebar</span>
            <Bars3Icon className="h-6 w-6" />
          </button>

          <div className="flex flex-1 gap-x-4 self-stretch lg:gap-x-6">
            <div className="flex flex-1" />
            <div className="flex items-center gap-x-4 lg:gap-x-6">
              <button
                onClick={() => setHearthConnectOpen(true)}
                className={clsx(
                  'hidden sm:flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium transition-colors',
                  hearthStatus?.available
                    ? 'bg-green-500/10 text-green-400 border border-green-500/30 hover:border-green-400/60 hover:bg-green-500/20 cursor-pointer'
                    : hearthStatus?.configured
                    ? 'bg-orange-500/10 text-orange-400 border border-orange-500/30 hover:border-orange-400/60 hover:bg-orange-500/20 cursor-pointer'
                    : 'bg-white/5 text-gray-400 border border-white/10 hover:border-orange-500/30 hover:text-orange-400 cursor-pointer'
                )}
                title={
                  hearthStatus?.available
                    ? `Hearth: ${hearthStatus.cluster_count} clusters, ${hearthStatus.total_gpus} GPUs — click to change connection`
                    : hearthStatus?.configured
                    ? `Hearth: ${hearthStatus?.error || 'Connection error'} — click to change connection`
                    : 'Click to connect Hearth'
                }
              >
                <FireIcon className="h-3.5 w-3.5" />
                <span className="relative flex h-1.5 w-1.5">
                  <span className={clsx(
                    'relative inline-flex rounded-full h-1.5 w-1.5',
                    hearthStatus?.available ? 'bg-green-400' : hearthStatus?.configured ? 'bg-orange-400' : 'bg-gray-500'
                  )} />
                </span>
              </button>
              <div className="hidden lg:block lg:h-6 lg:w-px lg:bg-white/10" />
              {authed ? (
                <div className="flex items-center gap-3">
                  <span className="flex items-center gap-1.5 text-sm text-gray-300">
                    <LockClosedIcon className="h-4 w-4 text-green-400" />
                    <span className="hidden sm:inline font-medium">{getDisplayName()}</span>
                  </span>
                  <button
                    onClick={handleLogout}
                    className="rounded-lg border border-white/20 px-3 py-1.5 text-xs font-medium text-gray-300 hover:bg-white/10 transition-colors"
                  >
                    Sign Out
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => setLoginOpen(true)}
                  className="flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500 shadow-sm transition-colors"
                >
                  <LockOpenIcon className="h-4 w-4" />
                  Sign In
                </button>
              )}
            </div>
          </div>
        </div>

        <main className="py-6 px-4 sm:px-6 lg:px-8 bg-[#f0f0f0] min-h-[calc(100vh-4rem)]">
          <Outlet />
        </main>
      </div>

      <LoginModal
        open={loginOpen}
        onClose={() => setLoginOpen(false)}
        authConfig={authConfigQuery.data}
        configLoading={authConfigQuery.isPending || authConfigQuery.isFetching}
        configError={authConfigQuery.isError}
        onRetryConfig={() => { void authConfigQuery.refetch() }}
      />
      <HearthConnectModal
        open={hearthConnectOpen}
        configured={hearthStatus?.configured ?? false}
        onClose={() => setHearthConnectOpen(false)}
      />
    </div>
  )
}
