import { Loader2, LocateFixed, MapPin, Search, X } from 'lucide-react'
import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { errorText, isAbort, userApi } from '../../services/userApi'
import type { GeoResult, Pt } from '../../types/user'

export interface QuickPick { key: string; label: string; hint?: string; icon?: ReactNode; onPick: () => void }

interface Props {
  label: string
  placeholder: string
  value: string                              // text of the currently chosen place ('' when none)
  onSelect: (p: Pt) => void
  onClear?: () => void
  quick?: QuickPick[]                        // shown first when the field is focused (e.g. "Your location")
  leading?: ReactNode
  trailing?: ReactNode
  autoFocus?: boolean
  large?: boolean
}

type State = { status: 'idle' | 'loading' | 'ok' | 'error'; results: GeoResult[]; error?: string }
const DEBOUNCE_MS = 350

/**
 * Place search with suggestions. Debounced (no request per keystroke), cancels superseded requests, and always says what
 * it is doing: Searching… / No places found / an error message. Keyboard: ↑ ↓ Enter Esc.
 */
export function PlaceSearch({ label, placeholder, value, onSelect, onClear, quick = [], leading, trailing, autoFocus, large }: Props) {
  const [text, setText] = useState(value)
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const [state, setState] = useState<State>({ status: 'idle', results: [] })
  const box = useRef<HTMLDivElement>(null)
  const uid = useId()

  useEffect(() => { setText(value) }, [value])

  // debounce the query, then search; a newer query aborts the older request
  useEffect(() => {
    const q = text.trim()
    if (!open || q.length < 2 || q === value) { setState({ status: 'idle', results: [] }); return }
    const ac = new AbortController()
    const timer = setTimeout(() => {
      setState(s => ({ ...s, status: 'loading', error: undefined }))
      userApi.geocode(q, ac.signal)
        .then(r => setState({ status: 'ok', results: r.results }))
        .catch(e => { if (!isAbort(e)) setState({ status: 'error', results: [], error: errorText(e) }) })
    }, DEBOUNCE_MS)
    return () => { clearTimeout(timer); ac.abort() }
  }, [text, open, value])

  useEffect(() => {
    const away = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', away)
    return () => document.removeEventListener('mousedown', away)
  }, [])

  const showQuick = quick.length > 0 && text.trim().length < 2
  const items: { key: string; run: () => void }[] = [
    ...(showQuick ? quick.map(q => ({ key: q.key, run: q.onPick })) : []),
    ...state.results.map(r => ({ key: r.id, run: () => choose(r) })),
  ]
  function choose(r: GeoResult) {
    setOpen(false); setText(r.label); setActive(-1)
    onSelect({ label: r.label, lat: r.lat, lon: r.lon, source: 'search' })
  }
  function onKey(e: React.KeyboardEvent) {
    if (e.key === 'ArrowDown') { e.preventDefault(); setOpen(true); setActive(i => Math.min(i + 1, items.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(i => Math.max(i - 1, 0)) }
    else if (e.key === 'Enter' && active >= 0 && items[active]) { e.preventDefault(); items[active].run(); setOpen(false) }
    else if (e.key === 'Escape') { setOpen(false); setActive(-1) }
  }

  const listId = `${uid}-list`
  const dropdown = open && (showQuick || state.status !== 'idle')
  return (
    <div ref={box} className="relative">
      <label className="sr-only" htmlFor={`${uid}-in`}>{label}</label>
      <div className={`flex items-center gap-2 bg-white border border-line-strong rounded-xl px-3 focus-within:border-ink focus-within:ring-2 focus-within:ring-ink/10 ${large ? 'h-12' : 'h-10'}`}>
        {leading ?? <Search className="size-4 text-ink-3 shrink-0" aria-hidden />}
        <input id={`${uid}-in`} role="combobox" aria-expanded={dropdown} aria-controls={listId} aria-autocomplete="list"
          aria-activedescendant={active >= 0 ? `${uid}-o${active}` : undefined} autoComplete="off" spellCheck={false} autoFocus={autoFocus}
          value={text} placeholder={placeholder} onChange={e => { setText(e.target.value); setOpen(true); setActive(-1) }}
          onFocus={() => setOpen(true)} onKeyDown={onKey}
          className={`flex-1 min-w-0 bg-transparent outline-none placeholder:text-ink-3 ${large ? 'text-[15px]' : 'text-[14px]'}`} />
        {state.status === 'loading' && <Loader2 className="size-4 animate-spin text-ink-3 shrink-0" aria-label="Searching" />}
        {text && onClear && (
          <button type="button" onClick={() => { setText(''); setOpen(false); onClear() }} aria-label={`Clear ${label}`} className="text-ink-3 hover:text-ink shrink-0">
            <X className="size-4" aria-hidden /></button>
        )}
        {trailing}
      </div>

      {dropdown && (
        <ul id={listId} role="listbox" aria-label={`${label} suggestions`}
          className="absolute z-20 left-0 right-0 top-full mt-1.5 bg-white border border-line rounded-xl shadow-[0_10px_30px_-12px_rgba(22,24,29,.25)] overflow-hidden max-h-72 overflow-y-auto">
          {showQuick && quick.map((q, i) => (
            <li key={q.key} role="presentation">
              <button type="button" role="option" id={`${uid}-o${i}`} aria-selected={active === i} onMouseDown={e => e.preventDefault()} onClick={() => { q.onPick(); setOpen(false) }}
                className={`w-full text-left px-3 py-2.5 flex items-center gap-3 hover:bg-paper ${active === i ? 'bg-paper' : ''}`}>
                <span className="size-8 rounded-full bg-[#e8effc] text-accent grid place-items-center shrink-0">{q.icon ?? <LocateFixed className="size-4" aria-hidden />}</span>
                <span><span className="block text-[13.5px] font-medium">{q.label}</span>{q.hint && <span className="block text-[12px] text-ink-3">{q.hint}</span>}</span>
              </button>
            </li>
          ))}
          {state.status === 'loading' && state.results.length === 0 && <li className="px-3 py-3 text-[13px] text-ink-3" role="status">Searching…</li>}
          {state.status === 'error' && <li className="px-3 py-3 text-[13px] text-[#9f2a1c]" role="alert">{state.error}</li>}
          {state.status === 'ok' && state.results.length === 0 && (
            <li className="px-3 py-3 text-[13px] text-ink-2" role="status">No places found for “{text.trim()}”. Try a nearby landmark or a different spelling.</li>
          )}
          {state.results.map((r, i) => {
            const idx = i + (showQuick ? quick.length : 0)
            return (
              <li key={r.id} role="presentation">
                <button type="button" role="option" id={`${uid}-o${idx}`} aria-selected={active === idx} onMouseDown={e => e.preventDefault()} onClick={() => choose(r)}
                  className={`w-full text-left px-3 py-2.5 flex items-center gap-3 hover:bg-paper ${active === idx ? 'bg-paper' : ''}`}>
                  <span className="size-8 rounded-full bg-paper text-ink-2 grid place-items-center shrink-0"><MapPin className="size-4" aria-hidden /></span>
                  <span className="min-w-0"><span className="block text-[13.5px] font-medium truncate">{r.label}</span>
                    {r.sublabel && <span className="block text-[12px] text-ink-3 truncate">{r.sublabel}</span>}</span>
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
