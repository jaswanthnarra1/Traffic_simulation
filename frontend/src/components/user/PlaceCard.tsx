import { Clock, ExternalLink, Navigation, Phone, X } from 'lucide-react'
import type { Place } from '../../types/user'
import { meters, safeUrl } from '../../utils/userTraffic'

/** Compact detail card for a nearby place. Missing fields are omitted, never invented. */
export function PlaceCard({ place, onClose, onDirections }: { place: Place; onClose: () => void; onDirections: () => void }) {
  const web = safeUrl(place.website)
  return (
    <section aria-label="Place details" className="rounded-2xl border border-line bg-white px-4 py-3.5 shadow-[0_10px_30px_-14px_rgba(22,24,29,.35)]">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[11px] font-semibold tracking-wider uppercase text-ink-3">{place.category_label}</div>
          <h2 className="text-[16px] font-semibold leading-snug break-words">{place.name ?? place.category_label}</h2>
        </div>
        <button type="button" onClick={onClose} aria-label="Close place details" className="text-ink-3 hover:text-ink shrink-0"><X className="size-4" aria-hidden /></button>
      </div>
      <dl className="mt-2 space-y-1 text-[13px] text-ink-2">
        <div className="flex gap-2"><dt className="sr-only">Distance</dt><dd className="num">{meters(place.distance_m)} away</dd></div>
        {place.address && <div><dt className="sr-only">Address</dt><dd>{place.address}</dd></div>}
        {place.opening_hours && <div className="flex gap-2 items-start"><Clock className="size-3.5 mt-1 shrink-0 text-ink-3" aria-hidden /><dt className="sr-only">Opening hours</dt><dd>{place.opening_hours}</dd></div>}
        {place.phone && <div className="flex gap-2 items-center"><Phone className="size-3.5 shrink-0 text-ink-3" aria-hidden /><dt className="sr-only">Phone</dt>
          <dd><a href={`tel:${place.phone.replace(/[^+\d]/g, '')}`} className="text-accent">{place.phone}</a></dd></div>}
        {web && <div className="flex gap-2 items-center"><ExternalLink className="size-3.5 shrink-0 text-ink-3" aria-hidden /><dt className="sr-only">Website</dt>
          <dd className="truncate"><a href={web} target="_blank" rel="noopener noreferrer" className="text-accent">Website</a></dd></div>}
      </dl>
      <button type="button" onClick={onDirections}
        className="mt-3 w-full h-10 rounded-xl bg-ink text-paper text-[13.5px] font-semibold inline-flex items-center justify-center gap-2 hover:bg-black">
        <Navigation className="size-4" aria-hidden /> Get directions</button>
      <p className="mt-2 text-[10.5px] text-ink-3">Place data from OpenStreetMap contributors.</p>
    </section>
  )
}
