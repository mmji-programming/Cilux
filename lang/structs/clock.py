from core.plugin import Plugin, Value
from lang.types._str import (
    StringValue,
)
from lang.types._bool import (
    BoolValue,
)
from lang.types._dict import (
    DictValue,
)
from .scheduler import (
    get_scheduler,
)


import time

UNITS = {
    "hz": 1,
    "khz": 1_000,
    "mhz": 1_000_000,
    "ghz": 1_000_000_000,
    "s": 1,
    "ms": 0.001,
    "us": 0.000_001,
    "µs": 0.000_001,
    "ns": 0.000_000_001,
    "ps": 0.000_000_000_001,
}


class ClockValue(Value):
    def __init__(self, data):
        self.data = data
        self.vtype = "clock"

    def __repr__(self):
        return f"<clock {self.data.name}>"

    def __dot__(self, prop):
        if prop == "name":
            return StringValue(str(self.data.name))

        if prop == "freq":
            return StringValue(str(self.data.freq))

        if prop == "period":
            return StringValue(str(self.data.period))

        if prop == "duty":
            return StringValue(str(self.data.duty))

        if prop == "state":
            return StringValue(str(self.data.state))

        if prop == "sync":
            return BoolValue(self.data.sync)

        if prop == "async":
            return BoolValue((not self.data.sync))

        if prop == "edge_callbacks":
            return DictValue(self.data.edge_callbacks)


class Clock:
    def __init__(self, name, freq=None, period=None, duty=50, sync=True):

        self.name = name
        self.state = 0

        if freq:
            self.freq = freq
            self.period = 1 / freq
        elif period:
            self.period = period
            self.freq = 1 / period
        else:
            self.freq = 1e8
            self.period = 1e-8

        self.duty = duty
        self.high_time = self.period * (duty / 100)
        self.low_time = self.period - self.high_time

        self.sync = sync
        self.edge_callbacks = {
            "posedge": [],
            "negedge": [],
        }

        self.tick_count = 0

    def add_handler(self, handler, edge="posedge"):
        self.edge_callbacks[edge].append(handler)

    def tick(self):
        self.tick_count += 1

        if not self.sync:
            self._fire_async_reset()

        # Rising edge
        self.state = 1
        self._fire("posedge")
        self._sleep(self.high_time)

        # Falling edge
        self.state = 0
        self._fire("negedge")
        self._sleep(self.low_time)

    def run(self, cycles=1):
        for _ in range(cycles):
            self.tick()

    def _fire(self, edge):
        handlers = self.edge_callbacks.get(edge, [])
        for handler in handlers:
            handler.evaluate()
        for handler in handlers:
            handler.commit()

    def _fire_async_reset(self):
        for edge in ("posedge", "negedge"):
            for handler in self.edge_callbacks.get(edge, []):
                if hasattr(handler, "is_reset") and handler.is_reset:
                    handler.evaluate()
                    handler.commit()

    def _sleep(self, duration):
        if duration > 0:
            time.sleep(duration)


class ClockPlugin(Plugin):
    name = "clock"
    deps = ["expr"]
    grammar = r"""
        clock_stmt: ("[" clock_config "]")? "clock" NAME ";"
        clock_config: clock_param ("," clock_param)* ","?
        clock_param: FREQ "=" INT unit?
                   | PERIOD "=" INT unit?
                   | DUTY "=" INT
                   | SYNC
                   | ASYNC
        
        FREQ: "freq"
        PERIOD: "period"
        DUTY: "duty"
        
        unit: KHZ 
            | MHZ 
            | GHZ 
            | HZ
            | KS 
            | MS 
            | US 
            | NS 
            | PS
            | S
            | HZ
        
        
        KHZ: /[kK][hH][zZ]/
        MHZ: /[mM][hH][zZ]/
        GHZ: /[gG][hH][zZ]/
        HZ: /[hH][zZ]/
        
        S: /[sS]/
        KS: /[kK][sS]/
        MS: /[mM][sS]/
        US: /[uU][sS]|[μ][sS]/
        NS: /[nN][sS]/
        PS: /[pP][sS]/

        
        SYNC: "sync"
        ASYNC: "async"
    """
    contributes = {"statement": ["clock_stmt"]}

    def exec_clock_stmt(kernel, node):

        clock_config = {}
        clock_name = ""

        if hasattr(node, "data") and node.data == "clock_stmt":
            for child in node.children:
                if hasattr(child, "data") and child.data == "clock_config":
                    for config in child.children:
                        if hasattr(config, "data") and config.data == "clock_param":
                            name = None
                            value = None
                            unit = None

                            for param in config.children:
                                if hasattr(param, "data") and param.data == "unit":
                                    _unit = param.children[0].value.lower()
                                    unit = UNITS.get(_unit, 1)
                                elif hasattr(param, "type"):
                                    if param.type in ("PERIOD", "FREQ", "DUTY"):
                                        name = param.value
                                    elif param.type == "INT":
                                        value = int(param.value)
                                    elif param.type == "SYNC":
                                        value = "sync"
                                    elif param.type == "ASYNC":
                                        value = "async"

                            if unit is not None and value is not None:
                                value = value * unit

                            if name and value is not None:
                                clock_config[name] = value

                            elif value in ("sync", "async"):
                                if value == "sync" and "async" in clock_config:
                                    raise NameError("'sync' and 'async' cannot be used at the same time.")
                                if value == "async" and "sync" in clock_config:
                                    raise NameError("'async' and 'sync' cannot be used at the same time.")
                                clock_config[value] = True

                elif hasattr(child, "type") and child.type == "NAME":
                    clock_name = child.value

        if "freq" in clock_config and "period" not in clock_config:
            if clock_config["freq"] > 0:
                clock_config["period"] = 1 / clock_config["freq"]

        elif "period" in clock_config and "freq" not in clock_config:
            if clock_config["period"] > 0:
                clock_config["freq"] = 1 / clock_config["period"]

        elif "freq" in clock_config and "period" in clock_config:
            expected_period = 1 / clock_config["freq"]
            if abs(clock_config["period"] - expected_period) > 0.0001:
                raise NameError(
                    f"'freq' and 'period' conflict: freq={clock_config['freq']}Hz implies period={expected_period}s, but period={clock_config['period']}s was given."
                )

        if "duty" not in clock_config:
            clock_config["duty"] = 50

        if "sync" not in clock_config and "async" not in clock_config:
            clock_config["sync"] = True

        if "async" in clock_config and clock_config["async"]:
            del clock_config["async"]
            clock_config["sync"] = False

        clock = Clock(name=clock_name, **clock_config)
        kernel.context.declare(clock_name, ClockValue(clock))
        get_scheduler(kernel).register(clock)

    exec_handlers = {"clock_stmt": exec_clock_stmt}
