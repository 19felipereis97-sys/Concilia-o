# conftest.py — compatibilidade com pytest e runner standalone
try:
    import pytest
except ImportError:
    # Stub mínimo para não quebrar imports
    class _Pytest:
        @staticmethod
        def fixture(*a, **kw):
            def decorator(fn):
                return fn
            return decorator
        @staticmethod
        def mark(*a, **kw):
            class _M:
                @staticmethod
                def parametrize(*a, **kw):
                    def d(fn): return fn
                    return d
            return _M()
    pytest = _Pytest()
