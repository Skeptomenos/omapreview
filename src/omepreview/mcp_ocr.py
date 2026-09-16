"""Request-owned OCR threads for the stdio adapter; no writer or job registry."""
from __future__ import annotations

import queue
import threading

import anyio

# Accessed only on the server event-loop thread. EOF cancels all active requests.
_active: set[threading.Event] = set()


async def run_owned(work, ctx=None):
    """Drain phase events and retain ownership until engine cleanup has finished.

    Engine deadlines bound cooperative work and subprocess teardown. Native PDF
    calls are not interruptible; shielding must not turn cancellation into an
    abandoned live thread during such a call.
    """
    cancel = threading.Event()
    done = threading.Event()
    events = queue.SimpleQueue()
    outcome = []

    def execute():
        try:
            outcome.append((True, work(cancel, events.put)))
        except BaseException as exc:
            outcome.append((False, exc))
        finally:
            done.set()

    async def drain():
        while not events.empty():
            event = events.get_nowait()
            if ctx is not None:
                await ctx.report_progress(event['completed'], event['total'], event['phase'])

    thread = threading.Thread(target=execute, name='omapreview-ocr', daemon=False)
    _active.add(cancel)
    try:
        thread.start()
        while not done.is_set():
            await drain()
            await anyio.sleep(.025)
        await drain()
    finally:
        cancel.set()
        # Cancellation and EOF must never abandon the engine or its process group.
        # No cancellable await may precede this shielded join.
        with anyio.CancelScope(shield=True):
            while thread.is_alive():
                await anyio.sleep(.025)
            thread.join()
        _active.discard(cancel)
    success, value = outcome[0]
    if success:
        return value
    raise value


class EOFStream:
    """Preserve SDK stream context while explicitly delivering transport EOF."""
    def __init__(self, stream):
        self.stream = stream

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def _cancel(self):
        for event in tuple(_active):
            event.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await self.stream.__anext__()
        except (StopAsyncIteration, anyio.EndOfStream, anyio.ClosedResourceError):
            self._cancel()
            raise

    async def __aenter__(self):
        await self.stream.__aenter__()
        return self

    async def __aexit__(self, *args):
        self._cancel()
        return await self.stream.__aexit__(*args)
