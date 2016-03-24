#!/usr/bin/env python
# -*- coding: utf-8 -*-

import functools
import random
import threading
from types import FunctionType

from six import get_function_globals

from tornado.concurrent import is_future
from tornado.gen import coroutine
import tornado.stack_context

sys_rand = random.SystemRandom()


def generate_id():
    return sys_rand.getrandbits(64)


def get_function_module(func):
    return get_function_globals(func).get('__name__')


def gen_patched_func(origin_func):
    @coroutine
    def patched_func(*args, **kwargs):
        if get_function_module(origin_func) == 'tornado.gen':
            yield tornado.stack_context.run_with_stack_context(
                tornado.stack_context.StackContext(
                    functools.partial(
                        RequestContext('span_%d' % generate_id())
                    )
                ),
                functools.partial(origin_func, *args, **kwargs))
        else:
            with tornado.stack_context.StackContext(
                    RequestContext('span_%d' % generate_id())):
                result = origin_func(*args, **kwargs)
                if is_future(result):
                    yield result

    return patched_func


class HandlerMetaclass(type):
    def __new__(cls, name, bases, attrs):
        for name, value in attrs.iteritems():
            if name in ['get', 'post'] and type(value) == FunctionType:
                value = gen_patched_func(value)
            attrs[name] = value
        return type.__new__(cls, name, bases, attrs)


def patch_handler(tornado_app):
    for _, specs in tornado_app.handlers:
        for spec in specs:
            handler_class = spec.handler_class
            patched_class = 'Patched%s' % handler_class.__name__
            # spec.handler_class = type(patched_class, (handler_class,),
            #                          {'__metaclass__': HandlerMetaclass})
            spec.handler_class = type(
                patched_class,
                (handler_class,),
                {
                    'get': gen_patched_func(handler_class.get),
                    'post': gen_patched_func(handler_class.post)
                })


class ContextLocalManager(threading.local):

    def __init__(self):
        self.current = {}


class ContextLocal(object):
    _contexts = ContextLocalManager()
    _default_instance = None

    def __init__(self):
        self._previous_instances = []

    @classmethod
    def current(cls):
        current_value = cls._contexts.current.get(cls.__name__, None)
        return current_value \
            if current_value is not None else cls._default_instance

    def __enter__(self):
        cls = type(self)
        self._previous_instances.append(
            cls._contexts.current.get(cls.__name__, None)
        )
        cls._contexts.current[cls.__name__] = self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        cls = type(self)
        cls._contexts.current[cls.__name__] = self._previous_instances.pop()

    def __call__(self):
        return self


class RequestContext(ContextLocal):

    def __init__(self, val):
        super(RequestContext, self).__init__()
        self.value = val


if __name__ == '__main__':
    from tornado.stack_context import StackContext
    with StackContext(RequestContext('foo')):
        assert RequestContext.current().value == 'foo'
