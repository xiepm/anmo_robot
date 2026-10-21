/** Also bound operations such as JPEG decoding that do not accept a signal. */
export function abortable<T>(operation: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const aborted = () => {
      signal.removeEventListener('abort', aborted)
      reject(new DOMException('Operation aborted', 'AbortError'))
    }
    signal.addEventListener('abort', aborted, { once: true })
    // Keep rejection handlers attached even after cancellation: a late fetch or
    // decode failure must not become an unhandled rejection.
    operation.then(value => {
      signal.removeEventListener('abort', aborted)
      resolve(value)
    }, error => {
      signal.removeEventListener('abort', aborted)
      reject(error)
    })
    if (signal.aborted) aborted()
  })
}
