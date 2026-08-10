import { Info } from 'lucide-react'
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'

export function FieldHelp({ label, text }: { label: string; text: string }) {
  const [open, setOpen] = useState(false)
  const [offset, setOffset] = useState({ x: 0, y: 0 })
  const root = useRef<HTMLSpanElement>(null)
  const tooltip = useRef<HTMLSpanElement>(null)
  const tooltipId = useId()

  useEffect(() => {
    if (!open) return
    const pointerdown = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false)
    }
    const keydown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.stopPropagation()
      setOpen(false)
    }
    document.addEventListener('pointerdown', pointerdown)
    document.addEventListener('keydown', keydown)
    return () => {
      document.removeEventListener('pointerdown', pointerdown)
      document.removeEventListener('keydown', keydown)
    }
  }, [open])

  useLayoutEffect(() => {
    if (!open) {
      setOffset({ x: 0, y: 0 })
      return
    }
    const updatePosition = () => {
      const bounds = tooltip.current?.getBoundingClientRect()
      if (!bounds) return
      const padding = 16
      const x = bounds.right > window.innerWidth - padding
        ? window.innerWidth - padding - bounds.right
        : bounds.left < padding
          ? padding - bounds.left
          : 0
      const y = bounds.bottom > window.innerHeight - padding
        ? window.innerHeight - padding - bounds.bottom
        : bounds.top < padding
          ? padding - bounds.top
          : 0
      setOffset({ x, y })
    }
    updatePosition()
    window.addEventListener('resize', updatePosition)
    return () => window.removeEventListener('resize', updatePosition)
  }, [open])

  return (
    <span className="field-help" ref={root}>
      <button
        aria-controls={tooltipId}
        aria-expanded={open}
        aria-label={`查看${label}说明`}
        className="field-help-button"
        onClick={() => setOpen((value) => !value)}
        type="button"
      >
        <Info aria-hidden="true" size={14} />
      </button>
      {open ? (
        <span
          className="field-help-tooltip"
          id={tooltipId}
          ref={tooltip}
          role="tooltip"
          style={{ transform: `translate(${offset.x}px, ${offset.y}px)` }}
        >
          {text}
        </span>
      ) : null}
    </span>
  )
}
