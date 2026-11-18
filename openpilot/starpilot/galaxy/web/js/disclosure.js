const motions = new WeakMap()
const interrupted = new WeakMap()

function reducedMotion() { return globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true }

function cancel(element) {
  const motion = motions.get(element)
  if (!motion) return
  interrupted.set(element, element.getBoundingClientRect().height)
  motion.animation.cancel()
  element.style.overflow = motion.overflow
  motions.delete(element)
}

// Animate the outer box, including bordered cards, using Galaxy's shared timing.
function resize(element, from, to, done, expanded) {
  cancel(element)
  interrupted.delete(element)
  if (reducedMotion() || !element.animate || from === to) { done(); return }
  const token = getComputedStyle(element).getPropertyValue('--motion-base').trim()
  const [time, ...curve] = token.split(/\s+/)
  const duration = Number.parseFloat(time) * (time?.endsWith('ms') ? 1 : 1000) || 240
  const overflow = element.style.overflow
  element.style.overflow = 'hidden'
  const animation = element.animate([{ height: `${from}px` }, { height: `${to}px` }], {
    duration, easing: curve.join(' ') || 'ease-out',
  })
  motions.set(element, { animation, overflow, expanded })
  animation.onfinish = () => {
    if (motions.get(element)?.animation !== animation) return
    motions.delete(element)
    element.style.overflow = overflow
    done()
  }
}

// One delegated handler covers every native disclosure, including dynamic/nested ones.
export function installDisclosureMotion(root = document) {
  const click = event => {
    if (event.defaultPrevented || event.button !== 0 || reducedMotion()) return
    const summary = event.target.closest?.('summary')
    const details = summary?.parentElement
    if (details?.tagName !== 'DETAILS' || details.querySelector('summary') !== summary || !details.animate) return
    if (event.target.closest('a, button, input, select, textarea')) return
    event.preventDefault()
    const expanded = !(motions.get(details)?.expanded ?? details.open)
    const from = details.getBoundingClientRect().height
    cancel(details)
    details.open = expanded
    const to = details.getBoundingClientRect().height
    // Keep content rendered during closing; update native state when it finishes.
    details.open = true
    resize(details, from, to, () => { details.open = expanded }, expanded)
  }
  root.addEventListener('click', click)
  return () => root.removeEventListener('click', click)
}

// The same resize implementation for Vue-controlled panels; no extra wrapper DOM.
export const GxCollapse = {
  name: 'GxCollapse',
  methods: {
    enter(element, done) {
      if (!element.animate || reducedMotion()) { cancel(element); interrupted.delete(element); done(); return }
      const from = interrupted.get(element) ?? 0
      cancel(element)
      const to = element.getBoundingClientRect().height
      resize(element, from, to, done, true)
    },
    leave(element, done) {
      if (!element.animate || reducedMotion()) { cancel(element); interrupted.delete(element); done(); return }
      const from = interrupted.get(element) ?? element.getBoundingClientRect().height
      resize(element, from, 0, done, false)
    },
    cancel,
  },
  template: `<Transition :css="false" @enter="enter" @leave="leave" @enter-cancelled="cancel" @leave-cancelled="cancel"><slot /></Transition>`,
}
