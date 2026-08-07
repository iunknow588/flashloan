from web import parameter_config as _parameter_config

__all__ = [name for name in dir(_parameter_config) if not name.startswith("_")]

globals().update({name: getattr(_parameter_config, name) for name in __all__})
