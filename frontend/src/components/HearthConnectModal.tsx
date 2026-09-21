import { Fragment, useState, useCallback } from 'react'
import { Dialog, Tab, Transition } from '@headlessui/react'
import {
  CloudArrowUpIcon,
  EyeIcon,
  EyeSlashIcon,
  FireIcon,
  KeyIcon,
} from '@heroicons/react/24/outline'
import { useDropzone } from 'react-dropzone'
import clsx from 'clsx'
import { useConnectHearth } from '../hooks/useHearth'

interface HearthConnectModalProps {
  open: boolean
  configured?: boolean
  onClose: () => void
}

export default function HearthConnectModal({
  open,
  configured = false,
  onClose,
}: HearthConnectModalProps) {
  const [method, setMethod] = useState<'kubeconfig' | 'credentials'>('kubeconfig')
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [credentials, setCredentials] = useState({
    apiServerUrl: '',
    username: '',
    password: '',
  })
  const [showPassword, setShowPassword] = useState(false)
  const connectHearth = useConnectHearth()

  const onDrop = useCallback((accepted: File[]) => {
    if (accepted.length > 0) {
      setSelectedFile(accepted[0])
    }
  }, [])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'application/x-yaml': ['.yaml', '.yml'],
      'text/plain': ['.kubeconfig', '.conf'],
      'application/octet-stream': ['.kubeconfig'],
    },
    multiple: false,
  })

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (method === 'kubeconfig' && !selectedFile) return
    if (
      method === 'credentials' &&
      (!credentials.apiServerUrl || !credentials.username || !credentials.password)
    ) return

    try {
      if (method === 'kubeconfig') {
        await connectHearth.mutateAsync({ method, file: selectedFile! })
      } else {
        await connectHearth.mutateAsync({
          method,
          credentials: {
            api_server_url: credentials.apiServerUrl,
            username: credentials.username,
            password: credentials.password,
          },
        })
      }
      resetForm()
      onClose()
    } catch {
      // error toast handled by the mutation hook
    }
  }

  const resetForm = () => {
    setSelectedFile(null)
    setCredentials({ apiServerUrl: '', username: '', password: '' })
    setMethod('kubeconfig')
    setShowPassword(false)
  }

  const handleClose = () => {
    resetForm()
    onClose()
  }

  const canSubmit = method === 'kubeconfig'
    ? !!selectedFile
    : !!credentials.apiServerUrl && !!credentials.username && !!credentials.password

  return (
    <Transition.Root show={open} as={Fragment}>
      <Dialog as="div" className="relative z-50" onClose={handleClose}>
        <Transition.Child
          as={Fragment}
          enter="ease-out duration-300"
          enterFrom="opacity-0"
          enterTo="opacity-100"
          leave="ease-in duration-200"
          leaveFrom="opacity-100"
          leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-gray-500/75 transition-opacity" />
        </Transition.Child>

        <div className="fixed inset-0 z-10 overflow-y-auto">
          <div className="flex min-h-full items-center justify-center p-4">
            <Transition.Child
              as={Fragment}
              enter="ease-out duration-300"
              enterFrom="opacity-0 translate-y-4 sm:scale-95"
              enterTo="opacity-100 translate-y-0 sm:scale-100"
              leave="ease-in duration-200"
              leaveFrom="opacity-100 translate-y-0 sm:scale-100"
              leaveTo="opacity-0 translate-y-4 sm:scale-95"
            >
              <Dialog.Panel className="relative w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
                <div className="flex flex-col items-center mb-6">
                  <div className="h-12 w-12 rounded-full bg-orange-100 flex items-center justify-center mb-3">
                    <FireIcon className="h-6 w-6 text-orange-600" />
                  </div>
                  <Dialog.Title className="text-lg font-semibold text-gray-900">
                    {configured ? 'Change' : 'Connect'} Hearth / Admin Cluster
                  </Dialog.Title>
                  <p className="mt-1 text-sm text-gray-500 text-center">
                    {configured
                      ? 'Replace the connection to the OpenShift management cluster'
                      : 'Connect to the OpenShift management cluster where Hearth and Fournos run'}
                  </p>
                </div>

                <form onSubmit={handleSubmit} className="space-y-4">
                  <Tab.Group
                    selectedIndex={method === 'kubeconfig' ? 0 : 1}
                    onChange={(index) => setMethod(index === 0 ? 'kubeconfig' : 'credentials')}
                  >
                    <Tab.List className="flex space-x-1 rounded-xl bg-gray-100 p-1">
                      <Tab className={({ selected }) => clsx(
                        'w-full rounded-lg py-2.5 text-sm font-medium focus:outline-none',
                        selected ? 'bg-white text-orange-700 shadow' : 'text-gray-600 hover:bg-white/50'
                      )}>
                        <span className="flex items-center justify-center gap-2">
                          <CloudArrowUpIcon className="h-4 w-4" /> Kubeconfig
                        </span>
                      </Tab>
                      <Tab className={({ selected }) => clsx(
                        'w-full rounded-lg py-2.5 text-sm font-medium focus:outline-none',
                        selected ? 'bg-white text-orange-700 shadow' : 'text-gray-600 hover:bg-white/50'
                      )}>
                        <span className="flex items-center justify-center gap-2">
                          <KeyIcon className="h-4 w-4" /> OpenShift Login
                        </span>
                      </Tab>
                    </Tab.List>

                    <Tab.Panels className="mt-4">
                      <Tab.Panel>
                        <div
                          {...getRootProps()}
                          className={`border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-colors ${
                            isDragActive
                              ? 'border-orange-500 bg-orange-50'
                              : selectedFile
                              ? 'border-green-400 bg-green-50'
                              : 'border-gray-300 hover:border-gray-400'
                          }`}
                        >
                          <input {...getInputProps()} />
                          <CloudArrowUpIcon className={`h-10 w-10 mx-auto ${
                            selectedFile ? 'text-green-500' : 'text-gray-400'
                          }`} />
                          {selectedFile ? (
                            <div className="mt-2">
                              <p className="text-sm font-medium text-green-700">{selectedFile.name}</p>
                              <p className="text-xs text-gray-500 mt-1">Click or drop to replace</p>
                            </div>
                          ) : (
                            <div className="mt-2">
                              <p className="text-sm text-gray-600">Drop your kubeconfig here, or click to browse</p>
                              <p className="text-xs text-gray-400 mt-1">
                                Requires access to Hearth and Fournos resources
                              </p>
                            </div>
                          )}
                        </div>
                      </Tab.Panel>

                      <Tab.Panel className="space-y-4">
                        <div>
                          <label
                            htmlFor="hearth-api-server-url"
                            className="block text-sm font-medium text-gray-700"
                          >
                            OpenShift API Server URL
                          </label>
                          <input
                            id="hearth-api-server-url"
                            type="url"
                            value={credentials.apiServerUrl}
                            onChange={(e) => setCredentials((current) => ({
                              ...current,
                              apiServerUrl: e.target.value,
                            }))}
                            placeholder="https://api.cluster.example.com:6443"
                            autoComplete="url"
                            className="mt-1 block w-full rounded-lg border-gray-300 shadow-sm focus:border-orange-500 focus:ring-orange-500"
                          />
                        </div>
                        <div>
                          <label
                            htmlFor="hearth-username"
                            className="block text-sm font-medium text-gray-700"
                          >
                            OpenShift Username
                          </label>
                          <input
                            id="hearth-username"
                            type="text"
                            value={credentials.username}
                            onChange={(e) => setCredentials((current) => ({
                              ...current,
                              username: e.target.value,
                            }))}
                            autoComplete="username"
                            className="mt-1 block w-full rounded-lg border-gray-300 shadow-sm focus:border-orange-500 focus:ring-orange-500"
                          />
                        </div>
                        <div>
                          <label
                            htmlFor="hearth-password"
                            className="block text-sm font-medium text-gray-700"
                          >
                            Password
                          </label>
                          <div className="relative mt-1">
                            <input
                              id="hearth-password"
                              type={showPassword ? 'text' : 'password'}
                              value={credentials.password}
                              onChange={(e) => setCredentials((current) => ({
                                ...current,
                                password: e.target.value,
                              }))}
                              autoComplete="current-password"
                              className="block w-full rounded-lg border-gray-300 pr-10 shadow-sm focus:border-orange-500 focus:ring-orange-500"
                            />
                            <button
                              type="button"
                              onClick={() => setShowPassword((visible) => !visible)}
                              className="absolute inset-y-0 right-0 flex items-center pr-3 text-gray-400 hover:text-gray-600"
                              aria-label={showPassword ? 'Hide password' : 'Show password'}
                            >
                              {showPassword
                                ? <EyeSlashIcon className="h-5 w-5" />
                                : <EyeIcon className="h-5 w-5" />}
                            </button>
                          </div>
                        </div>
                        <p className="text-xs text-gray-500">
                          The password is exchanged for this user's OpenShift OAuth token and is
                          never saved. Control Center will use that user's existing permissions;
                          it will not create or switch to a service account.
                        </p>
                      </Tab.Panel>
                    </Tab.Panels>
                  </Tab.Group>

                  <div className="rounded-lg bg-gray-50 p-3 text-xs text-gray-500 space-y-1">
                    <p className="font-medium text-gray-700">What this does:</p>
                    <ul className="list-disc list-inside space-y-0.5">
                      <li>Connects to the OpenShift management/admin cluster</li>
                      <li>Reads FournosCluster CRDs managed by the Hearth operator</li>
                      <li>Surfaces GPU hardware discovery, lock status, and Kueue quotas</li>
                    </ul>
                  </div>

                  <div className="flex gap-3">
                    <button
                      type="button"
                      onClick={handleClose}
                      className="flex-1 rounded-lg border border-gray-300 px-4 py-2.5 text-sm font-semibold text-gray-700 hover:bg-gray-50 transition-colors"
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      disabled={!canSubmit || connectHearth.isPending}
                      className="flex-1 rounded-lg bg-orange-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-orange-500 focus:outline-none focus:ring-2 focus:ring-orange-500 focus:ring-offset-2 disabled:opacity-50 transition-colors"
                    >
                      {connectHearth.isPending
                        ? (configured ? 'Updating...' : 'Connecting...')
                        : (configured ? 'Update Connection' : 'Connect')}
                    </button>
                  </div>
                </form>
              </Dialog.Panel>
            </Transition.Child>
          </div>
        </div>
      </Dialog>
    </Transition.Root>
  )
}
