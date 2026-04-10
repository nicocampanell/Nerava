import functools
import time


def ttl_cache(ttl_seconds=300):
    def deco(fn):
        cache={}
        @functools.wraps(fn)
        def wrap(*a, **k):
            key=(a, tuple(sorted(k.items())))
            now=time.time()
            if key in cache:
                v,t=cache[key]
                if now-t < ttl_seconds: return v
            v=fn(*a, **k)
            cache[key]=(v, now)
            return v
        return wrap
    return deco
