"""Background jobs with structured events.

Workers never touch Tkinter.  They post `JobEvent`s to a queue; the GUI
drains it on the main thread with ``after()``.
"""
from __future__ import annotations

import queue
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .audio import Cancelled


@dataclass
class JobEvent:
    job: str
    kind: str  # started | progress | done | failed | cancelled
    message: str = ""
    fraction: Optional[float] = None  # None = indeterminate
    result: Any = None
    elapsed: float = 0.0


@dataclass
class Job:
    name: str
    label: str
    cancel_event: threading.Event = field(default_factory=threading.Event)
    thread: Optional[threading.Thread] = None
    started: float = 0.0

    def cancelled(self) -> bool:
        return self.cancel_event.is_set()


class JobRunner:
    def __init__(self) -> None:
        self.events: "queue.Queue[JobEvent]" = queue.Queue()
        self.jobs: dict[str, Job] = {}

    def busy(self, name: Optional[str] = None) -> bool:
        if name is not None:
            j = self.jobs.get(name)
            return j is not None and j.thread is not None and j.thread.is_alive()
        return any(j.thread is not None and j.thread.is_alive() for j in self.jobs.values())

    def running(self) -> list[Job]:
        return [j for j in self.jobs.values() if j.thread is not None and j.thread.is_alive()]

    def start(self, name: str, label: str, fn: Callable[[Callable[[], bool], Callable[[str, Optional[float]], None]], Any]
              ) -> Job:
        """Run fn(cancelled, progress) in a daemon thread. Refuses duplicates."""
        if self.busy(name):
            raise RuntimeError(f"{label} is already running")
        job = Job(name, label)
        job.started = time.monotonic()

        def progress(msg: str, frac: Optional[float] = None) -> None:
            self.events.put(JobEvent(name, "progress", msg, frac, elapsed=time.monotonic() - job.started))

        def target() -> None:
            self.events.put(JobEvent(name, "started", label, None))
            try:
                result = fn(job.cancelled, progress)
                if job.cancelled():
                    self.events.put(JobEvent(name, "cancelled", f"{label} cancelled",
                                             elapsed=time.monotonic() - job.started))
                else:
                    self.events.put(JobEvent(name, "done", f"{label} finished", 1.0, result,
                                             elapsed=time.monotonic() - job.started))
            except Cancelled:
                self.events.put(JobEvent(name, "cancelled", f"{label} cancelled",
                                         elapsed=time.monotonic() - job.started))
            except Exception as exc:  # reported to the UI, never swallowed
                tb = traceback.format_exc()
                self.events.put(JobEvent(name, "failed", f"{exc}", result=tb,
                                         elapsed=time.monotonic() - job.started))

        job.thread = threading.Thread(target=target, name=f"job-{name}", daemon=True)
        self.jobs[name] = job
        job.thread.start()
        return job

    def cancel(self, name: Optional[str] = None) -> None:
        for j in self.jobs.values():
            if name is None or j.name == name:
                j.cancel_event.set()

    def wait_all(self, timeout: float = 15.0) -> bool:
        end = time.monotonic() + timeout
        for j in list(self.jobs.values()):
            if j.thread is not None:
                j.thread.join(max(0.0, end - time.monotonic()))
        return not self.busy()
