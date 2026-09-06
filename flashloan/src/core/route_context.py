class RouteContext:
    def __init__(self) -> None:
        self.panel = None
        self._providers: dict[str, object] = {}

    def bind(self, panel, module_globals: dict | None = None) -> None:
        self.panel = panel
        if module_globals is not None:
            module_globals.update(vars(panel))

    def bind_panel_provider(self, panel=None) -> None:
        """Bind panel method dispatch explicitly for route consumers.

        The panel object remains the compatibility boundary, while routes can
        replace this provider in tests or alternate app assembly.
        """
        target = panel if panel is not None else self.panel
        if target is None:
            raise RuntimeError("route context panel is not bound")

        def call(name: str, *args, **kwargs):
            return getattr(target, name)(*args, **kwargs)

        self.bind_provider("panel", call)

    def panel_call(self, name: str, *args, **kwargs):
        """Dispatch through an injected panel provider when available."""
        provider = self._providers.get("panel")
        if callable(provider):
            return provider(name, *args, **kwargs)
        return self.call(name, *args, **kwargs)

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

    def bind_provider(self, name: str, provider: object) -> None:
        """Bind an injectable route service without changing panel globals."""
        key = str(name or "").strip()
        if not key:
            raise ValueError("provider name is required")
        if provider is None:
            raise ValueError(f"provider {key} cannot be None")
        self._providers[key] = provider

    def provider(self, name: str) -> object:
        """Return a bound provider or fail before request handling begins."""
        key = str(name or "").strip()
        if key not in self._providers:
            raise RuntimeError(f"route provider is not bound: {key or '<empty>'}")
        return self._providers[key]

    def clear_provider(self, name: str) -> None:
        self._providers.pop(str(name or "").strip(), None)

    def providers(self) -> dict[str, object]:
        """Return a shallow snapshot suitable for route initialization diagnostics."""
        return dict(self._providers)

