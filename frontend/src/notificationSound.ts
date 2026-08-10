let audioContext: AudioContext | null = null

function getAudioContext(): AudioContext | null {
  if (typeof window === 'undefined' || typeof window.AudioContext === 'undefined') {
    return null
  }
  if (audioContext === null) {
    try {
      audioContext = new window.AudioContext()
    } catch {
      return null
    }
  }
  return audioContext
}

function playTone(context: AudioContext) {
  const oscillator = context.createOscillator()
  const gain = context.createGain()
  const start = context.currentTime
  oscillator.type = 'sine'
  oscillator.frequency.setValueAtTime(880, start)
  oscillator.frequency.exponentialRampToValueAtTime(660, start + 0.16)
  gain.gain.setValueAtTime(0.0001, start)
  gain.gain.exponentialRampToValueAtTime(0.12, start + 0.015)
  gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.18)
  oscillator.connect(gain)
  gain.connect(context.destination)
  oscillator.start(start)
  oscillator.stop(start + 0.19)
}

export function unlockNotificationSound() {
  const context = getAudioContext()
  if (context?.state === 'suspended') void context.resume().catch(() => undefined)
}

export function playTaskCompletionSound() {
  const context = getAudioContext()
  if (context === null) return
  if (context.state === 'suspended') {
    void context.resume().then(() => playTone(context)).catch(() => undefined)
    return
  }
  try {
    playTone(context)
  } catch {
    // A blocked or unavailable audio device must not prevent the visual prompt.
  }
}
