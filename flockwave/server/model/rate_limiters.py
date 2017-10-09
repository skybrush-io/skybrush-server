"""Abstract interface specification and concrete implementations of
rate limiters that limit how frequently a given function can be called.
"""

from abc import ABCMeta, abstractmethod
from eventlet import spawn_after
from functools import wraps
from future.utils import with_metaclass
from time import time

__all__ = ("RateLimiter", "RateLimiterBase", "DummyRateLimiter",
           "DelayingRateLimiter", "rate_limited_by")


class RateLimiter(with_metaclass(ABCMeta, object)):
    """Interface specification for rate limiters that limit how frequently
    a given function or method can be called.
    """

    @abstractmethod
    def __call__(self, *args, **kwds):
        """Attempts to call the function wrapped by the rate limiter.

        The rate limiter may then decide whether a function is safe to be
        called immediately or the call should be suppressed or delayed. It
        is the responsibility of the rate limiter to schedule the function
        call at a later time if needed.

        The rate limiter may also transform the arguments passed to the
        function in an arbitrary way if needed. For instance, a rate limiter
        may batch calls to a specific function for a given amount of time,
        and then call the function once with a list containing all the
        invocations if the function supports that.
        """
        raise NotImplementedError


class RateLimiterBase(RateLimiter):
    """Base implementation of rate limiters."""

    def __init__(self, func):
        """Constructor.

        Parameters:
            func (callable): the function that the rate limiter will wrap
        """
        self._func = func

    @property
    def func(self):
        """Inherited."""
        return self._func


class DummyRateLimiter(RateLimiterBase):
    """Dummy implementation of a rate limiter that allows every function
    invocation to pass through.
    """

    def __call__(self, *args, **kwds):
        """Calls the wrapped function right now with the given positional
        and keyword arguments, and returns whatever the wrapped function
        returns.
        """
        return self._func(*args, **kwds)


class DelayingRateLimiter(RateLimiterBase):
    """Rate limiter that may delay function invocations by at most the given
    number of seconds.

    More precisely, the rate limiter works as follows. If there was no
    function invocation in the last X seconds, the function invocation is
    allowed to pass through directly. If there was at least one function
    invocation in the last X seconds, the argument of the function
    invocation are stored internally. When the grace period is over, the
    *last* stored function invocation arguments are passed to the function
    itself. Therefore, during the grace period, function invocations except
    the last one will silently be ignored.
    """

    def __init__(self, func, timeout):
        """Constructor.

        Parameters:
            func (callable): the function that the rate limiter will limit
            timeout (float): the length of the grace period after a function
                invocation when new calls will not be allowed
        """
        super(DelayingRateLimiter, self).__init__(func)
        self.timeout = max(timeout, 0)
        self._next_call_args = None
        self._mute_until = None
        self._scheduled_call = None

    def __call__(self, *args, **kwds):
        """Inherited."""
        now = time()

        self._next_call_args = self._update_next_call_args(args, kwds)

        if self._mute_until is not None:
            if self._scheduled_call is None:
                delay = self._mute_until - now
                if delay >= 0:
                    self._scheduled_call = spawn_after(delay, self._call_now)
                else:
                    self._call_now(now)
        else:
            self._call_now(now)

    def _call_now(self, now=None):
        if now is None:
            now = time()

        self._scheduled_call = None

        self._before_wrapped_function_called()
        args, kwds = self._next_call_args
        try:
            self._func(*args, **kwds)
        finally:
            self._after_wrapped_function_called()
            self._next_call_args = None

            if self.timeout > 0:
                self._mute_until = now + self.timeout

    def _after_wrapped_function_called(self):
        """Hook function that is called after the rate limiter called the
        wrapped function.
        """
        pass

    def _before_wrapped_function_called(self):
        """Hook function that is called before the rate limiter calls the
        wrapped function. This is the last place where one can adjust the
        value of ``self._next_call_args()``.
        """
        pass

    def _update_next_call_args(self, args, kwds):
        """Updates the positional and keyword arguments that will be passed
        to the next invocation of the function.

        Parameters:
            args (List[object]): list of positional arguments that the user
                used when invoking the rate limiter
            kwds (Dict): dict of keyword arguments that the user used when
                invoking the rate limiter

        Returns:
            (List[object], Dict): the positional and keyword arguments to
                pass to the next real invocation of the wrapped rate-limited
                function. This may or may not be the same as the input.
        """
        return args, kwds


class UAVSpecializedMessageRateLimiter(DelayingRateLimiter):
    """Rate limiter that is specialized for instance methods that accept a
    list of UAV identifiers as the first argument (apart from ``self``).
    The rate limiter will keep track of a set of UAV identifiers that
    were seen since the last invocation of the wrapped function; when the
    grace period is over, the collected UAV identifiers will be sent to
    the wrapped function at once.
    """

    def __init__(self, *args, **kwds):
        super(UAVSpecializedMessageRateLimiter, self).__init__(*args, **kwds)
        self._collected_uav_ids = set()
        self._self = None

    def _after_wrapped_function_called(self):
        self._collected_uav_ids.clear()
        self._self = None

    def _before_wrapped_function_called(self):
        self._next_call_args = \
            (self._self, sorted(self._collected_uav_ids)), {}

    def _update_next_call_args(self, args, kwds):
        """Inherited."""
        assert len(args) == 2, "wrapped method must have a single argument"
        assert not kwds, "wrapped method must not have keyword arguments"
        assert self._self is None or self._self == args[0], \
            "UAVSpecializedMessageRateLimiter must be called with the same "\
            "class instance"
        self._self = args[0]
        self._collected_uav_ids.update(args[1])
        return args, kwds


def rate_limited_by(rate_limiter, *args, **kwds):
    """Function that returns a decorator that decorates other functions
    or methods with the given rate limiter.

    Additional positional and keyword arguments not named here are passed
    to the ``rate_limiter`` callable.

    Parameters:
        rate_limiter (callable): a callable that returns a RateLimiter_
            instance when invoked with the function being decorated as its
            only argument. Typically a subclass of RateLimiterBase_.

    Returns:
        callable: a decorator that decorates functions with the given
            rate limiter
    """
    def decorator(func):
        limiter = rate_limiter(func, *args, **kwds)

        # We need to return a function here -- we cannot simply return the
        # limiter instance even if it is callable because that would render
        # the decorator useless in classes where the decorated object is
        # turned into an unbound class method instance (this cannot be done
        # if the decorated object is a class instance and not a function)

        @wraps(func)
        def wrapper(*wrapper_args, **wrapper_kwds):
            return limiter(*wrapper_args, **wrapper_kwds)

        return wrapper
    return decorator
