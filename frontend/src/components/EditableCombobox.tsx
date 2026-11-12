import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronUpDownIcon, XMarkIcon } from '@heroicons/react/20/solid'
import clsx from 'clsx'

/** Editable filter input with suggestions from durable History data.
 * Values outside the suggestion list remain valid, which preserves prefix
 * searches for SHAs/digests and allows newly observed values immediately.
 */
export default function EditableCombobox({
  value,
  onChange,
  options,
  placeholder,
  ariaLabel,
  className,
  maxLength,
  inputMode,
  multiple = false,
}: {
  value: string
  onChange: (value: string) => void
  options: string[]
  placeholder: string
  ariaLabel: string
  className?: string
  maxLength?: number
  inputMode?: 'text' | 'numeric' | 'search'
  multiple?: boolean
}) {
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const tokens = multiple ? value.split(',') : [value]
  const activeToken = (tokens[tokens.length - 1] || '').trim()
  const selected = useMemo(
    () => new Set(multiple ? value.split(',').map((item) => item.trim()).filter(Boolean) : []),
    [multiple, value]
  )
  const filtered = useMemo(() => {
    const query = activeToken.toLowerCase()
    return options
      .filter((option) => !selected.has(option))
      .filter((option) => !query || option.toLowerCase().includes(query))
      .slice(0, 50)
  }, [activeToken, options, selected])

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])

  const choose = (option: string) => {
    if (multiple) {
      const values = value.split(',').map((item) => item.trim()).filter(Boolean)
      if (activeToken && values[values.length - 1] === activeToken) values.pop()
      onChange([...values, option].join(', ') + ', ')
      inputRef.current?.focus()
    } else {
      onChange(option)
      setOpen(false)
      inputRef.current?.blur()
    }
  }

  return (
    <div ref={containerRef} className={clsx('relative', className)}>
      <div className="relative">
        <input
          ref={inputRef}
          type="text"
          value={value}
          maxLength={maxLength}
          inputMode={inputMode}
          onChange={(event) => { onChange(event.target.value); setOpen(true) }}
          onFocus={() => setOpen(true)}
          onKeyDown={(event) => {
            if (event.key === 'Escape') setOpen(false)
            if (event.key === 'Enter' && filtered.length === 1) {
              event.preventDefault()
              choose(filtered[0])
            }
          }}
          placeholder={placeholder}
          aria-label={ariaLabel}
          role="combobox"
          aria-expanded={open}
          aria-autocomplete="list"
          autoComplete="off"
          className="w-full rounded-md border-gray-300 pr-8 text-sm shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
        />
        {value ? (
          <button
            type="button"
            title={`Clear ${ariaLabel}`}
            aria-label={`Clear ${ariaLabel}`}
            onClick={() => { onChange(''); inputRef.current?.focus() }}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
          >
            <XMarkIcon className="h-4 w-4" />
          </button>
        ) : (
          <ChevronUpDownIcon className="pointer-events-none absolute right-2 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
        )}
      </div>
      {open && (
        <div role="listbox" className="absolute z-30 mt-1 max-h-60 w-full min-w-[14rem] overflow-y-auto rounded-md border border-gray-200 bg-white py-1 shadow-lg">
          {filtered.length ? filtered.map((option) => (
            <button
              key={option}
              type="button"
              role="option"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => choose(option)}
              className="block w-full truncate px-3 py-1.5 text-left text-sm text-gray-700 hover:bg-indigo-50"
              title={option}
            >
              {option}
            </button>
          )) : (
            <p className="px-3 py-2 text-sm text-gray-400">
              Type a value to filter
            </p>
          )}
        </div>
      )}
    </div>
  )
}
