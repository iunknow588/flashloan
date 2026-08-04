class RouteContext:
    def __init__(self) -> None:
        self.panel = None

    def bind(self, panel, module_globals: dict | None = None) -> None:
        self.panel = panel
        if module_globals is not None:
            module_globals.update(vars(panel))

    def call(self, name: str, *args, **kwargs):
        if self.panel is None:
            raise RuntimeError("route context is not bound")
        sync = getattr(self.panel, "sync_liquidation_module_context", None)
        if callable(sync):
            sync()
        return getattr(self.panel, name)(*args, **kwargs)

    def get(self, name: str):
        if self.panel is None:
            raise RuntimeError("route context is not bound")
        return getattr(self.panel, name)

