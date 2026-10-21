"""Console shutdown/input adapters; no SDK or device operations."""
import signal
import sys


class StopSignals:
    """Restore signal handlers only after the caller has finished shutdown."""
    def __init__(self, loop, callback):
        self.loop, self.entries = loop, []
        signals = [signal.SIGINT, signal.SIGTERM]
        if sys.platform == 'win32' and hasattr(signal, 'SIGBREAK'):
            signals.append(signal.SIGBREAK)
        try:
            for sig in signals:
                previous = signal.getsignal(sig)
                if sys.platform == 'win32':
                    def handler(_sig, _frame):
                        loop.call_soon_threadsafe(callback)
                    signal.signal(sig, handler)
                    self.entries.append((sig, previous, False))
                else:
                    loop.add_signal_handler(sig, callback)
                    self.entries.append((sig, previous, True))
        except BaseException:
            self.close()
            raise

    def close(self):
        for sig, previous, asynchronous in reversed(self.entries):
            if asynchronous:
                self.loop.remove_signal_handler(sig)
            signal.signal(sig, previous)
        self.entries.clear()


class ConsoleLines:
    """Poll complete console commands without blocking the STOP/event loop."""
    def __init__(self, stream=None):
        self.stream = stream if stream is not None else sys.stdin
        self.buffer = ''
        self.special = False

    def poll(self):
        if sys.platform != 'win32':
            import select
            return [self.stream.readline()] if select.select([self.stream], [], [], 0)[0] else []
        import msvcrt
        result = []
        # Bound work per event-loop tick, including unusually large pastes.
        for _ in range(256):
            if not msvcrt.kbhit():
                break
            char = msvcrt.getwch()
            if self.special:
                self.special = False
            elif char in ('\x00', '\xe0'):
                self.special = True
            elif char in ('\x03', '\x1a'):
                self.buffer = ''
                result.append('STOP\n')
            elif char in ('\r', '\n'):
                result.append(self.buffer+'\n')
                self.buffer = ''
                msvcrt.putwch('\r'); msvcrt.putwch('\n')
            elif char == '\b':
                if self.buffer:
                    self.buffer = self.buffer[:-1]
                    for item in '\b \b': msvcrt.putwch(item)
            elif char.isprintable():
                if len(self.buffer) < 128:
                    self.buffer += char
                    msvcrt.putwch(char)
        return result
