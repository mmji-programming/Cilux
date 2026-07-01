import itertools


class ClockScheduler:
    def __init__(self):
        self.sync_clocks = []
        self.async_clocks = []
        self.sim_time = 0.0
        self._seq = itertools.count()

    def register(self, clock):
        if getattr(clock, "_scheduler_registered", False):
            return

        existing = next(
            (c for c in self.sync_clocks + self.async_clocks if c.name == clock.name),
            None,
        )
        if existing is not None:
            if existing in self.sync_clocks:
                self.sync_clocks.remove(existing)
            if existing in self.async_clocks:
                self.async_clocks.remove(existing)

        clock._scheduler_registered = True
        clock._next_tick_time = 0.0
        if clock.sync:
            self.sync_clocks.append(clock)
        else:
            self.async_clocks.append(clock)

    def _drain_async(self):
        progressed = True
        while progressed:
            progressed = False
            for clock in self.async_clocks:
                while clock._next_tick_time <= self.sim_time:
                    clock.tick()  # existing, untouched Clock.tick()
                    clock._next_tick_time += clock.period
                    progressed = True

    def _next_sync_time(self):
        if not self.sync_clocks:
            return None
        return min(c._next_tick_time for c in self.sync_clocks)

    def _next_async_time(self):
        if not self.async_clocks:
            return None
        return min(c._next_tick_time for c in self.async_clocks)

    def run_step(self):
        next_async_t = self._next_async_time()
        next_sync_t = self._next_sync_time()

        if next_async_t is None and next_sync_t is None:
            return False

        if next_sync_t is None or (next_async_t is not None and next_async_t <= next_sync_t):
            self.sim_time = next_async_t
            self._drain_async()
            if next_sync_t is not None and next_sync_t == self.sim_time:
                for clock in self.sync_clocks:
                    if clock._next_tick_time <= self.sim_time:
                        clock.tick()
                        clock._next_tick_time += clock.period
                self._drain_async()
            return True

        self.sim_time = next_sync_t
        for clock in self.sync_clocks:
            if clock._next_tick_time <= self.sim_time:
                clock.tick()  # existing, untouched Clock.tick()
                clock._next_tick_time += clock.period

        self._drain_async()
        return True

    def run(self, cycles=None, time=None):
        if cycles is not None:
            if not self.sync_clocks:
                return  # nothing to count cycles against

            reference = self.sync_clocks[0]
            target_ticks = reference.tick_count + cycles
            while reference.tick_count < target_ticks:
                if not self.run_step():
                    break
            return

        if time is not None:
            target = self.sim_time + time
            while self.sim_time < target:
                if not self.run_step():
                    while self.sim_time < target:
                        if not self.async_clocks:
                            self.sim_time = target
                            break
                        nxt = min(c._next_tick_time for c in self.async_clocks)
                        self.sim_time = min(nxt, target)
                        self._drain_async()
                        if nxt > target:
                            self.sim_time = target
                            break
                    break
            return

        self.run_step()


def get_scheduler(kernel):
    sched = getattr(kernel, "_clock_scheduler", None)
    if sched is None:
        sched = ClockScheduler()
        kernel._clock_scheduler = sched
    return sched
